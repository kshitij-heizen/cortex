import asyncio
import logging
import time

from botocore.exceptions import ClientError

from api.models import AddonInstallResult, AddonInstallStatus
from api.services.addon_installer import AddonInstallerService


logger = logging.getLogger(__name__)


class DestroyManager(AddonInstallerService):
    """Handles pre-destroy cleanup and orchestrates the full destroy flow."""

    def _build_pre_destroy_script(self) -> str:
        cluster_name = self.outputs.get("eks_cluster_name")
        region = self.config.aws_config.region

        return f"""#!/bin/bash
set -o pipefail

export PATH="/usr/local/bin:$PATH"
export HOME="${{HOME:-/root}}"
mkdir -p "$HOME/.kube"
export KUBECONFIG="$HOME/.kube/config"

CLUSTER_NAME="{cluster_name}"
REGION="{region}"
CUSTOMER_ID="{self.customer_id}"

echo "==> Configuring kubectl for $CLUSTER_NAME in $REGION..."
aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$REGION"

echo "==> Starting pre-destroy cleanup for $CUSTOMER_ID..."

echo "==> Deleting root ArgoCD application..."
kubectl delete application $CUSTOMER_ID-root-app -n argocd --ignore-not-found=true --timeout=60s 2>/dev/null || true
sleep 5

echo "==> Deleting all ArgoCD applications..."
kubectl delete applications --all -n argocd --timeout=120s 2>/dev/null || true

echo "==> Waiting 30s for load balancer cleanup..."
sleep 30

echo "==> Checking for remaining LoadBalancer services..."
LB_SVCS=$(kubectl get svc -A -o json 2>/dev/null | jq -r '.items[] | select(.spec.type=="LoadBalancer") | .metadata.namespace + "/" + .metadata.name' || echo "")
if [ -n "$LB_SVCS" ]; then
    echo "==> Deleting remaining LoadBalancer services..."
    for svc in $LB_SVCS; do
        NS=$(echo "$svc" | cut -d/ -f1)
        NAME=$(echo "$svc" | cut -d/ -f2)
        echo "    Deleting $NS/$NAME..."
        kubectl delete svc "$NAME" -n "$NS" --timeout=60s 2>/dev/null || true
    done
    echo "==> Waiting 30s for LB deletion..."
    sleep 30
fi

echo "==> Deleting Karpenter nodepools..."
kubectl delete nodepools --all --timeout=60s 2>/dev/null || true
echo "==> Deleting Karpenter EC2NodeClasses..."
kubectl delete ec2nodeclasses --all --timeout=60s 2>/dev/null || true

echo "==> Waiting for Karpenter nodes to terminate..."
TIMEOUT=300
ELAPSED=0
while [ $ELAPSED -lt $TIMEOUT ]; do
    NODE_COUNT=$(kubectl get nodes --no-headers 2>/dev/null | wc -l)
    BOOTSTRAP_COUNT=$(kubectl get nodes -l node-role=system --no-headers 2>/dev/null | wc -l)
    KARPENTER_NODES=$((NODE_COUNT - BOOTSTRAP_COUNT))
    if [ "$KARPENTER_NODES" -le 0 ] 2>/dev/null; then
        echo "==> All Karpenter nodes terminated!"
        break
    fi
    echo "    $KARPENTER_NODES Karpenter nodes remaining, waiting..."
    sleep 15
    ELAPSED=$((ELAPSED + 15))
done

if [ $ELAPSED -ge $TIMEOUT ]; then
    echo "==> WARNING: Timeout waiting for Karpenter nodes. Proceeding anyway."
fi

echo "==> Cleaning up application namespaces..."
for ns in cortex-app cortex-ingestion nextjs falkordb-$CUSTOMER_ID falkordb-shared falkordb-dashboard milvus-$CUSTOMER_ID vector clickhouse monitoring nginx-inc; do
    kubectl delete namespace "$ns" --ignore-not-found=true --timeout=60s 2>/dev/null || true
done

echo "==> Remaining nodes:"
kubectl get nodes 2>/dev/null || true

echo ""
echo "==> Pre-destroy cleanup complete!"
"""

    def _run_pre_destroy_sync(self) -> AddonInstallResult:
        instance_id = self.outputs.get("access_node_instance_id")
        cluster_name = self.outputs.get("eks_cluster_name")

        if not instance_id:
            raise ValueError("SSM access node is not available in deployment outputs")
        if not cluster_name:
            raise ValueError("EKS cluster name not found in deployment outputs")

        script = self._build_pre_destroy_script()
        ssm = self._get_client("ssm")

        response = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": script.split("\n")},
            TimeoutSeconds=600,
            Comment=f"Pre-destroy cleanup for {self.customer_id}-{self.environment}",
        )

        command_id = response["Command"]["CommandId"]
        self._save_addon_state("pre-destroy", command_id, instance_id)

        waiter_timeout = 600
        poll_interval = 10
        elapsed = 0

        while elapsed < waiter_timeout:
            time.sleep(poll_interval)
            elapsed += poll_interval

            try:
                invocation = ssm.get_command_invocation(
                    CommandId=command_id,
                    InstanceId=instance_id,
                )
            except ClientError as e:
                if e.response["Error"]["Code"] == "InvocationDoesNotExist":
                    continue
                raise

            ssm_status = invocation.get("Status", "")

            if ssm_status == "Success":
                return AddonInstallResult(
                    addon_name="pre-destroy",
                    status=AddonInstallStatus.SUCCEEDED,
                    ssm_command_id=command_id,
                    instance_id=instance_id,
                    output=invocation.get("StandardOutputContent"),
                )
            elif ssm_status in ("Failed", "TimedOut", "Cancelled"):
                return AddonInstallResult(
                    addon_name="pre-destroy",
                    status=AddonInstallStatus.FAILED,
                    ssm_command_id=command_id,
                    instance_id=instance_id,
                    output=invocation.get("StandardOutputContent"),
                    error=invocation.get("StandardErrorContent"),
                )

        return AddonInstallResult(
            addon_name="pre-destroy",
            status=AddonInstallStatus.FAILED,
            ssm_command_id=command_id,
            instance_id=instance_id,
            error="Timed out waiting for pre-destroy cleanup to complete",
        )

    async def run_pre_destroy(self) -> AddonInstallResult:
        return await asyncio.to_thread(self._run_pre_destroy_sync)

