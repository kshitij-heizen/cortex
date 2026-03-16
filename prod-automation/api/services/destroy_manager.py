import asyncio
import logging
import time

from botocore.exceptions import ClientError

from api.models import AddonInstallResult, AddonInstallStatus
from api.services.addon_installer import AddonInstallerService


logger = logging.getLogger(__name__)


class DestroyManager(AddonInstallerService):
    """Handles pre-destroy cleanup before Pulumi destroy.

    Inherits from AddonInstallerService to reuse SSM client, config,
    deployment outputs, and state management.
    """

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

# =============================================================================
# PHASE 1: Delete ArgoCD Applications (stops all workloads)
# =============================================================================
echo "==> Phase 1: Deleting ArgoCD applications..."
if kubectl get namespace argocd &>/dev/null; then
    kubectl delete applications --all -n argocd --timeout=300s || true
    echo "==> Waiting for application resources to terminate..."
    sleep 30
    for app in $(kubectl get applications -n argocd -o name 2>/dev/null); do
        kubectl patch $app -n argocd --type merge -p '{{"metadata":{{"finalizers":null}}}}' 2>/dev/null || true
    done
    kubectl delete applications --all -n argocd --timeout=60s 2>/dev/null || true
fi
echo "==> ArgoCD applications deleted!"

# =============================================================================
# PHASE 2: Delete LoadBalancer services (NLBs from NGINX ingress)
# =============================================================================
echo "==> Phase 2: Deleting LoadBalancer services..."
kubectl delete svc -n nginx-inc --all --timeout=120s 2>/dev/null || true

LB_SVCS=$(kubectl get svc -A -o json 2>/dev/null | jq -r '.items[] | select(.spec.type=="LoadBalancer") | .metadata.namespace + "/" + .metadata.name' || echo "")
if [ -n "$LB_SVCS" ]; then
    for svc in $LB_SVCS; do
        NS=$(echo "$svc" | cut -d/ -f1)
        NAME=$(echo "$svc" | cut -d/ -f2)
        echo "    Deleting $NS/$NAME..."
        kubectl delete svc "$NAME" -n "$NS" --timeout=60s 2>/dev/null || true
    done
fi
echo "==> Waiting for NLBs to deregister (3 min)..."
sleep 180

# =============================================================================
# PHASE 3: Delete Karpenter nodes
# =============================================================================
echo "==> Phase 3: Deleting Karpenter managed nodes..."
kubectl delete nodepools --all --timeout=120s 2>/dev/null || true
kubectl delete ec2nodeclasses --all --timeout=120s 2>/dev/null || true
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

# =============================================================================
# PHASE 4: Delete CRD instances (before removing operators)
# =============================================================================
echo "==> Phase 4: Deleting CRD instances..."
kubectl delete clusters.apps.kubeblocks.io --all -A --timeout=120s 2>/dev/null || true
kubectl delete milvus --all -A --timeout=120s 2>/dev/null || true
kubectl delete clickhouseinstallations --all -A --timeout=120s 2>/dev/null || true
kubectl delete clusterissuers --all --timeout=60s 2>/dev/null || true
kubectl delete clustersecretstores --all --timeout=60s 2>/dev/null || true
kubectl delete externalsecrets --all -A --timeout=60s 2>/dev/null || true
echo "==> Waiting for CRD instances to terminate..."
sleep 60

# =============================================================================
# PHASE 5: Uninstall Helm releases (reverse order)
# =============================================================================
echo "==> Phase 5: Uninstalling Helm releases..."
helm uninstall argocd -n argocd 2>/dev/null || true
helm uninstall external-secrets -n external-secrets 2>/dev/null || true
helm uninstall cert-manager -n cert-manager 2>/dev/null || true
helm uninstall monitoring -n monitoring 2>/dev/null || true
helm uninstall clickhouse-operator -n clickhouse 2>/dev/null || true
helm uninstall milvus-operator -n milvus-operator 2>/dev/null || true
helm uninstall kb-addon-falkordb -n kubeblocks 2>/dev/null || true
helm uninstall kubeblocks -n kubeblocks 2>/dev/null || true
helm uninstall karpenter -n karpenter 2>/dev/null || true

# =============================================================================
# PHASE 6: Clean up namespaces
# =============================================================================
echo "==> Phase 6: Cleaning up namespaces..."
for ns in argocd external-secrets cert-manager monitoring clickhouse milvus-operator kubeblocks karpenter nginx-inc falkordb-shared falkordb-$CUSTOMER_ID milvus-$CUSTOMER_ID cortex-app cortex-ingestion nextjs vector falkordb-dashboard; do
    kubectl delete namespace "$ns" --timeout=60s 2>/dev/null || true
done

for ns in $(kubectl get namespaces -o jsonpath='{{range .items[?(@.status.phase=="Terminating")]}}{{.metadata.name}} {{end}}'); do
    echo "==> Removing finalizers from stuck namespace: $ns"
    kubectl get namespace $ns -o json | jq '.spec.finalizers = []' | kubectl replace --raw "/api/v1/namespaces/$ns/finalize" -f - 2>/dev/null || true
done

# =============================================================================
# PHASE 7: Final verification
# =============================================================================
echo "==> Phase 7: Verifying cleanup..."
echo "Remaining pods:"
kubectl get pods -A 2>/dev/null || true
echo ""
echo "Remaining LoadBalancer services:"
kubectl get svc -A --field-selector spec.type=LoadBalancer 2>/dev/null || true
echo ""
echo "Remaining PVCs:"
kubectl get pvc -A 2>/dev/null || true
echo ""
echo "==> Pre-destroy cleanup complete! Safe to run pulumi destroy."
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
            TimeoutSeconds=900,
            Comment=f"Pre-destroy cleanup for {self.customer_id}-{self.environment}",
        )

        command_id = response["Command"]["CommandId"]
        self._save_addon_state("pre-destroy", command_id, instance_id)

        waiter_timeout = 900
        poll_interval = 15
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

