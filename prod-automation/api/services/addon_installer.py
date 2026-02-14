import asyncio
import base64
import json
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

from api.config_storage import config_storage
from api.database import db
from api.models import (
    AddonInstallResult,
    AddonInstallStatus,
    ArgoCDAddonResolved,
    KarpenterConfigResolved,
)
from api.settings import settings


class AddonInstallerService:
    """Install cluster addons by executing scripts on the SSM access node.

    Installs Karpenter and ArgoCD in a single combined script to ensure
    proper ordering (Karpenter must be ready before ArgoCD deploys workloads).
    """

    def __init__(self, customer_id: str, environment: str):
        self.customer_id = customer_id
        self.environment = environment
        self._config = None
        self._deployment = None
        self._clients: dict = {}

    @property
    def config(self):
        if not self._config:
            self._config = config_storage.get(self.customer_id)
            if not self._config:
                raise ValueError(f"Customer {self.customer_id} not found")
        return self._config

    @property
    def deployment(self):
        if not self._deployment:
            self._deployment = db.get_deployment(self.customer_id, self.environment)
            if not self._deployment:
                raise ValueError(
                    f"Deployment {self.customer_id}-{self.environment} not found"
                )
        return self._deployment

    @property
    def outputs(self) -> dict:
        if not self.deployment.outputs:
            return {}
        try:
            return json.loads(self.deployment.outputs)
        except (json.JSONDecodeError, TypeError):
            return {}

    def _get_client(self, service: str):
        """Get boto3 client with assumed role credentials."""
        if service in self._clients:
            return self._clients[service]

        try:
            sts = boto3.client("sts", region_name=self.config.aws_config.region)
            assumed = sts.assume_role(
                RoleArn=self.config.aws_config.role_arn,
                ExternalId=self.config.aws_config.external_id,
                RoleSessionName=f"byoc-addon-{self.customer_id}",
                DurationSeconds=900,
            )
        except NoCredentialsError as e:
            raise ValueError(
                f"Failed to locate AWS credentials: {e}. "
                "Use env vars, IAM role (EC2/IRSA), or other default provider chain."
            ) from e
        except ClientError as e:
            raise ValueError(
                f"Failed to assume role {self.config.aws_config.role_arn}: {e}"
            ) from e

        creds = assumed["Credentials"]

        client = boto3.client(
            service,
            region_name=self.config.aws_config.region,
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
        )
        self._clients[service] = client
        return client

    @staticmethod
    def _argocd_repo_password_param_name(customer_id: str, environment: str) -> str:
        """SSM Parameter Store path for ArgoCD repo password."""
        return f"/byoc/{customer_id}/{environment}/argocd/repo-password"

    def _state_path(self) -> Path:
        return Path(settings.config_storage_path) / f"{self.customer_id}.addons.json"

    def _save_addon_state(
        self, addon_name: str, command_id: str, instance_id: str
    ) -> None:
        """Persist the last SSM command ID for an addon install."""
        path = self._state_path()
        state: dict = {}
        if path.exists():
            state = json.loads(path.read_text())
        state[addon_name] = {
            "last_command_id": command_id,
            "instance_id": instance_id,
            "triggered_at": datetime.now(timezone.utc).isoformat(),
        }
        path.write_text(json.dumps(state, indent=2))

    def _load_addon_state(self, addon_name: str) -> dict | None:
        """Load the last known SSM command state for an addon."""
        path = self._state_path()
        if not path.exists():
            return None
        state = json.loads(path.read_text())
        return state.get(addon_name)

    def _get_karpenter_instance_types(self, karpenter_config: KarpenterConfigResolved) -> list[str]:
        """Generate list of instance types from families and sizes."""
        instance_types = []
        for family in karpenter_config.node_pool.instance_families:
            for size in karpenter_config.node_pool.instance_sizes:
                instance_types.append(f"{family}.{size}")
        return instance_types

    def _build_karpenter_install_script(
        self,
        cluster_name: str,
        region: str,
        karpenter_config: KarpenterConfigResolved,
        karpenter_role_arn: str,
        node_role_name: str,
    ) -> str:
        """Build the shell script to install Karpenter via Helm."""
        instance_types = self._get_karpenter_instance_types(karpenter_config)
        capacity_types_json = json.dumps(karpenter_config.node_pool.capacity_types)
        instance_types_json = json.dumps(instance_types)
        architectures_json = json.dumps(karpenter_config.node_pool.architectures)

        return f"""
# =============================================================================
# KARPENTER INSTALLATION
# =============================================================================
echo "==> Installing Karpenter {karpenter_config.version}..."

# Install Karpenter Helm chart
helm registry logout public.ecr.aws || true
helm upgrade --install karpenter oci://public.ecr.aws/karpenter/karpenter \\
    --version "{karpenter_config.version}" \\
    --namespace karpenter --create-namespace \\
    --set "settings.clusterName=$CLUSTER_NAME" \\
    --set "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn={karpenter_role_arn}" \\
    --set "controller.resources.requests.cpu=0.5" \\
    --set "controller.resources.requests.memory=512Mi" \\
    --set "controller.resources.limits.cpu=1" \\
    --set "controller.resources.limits.memory=1Gi" \\
    --wait --timeout 5m

echo "==> Waiting for Karpenter controller to be ready..."
kubectl wait --for=condition=available --timeout=300s deployment/karpenter -n karpenter

echo "==> Creating EC2NodeClass..."
cat <<'EC2NODECLASS_EOF' | kubectl apply -f -
apiVersion: karpenter.k8s.aws/v1
kind: EC2NodeClass
metadata:
  name: default
spec:
  amiSelectorTerms:
    - alias: al2023@latest
  subnetSelectorTerms:
    - tags:
        karpenter.sh/discovery: "{cluster_name}"
  securityGroupSelectorTerms:
    - tags:
        karpenter.sh/discovery: "{cluster_name}"
  role: "{node_role_name}"
  tags:
    karpenter.sh/discovery: "{cluster_name}"
    Name: "{cluster_name}-karpenter-node"
EC2NODECLASS_EOF

echo "==> Creating NodePool..."
cat <<'NODEPOOL_EOF' | kubectl apply -f -
apiVersion: karpenter.sh/v1
kind: NodePool
metadata:
  name: default
spec:
  template:
    spec:
      nodeClassRef:
        group: karpenter.k8s.aws
        kind: EC2NodeClass
        name: default
      requirements:
        - key: karpenter.sh/capacity-type
          operator: In
          values: {capacity_types_json}
        - key: node.kubernetes.io/instance-type
          operator: In
          values: {instance_types_json}
        - key: kubernetes.io/arch
          operator: In
          values: {architectures_json}
  limits:
    cpu: {karpenter_config.node_pool.cpu_limit}
    memory: {karpenter_config.node_pool.memory_limit_gb}Gi
  disruption:
    consolidationPolicy: {karpenter_config.disruption.consolidation_policy}
    consolidateAfter: {karpenter_config.disruption.consolidate_after_seconds}s
NODEPOOL_EOF

echo "==> Verifying Karpenter installation..."
kubectl get pods -n karpenter
kubectl get ec2nodeclasses
kubectl get nodepools

echo "==> Karpenter installation complete!"
"""

    def _build_storage_and_nodepools_script(self, cluster_name: str) -> str:
        """Build script to create gp3 StorageClass and Karpenter NodePools."""
        return f"""
# =============================================================================
# STORAGE CLASS (gp3)
# =============================================================================
echo "==> Creating gp3 StorageClass..."

cat <<'STORAGECLASS_EOF' | kubectl apply -f -
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: gp3
provisioner: ebs.csi.aws.com
parameters:
  type: gp3
  fsType: ext4
  encrypted: "true"
volumeBindingMode: WaitForFirstConsumer
allowVolumeExpansion: true
STORAGECLASS_EOF

echo "==> Verifying StorageClass..."
kubectl get storageclass gp3

# =============================================================================
# KARPENTER NODEPOOLS
# =============================================================================
echo "==> Creating Karpenter NodePools..."

cat <<'NODEPOOL_EOF' | kubectl apply -f -
apiVersion: karpenter.sh/v1
kind: NodePool
metadata:
  name: general-pool
spec:
  template:
    metadata:
      labels:
        role: general
    spec:
      nodeClassRef:
        group: karpenter.k8s.aws
        kind: EC2NodeClass
        name: default
      requirements:
        - key: node.kubernetes.io/instance-type
          operator: In
          values: ["m6i.medium", "m6i.large", "m6i.xlarge", "m6a.medium", "m6a.large", "m6a.xlarge", "m7i.medium", "m7i.large"]
        - key: karpenter.sh/capacity-type
          operator: In
          values: ["on-demand"]
  limits:
    cpu: 100
  disruption:
    consolidationPolicy: WhenEmptyOrUnderutilized
    consolidateAfter: 5m
---
apiVersion: karpenter.sh/v1
kind: NodePool
metadata:
  name: memory-pool-xlarge
spec:
  template:
    metadata:
      labels:
        role: memory-db-large-scalable
    spec:
      nodeClassRef:
        group: karpenter.k8s.aws
        kind: EC2NodeClass
        name: default
      requirements:
        - key: node.kubernetes.io/instance-type
          operator: In
          values: ["r6i.large", "r6i.xlarge", "r6i.2xlarge", "r6a.large", "r6a.xlarge", "r6a.2xlarge"]
        - key: karpenter.sh/capacity-type
          operator: In
          values: ["on-demand"]
      taints:
        - key: workload
          value: database-large-scalable
          effect: NoSchedule
  limits:
    cpu: 100
  disruption:
    consolidationPolicy: WhenEmpty
    consolidateAfter: 30m
    budgets:
      - nodes: "0"
        reasons:
          - Drifted
          - Underutilized
NODEPOOL_EOF

echo "==> Verifying NodePools..."
kubectl get nodepools

# =============================================================================
# KUBEBLOCKS INSTALLATION
# =============================================================================
echo "==> Installing KubeBlocks..."
helm repo add kubeblocks https://apecloud.github.io/helm-charts
helm repo update

# Install CRDs first from GitHub - USE v1.0.0
echo "==> Installing KubeBlocks CRDs..."
kubectl apply -f https://github.com/apecloud/kubeblocks/releases/download/v1.0.0/kubeblocks_crds.yaml --server-side

# Wait for CRDs to be established
echo "==> Waiting for CRDs to be ready..."
sleep 10
kubectl wait --for=condition=Established crd --all --timeout=120s

# Now install KubeBlocks - USE v1.0.0
echo "==> Installing KubeBlocks operator..."
helm upgrade --install kubeblocks kubeblocks/kubeblocks \
    --namespace kubeblocks --create-namespace \
    --version "1.0.0" \
    --wait --timeout 10m

echo "==> Verifying KubeBlocks..."
kubectl get pods -n kubeblocks
kubectl get crd | grep kubeblocks | head -5

echo "==> KubeBlocks installation complete!"

# =============================================================================
# VOLUMESNAPSHOT CRDS (required by KubeBlocks dataprotection)
# =============================================================================
echo "==> Installing VolumeSnapshot CRDs..."
kubectl apply -f https://raw.githubusercontent.com/kubernetes-csi/external-snapshotter/master/client/config/crd/snapshot.storage.k8s.io_volumesnapshotclasses.yaml
kubectl apply -f https://raw.githubusercontent.com/kubernetes-csi/external-snapshotter/master/client/config/crd/snapshot.storage.k8s.io_volumesnapshotcontents.yaml
kubectl apply -f https://raw.githubusercontent.com/kubernetes-csi/external-snapshotter/master/client/config/crd/snapshot.storage.k8s.io_volumesnapshots.yaml

# =============================================================================
# FALKORDB ADDON (manual Helm install - jihulab.com unreachable from in-cluster)
# =============================================================================
echo "==> Installing FalkorDB addon..."
FALKORDB_CHART_URL="https://jihulab.com/api/v4/projects/150246/packages/helm/stable/charts/falkordb-1.0.1.tgz"
curl -L --retry 3 --connect-timeout 30 "$FALKORDB_CHART_URL" -o /tmp/falkordb.tgz

if helm status kb-addon-falkordb -n kubeblocks &>/dev/null; then
    echo "==> FalkorDB addon already installed, upgrading..."
    helm upgrade kb-addon-falkordb /tmp/falkordb.tgz -n kubeblocks
else
    helm install kb-addon-falkordb /tmp/falkordb.tgz -n kubeblocks
fi

echo "==> Waiting for FalkorDB ClusterDefinition..."
timeout=120
elapsed=0
while [ $elapsed -lt $timeout ]; do
    if kubectl get clusterdefinitions.apps.kubeblocks.io falkordb &>/dev/null; then
        echo "==> FalkorDB ClusterDefinition is ready!"
        break
    fi
    sleep 5
    elapsed=$((elapsed + 5))
done

kubectl get clusterdefinitions.apps.kubeblocks.io | grep falkordb
echo "==> FalkorDB addon installation complete!"

# =============================================================================
# MILVUS OPERATOR
# =============================================================================
echo "==> Installing Milvus Operator..."

helm repo add milvus-operator https://zilliztech.github.io/milvus-operator
helm repo update

if helm status milvus-operator -n milvus-operator &>/dev/null; then
    echo "==> Milvus Operator already installed, upgrading..."
    helm upgrade milvus-operator milvus-operator/milvus-operator -n milvus-operator --wait --timeout 5m
else
    helm install milvus-operator milvus-operator/milvus-operator -n milvus-operator --create-namespace --wait --timeout 5m
fi

echo "==> Waiting for Milvus CRDs..."
timeout=120
elapsed=0
while [ $elapsed -lt $timeout ]; do
    if kubectl get crd milvuses.milvus.io &>/dev/null; then
        echo "==> Milvus CRDs are ready!"
        break
    fi
    sleep 5
    elapsed=$((elapsed + 5))
done
kubectl get pods -n milvus-operator
echo "==> Milvus Operator installation complete!"

"""


    def _build_monitoring_and_data_pipeline_script(self, cluster_name: str) -> str:
        """Build script to install monitoring stack, ClickHouse operator, and Vector prerequisites."""
        return f"""
# =============================================================================
# CLICKHOUSE STORAGE CLASS
# =============================================================================
echo "==> Creating ClickHouse StorageClass..."

cat <<'CH_SC_EOF' | kubectl apply -f -
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: clickhouse-storage
  annotations:
    storageclass.kubernetes.io/is-default-class: "false"
provisioner: ebs.csi.aws.com
parameters:
  type: gp3
  iops: "3000"
  throughput: "125"
  fsType: ext4
  encrypted: "true"
allowVolumeExpansion: true
volumeBindingMode: WaitForFirstConsumer
reclaimPolicy: Retain
CH_SC_EOF

echo "==> ClickHouse StorageClass created!"

# =============================================================================
# CLICKHOUSE KARPENTER NODEPOOL
# =============================================================================
echo "==> Creating ClickHouse Karpenter NodePool..."

cat <<'CH_NP_EOF' | kubectl apply -f -
apiVersion: karpenter.sh/v1
kind: NodePool
metadata:
  name: clickhouse-pool
spec:
  template:
    metadata:
      labels:
        role: clickhouse
    spec:
      nodeClassRef:
        group: karpenter.k8s.aws
        kind: EC2NodeClass
        name: default
      requirements:
        - key: node.kubernetes.io/instance-type
          operator: In
          values: ["c7i.large", "c7i.xlarge", "c6i.large", "c6i.xlarge"]
        - key: karpenter.sh/capacity-type
          operator: In
          values: ["on-demand"]
      taints:
        - key: workload
          value: clickhouse
          effect: NoSchedule
  limits:
    cpu: 100
  disruption:
    consolidationPolicy: WhenEmpty
    consolidateAfter: 30m
    budgets:
      - nodes: "0"
        reasons:
          - Drifted
          - Underutilized
CH_NP_EOF

echo "==> ClickHouse NodePool created!"

# =============================================================================
# ALTINITY CLICKHOUSE OPERATOR
# =============================================================================
echo "==> Installing Altinity ClickHouse Operator..."

helm repo add altinity https://helm.altinity.com
helm repo update altinity

if helm status clickhouse-operator -n clickhouse &>/dev/null; then
    echo "==> ClickHouse Operator already installed, upgrading..."
    helm upgrade clickhouse-operator altinity/altinity-clickhouse-operator \\
        --version 0.25.5 --namespace clickhouse --wait --timeout 5m
else
    helm install clickhouse-operator altinity/altinity-clickhouse-operator \\
        --version 0.25.5 --namespace clickhouse --create-namespace --wait --timeout 5m
fi

echo "==> Waiting for ClickHouse Operator CRDs..."
TIMEOUT=120
ELAPSED=0
while [ $ELAPSED -lt $TIMEOUT ]; do
    if kubectl get crd clickhouseinstallations.clickhouse.altinity.com &>/dev/null; then
        echo "==> ClickHouse CRDs are ready!"
        break
    fi
    sleep 5
    ELAPSED=$((ELAPSED + 5))
done
kubectl get pods -n clickhouse
echo "==> ClickHouse Operator installation complete!"

# =============================================================================
# MONITORING STACK (kube-prometheus-stack)
# =============================================================================
echo "==> Installing kube-prometheus-stack..."

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

if helm status monitoring -n monitoring &>/dev/null; then
    echo "==> Monitoring stack already installed, upgrading..."
    helm upgrade monitoring prometheus-community/kube-prometheus-stack \\
        --namespace monitoring \\
        --version 65.1.1 \\
        --set prometheus.prometheusSpec.nodeSelector.role=general \\
        --set grafana.nodeSelector.role=general \\
        --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false \\
        --set prometheus.prometheusSpec.podMonitorSelectorNilUsesHelmValues=false \\
        --set prometheus.prometheusSpec.retention=10d \\
        --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.storageClassName=gp2 \\
        --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.resources.requests.storage=20Gi \\
        --set grafana.persistence.enabled=true \\
        --set grafana.persistence.storageClassName=gp2 \\
        --set grafana.persistence.size=2Gi \\
        --set grafana.adminPassword="Prod_Grafana_Pass123!" \\
        --set alertmanager.enabled=false \\
        --set nodeExporter.enabled=true \\
        --wait --timeout 10m
else
    helm install monitoring prometheus-community/kube-prometheus-stack \\
        --namespace monitoring --create-namespace \\
        --version 65.1.1 \\
        --set prometheus.prometheusSpec.nodeSelector.role=general \\
        --set grafana.nodeSelector.role=general \\
        --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false \\
        --set prometheus.prometheusSpec.podMonitorSelectorNilUsesHelmValues=false \\
        --set prometheus.prometheusSpec.retention=10d \\
        --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.storageClassName=gp2 \\
        --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.resources.requests.storage=20Gi \\
        --set grafana.persistence.enabled=true \\
        --set grafana.persistence.storageClassName=gp2 \\
        --set grafana.persistence.size=2Gi \\
        --set grafana.adminPassword="Prod_Grafana_Pass123!" \\
        --set alertmanager.enabled=false \\
        --set nodeExporter.enabled=true \\
        --wait --timeout 10m
fi

echo "==> Waiting for monitoring pods..."
kubectl wait --for=condition=available --timeout=300s deployment/monitoring-grafana -n monitoring || true
kubectl wait --for=condition=available --timeout=300s deployment/monitoring-kube-state-metrics -n monitoring || true

echo "==> Monitoring pods:"
kubectl get pods -n monitoring
echo "==> Monitoring stack installation complete!"
"""


    def _build_argocd_install_script(
        self,
        argocd_config: ArgoCDAddonResolved,
        ssm_repo_password_param: str | None = None,
    ) -> str:
        """Build the shell script to install ArgoCD via Helm."""
        controller_replicas = 2 if argocd_config.ha_enabled else 1

        lines = [
            "",
            "# =============================================================================",
            "# ARGOCD INSTALLATION",
            "# =============================================================================",
            f'echo "==> Installing ArgoCD (chart version {argocd_config.chart_version})..."',
            "",
            "# Add ArgoCD Helm repo",
            "helm repo add argo https://argoproj.github.io/argo-helm",
            "helm repo update",
            "",
            "# Install / upgrade ArgoCD",
            "helm upgrade --install argocd argo/argo-cd \\",
            "  --namespace argocd --create-namespace \\",
            f"  --version {argocd_config.chart_version} \\",
            f"  --set server.replicas={argocd_config.server_replicas} \\",
            f"  --set repoServer.replicas={argocd_config.repo_server_replicas} \\",
            f"  --set 'redis-ha.enabled={'true' if argocd_config.ha_enabled else 'false'}' \\",
            f"  --set controller.replicas={controller_replicas} \\",
            "  --wait --timeout 5m",
            "",
        ]

        if argocd_config.repository:
            repo = argocd_config.repository
            type_b64 = base64.b64encode(b"git").decode()
            url_b64 = base64.b64encode(repo.url.encode()).decode()
            user_b64 = base64.b64encode(repo.username.encode()).decode()

            lines.append("# Create repository credentials secret")
            lines.append('echo "==> Creating ArgoCD repository credentials..."')
            if ssm_repo_password_param:
                lines.extend(
                    [
                        "# Fetch password from SSM at runtime (never inlined in the script)",
                        f'REPO_PASSWORD=$(aws ssm get-parameter --name "{ssm_repo_password_param}" --with-decryption --region "$REGION" --query Parameter.Value --output text)',
                        'PASS_B64=$(echo -n "$REPO_PASSWORD" | base64 -w0)',
                        "unset REPO_PASSWORD",
                        f'TYPE_B64="{type_b64}"',
                        f'URL_B64="{url_b64}"',
                        f'USER_B64="{user_b64}"',
                        "kubectl apply -f - <<REPO_CREDS_EOF",
                        "apiVersion: v1",
                        "kind: Secret",
                        "metadata:",
                        "  name: repo-creds",
                        "  namespace: argocd",
                        "  labels:",
                        "    argocd.argoproj.io/secret-type: repo-creds",
                        "data:",
                        "  type: $TYPE_B64",
                        "  url: $URL_B64",
                        "  username: $USER_B64",
                        "  password: $PASS_B64",
                        "REPO_CREDS_EOF",
                        "",
                    ]
                )
            else:
                lines.extend(
                    [
                        "kubectl apply -f - <<'REPO_CREDS_EOF'",
                        "apiVersion: v1",
                        "kind: Secret",
                        "metadata:",
                        "  name: repo-creds",
                        "  namespace: argocd",
                        "  labels:",
                        "    argocd.argoproj.io/secret-type: repo-creds",
                        "data:",
                        f"  type: {type_b64}",
                        f"  url: {url_b64}",
                        f"  username: {user_b64}",
                        "REPO_CREDS_EOF",
                        "",
                    ]
                )

            # Create root application if path is configured
            if argocd_config.root_app_path:
                lines.extend(
                    [
                        "# Create root ArgoCD Application",
                        'echo "==> Creating root ArgoCD Application..."',
                        "kubectl apply -f - <<'ROOT_APP_EOF'",
                        "apiVersion: argoproj.io/v1alpha1",
                        "kind: Application",
                        "metadata:",
                        f"  name: {self.customer_id}-root-app",
                        "  namespace: argocd",
                        "spec:",
                        "  project: default",
                        "  source:",
                        f"    repoURL: {repo.url}",
                        "    targetRevision: HEAD",
                        f"    path: {argocd_config.root_app_path}",
                        "    directory:",
                        "      recurse: true",
                        "  destination:",
                        "    server: https://kubernetes.default.svc",
                        "    namespace: argocd",
                        "  syncPolicy:",
                        "    automated:",
                        "      prune: true",
                        "      selfHeal: true",
                        "ROOT_APP_EOF",
                        "",
                    ]
                )

        lines.extend(
            [
                "# Verify ArgoCD installation",
                'echo "==> Verifying ArgoCD installation..."',
                "kubectl get pods -n argocd",
                "kubectl get svc -n argocd",
                "",
                'echo "==> ArgoCD installation complete!"',
            ]
        )

        return "\n".join(lines)

    def _build_combined_install_script(
        self,
        cluster_name: str,
        region: str,
        karpenter_config: KarpenterConfigResolved,
        karpenter_role_arn: str,
        node_role_name: str,
        argocd_config: ArgoCDAddonResolved | None,
        ssm_repo_password_param: str | None = None,
    ) -> str:
        """Build combined script that installs Karpenter then ArgoCD."""
        script_parts = [
            "#!/bin/bash",
            "set -euo pipefail",
            "",
            'export PATH="/usr/local/bin:$PATH"',
            '# SSM Run Command may not set HOME; use default so KUBECONFIG is predictable',
            'export HOME="${HOME:-/root}"',
            'mkdir -p "$HOME/.kube"',
            'export KUBECONFIG="$HOME/.kube/config"',
            "",
            f'CLUSTER_NAME="{cluster_name}"',
            f'REGION="{region}"',
            "",
            "# Configure kubectl",
            'echo "==> Configuring kubectl for $CLUSTER_NAME in $REGION..."',
            'aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$REGION"',
            'echo "==> Verifying cluster access..."',
            "kubectl get nodes",
            "",
        ]

        # Add Karpenter installation
        script_parts.append(
            self._build_karpenter_install_script(
                cluster_name=cluster_name,
                region=region,
                karpenter_config=karpenter_config,
                karpenter_role_arn=karpenter_role_arn,
                node_role_name=node_role_name,
            )
        )

        script_parts.append(self._build_storage_and_nodepools_script(cluster_name))

        script_parts.append(
            self._build_monitoring_and_data_pipeline_script(cluster_name)
        )

        # Add ArgoCD installation if enabled
        if argocd_config and argocd_config.enabled:
            script_parts.append(
                self._build_argocd_install_script(
                    argocd_config=argocd_config,
                    ssm_repo_password_param=ssm_repo_password_param,
                )
            )

        # Final summary
        script_parts.extend(
            [
                "",
                "# =============================================================================",
                "# INSTALLATION COMPLETE",
                "# =============================================================================",
                'echo ""',
                'echo "==> All addons installed successfully!"',
                'echo "    - Karpenter: Ready to provision nodes"',
            ]
        )

        if argocd_config and argocd_config.enabled:
            script_parts.append('echo "    - ArgoCD: Ready to sync applications"')

        script_parts.extend(
            [
                'echo ""',
                "kubectl get nodes",
                'echo ""',
            ]
        )

        return "\n".join(script_parts)

    def _install_all_addons_sync(self) -> AddonInstallResult:
        """Install all addons via SSM Run Command (blocking)."""
        instance_id = self.outputs.get("access_node_instance_id")
        cluster_name = self.outputs.get("eks_cluster_name")
        karpenter_role_arn = self.outputs.get("karpenter_controller_role_arn")
        node_role_name = self.outputs.get("eks_node_role_name")
        region = self.config.aws_config.region

        if not instance_id:
            raise ValueError("SSM access node is not available in deployment outputs")
        if not cluster_name:
            raise ValueError("EKS cluster name not found in deployment outputs")
        if not karpenter_role_arn:
            raise ValueError("Karpenter controller role ARN not found in deployment outputs")
        if not node_role_name:
            raise ValueError("EKS node role name not found in deployment outputs")

        # Get configs
        karpenter_config = self.config.eks_config.karpenter
        argocd_config = self.config.addons.argocd if self.config.addons else None

        ssm = self._get_client("ssm")
        ssm_repo_password_param: str | None = None

        # Store ArgoCD repo password in SSM if provided
        if argocd_config and argocd_config.repository and (argocd_config.repository.password or "").strip():
            param_name = self._argocd_repo_password_param_name(
                self.customer_id, self.environment
            )
            try:
                ssm.put_parameter(
                    Name=param_name,
                    Value=argocd_config.repository.password,
                    Type="SecureString",
                    Overwrite=True,
                )
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") == "AccessDeniedException":
                    raise ValueError(
                        "Cannot store ArgoCD repo password in SSM. "
                        "The assumed role must have ssm:PutParameter for /byoc/*."
                    ) from e
                raise
            ssm_repo_password_param = param_name

        # Build combined script
        script = self._build_combined_install_script(
            cluster_name=cluster_name,
            region=region,
            karpenter_config=karpenter_config,
            karpenter_role_arn=karpenter_role_arn,
            node_role_name=node_role_name,
            argocd_config=argocd_config,
            ssm_repo_password_param=ssm_repo_password_param,
        )

        # Send SSM command
        response = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": script.split("\n")},
            TimeoutSeconds=900,  # 15 minutes for everything
            Comment=f"Install Karpenter + ArgoCD for {self.customer_id}-{self.environment}",
        )

        command_id = response["Command"]["CommandId"]

        self._save_addon_state("all", command_id, instance_id)

        return AddonInstallResult(
            addon_name="all",
            status=AddonInstallStatus.IN_PROGRESS,
            ssm_command_id=command_id,
            instance_id=instance_id,
            started_at=datetime.now(timezone.utc),
        )

    def _install_argocd_only_sync(
        self, argocd_config: ArgoCDAddonResolved
    ) -> AddonInstallResult:
        """Install ArgoCD only via SSM Run Command (blocking).

        Use this when Karpenter is already installed and you just need ArgoCD.
        """
        instance_id = self.outputs.get("access_node_instance_id")
        cluster_name = self.outputs.get("eks_cluster_name")
        region = self.config.aws_config.region

        if not instance_id:
            raise ValueError("SSM access node is not available in deployment outputs")
        if not cluster_name:
            raise ValueError("EKS cluster name not found in deployment outputs")

        ssm = self._get_client("ssm")
        ssm_repo_password_param: str | None = None

        if argocd_config.repository and (argocd_config.repository.password or "").strip():
            param_name = self._argocd_repo_password_param_name(
                self.customer_id, self.environment
            )
            try:
                ssm.put_parameter(
                    Name=param_name,
                    Value=argocd_config.repository.password,
                    Type="SecureString",
                    Overwrite=True,
                )
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") == "AccessDeniedException":
                    raise ValueError(
                        "Cannot store ArgoCD repo password in SSM. "
                        "The assumed role must have ssm:PutParameter for /byoc/*."
                    ) from e
                raise
            ssm_repo_password_param = param_name

        # Build ArgoCD-only script
        script_parts = [
            "#!/bin/bash",
            "set -euo pipefail",
            "",
            'export PATH="/usr/local/bin:$PATH"',
            'export HOME="${HOME:-/root}"',
            'mkdir -p "$HOME/.kube"',
            'export KUBECONFIG="$HOME/.kube/config"',
            "",
            f'CLUSTER_NAME="{cluster_name}"',
            f'REGION="{region}"',
            "",
            'echo "==> Configuring kubectl for $CLUSTER_NAME in $REGION..."',
            'aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$REGION"',
            "kubectl get nodes",
            "",
            self._build_argocd_install_script(
                argocd_config=argocd_config,
                ssm_repo_password_param=ssm_repo_password_param,
            ),
        ]

        script = "\n".join(script_parts)

        response = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": script.split("\n")},
            TimeoutSeconds=600,
            Comment=f"Install ArgoCD for {self.customer_id}-{self.environment}",
        )

        command_id = response["Command"]["CommandId"]

        self._save_addon_state("argocd", command_id, instance_id)

        return AddonInstallResult(
            addon_name="argocd",
            status=AddonInstallStatus.IN_PROGRESS,
            ssm_command_id=command_id,
            instance_id=instance_id,
            started_at=datetime.now(timezone.utc),
        )

    def _get_install_status_sync(
        self,
        command_id: str,
        instance_id: str,
        addon_name: str = "all",
    ) -> AddonInstallResult:
        """Check the status of an addon install SSM command (blocking)."""
        ssm = self._get_client("ssm")

        try:
            response = ssm.get_command_invocation(
                CommandId=command_id,
                InstanceId=instance_id,
            )
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "InvocationDoesNotExist":
                return AddonInstallResult(
                    addon_name=addon_name,
                    status=AddonInstallStatus.PENDING,
                    ssm_command_id=command_id,
                    instance_id=instance_id,
                )
            raise

        ssm_status = response.get("Status", "")
        output = response.get("StandardOutputContent", "")
        error = response.get("StandardErrorContent", "")

        if ssm_status in ("Pending", "InProgress", "Delayed"):
            install_status = AddonInstallStatus.IN_PROGRESS
        elif ssm_status == "Success":
            install_status = AddonInstallStatus.SUCCEEDED
        else:
            install_status = AddonInstallStatus.FAILED

        return AddonInstallResult(
            addon_name=addon_name,
            status=install_status,
            ssm_command_id=command_id,
            instance_id=instance_id,
            output=output or None,
            error=error or None,
        )


    async def install_all_addons(self) -> AddonInstallResult:
        """Install all addons (Karpenter + ArgoCD) via SSM Run Command."""
        return await asyncio.to_thread(self._install_all_addons_sync)

    async def install_argocd(
        self, argocd_config: ArgoCDAddonResolved
    ) -> AddonInstallResult:
        """Install ArgoCD only via SSM Run Command."""
        return await asyncio.to_thread(self._install_argocd_only_sync, argocd_config)

    async def get_install_status(
        self,
        command_id: str | None = None,
        instance_id: str | None = None,
        addon_name: str = "all",
    ) -> AddonInstallResult:
        """Check addon install status.

        If command_id/instance_id are not provided, looks up the last known
        install from the persisted addon state.
        """
        if not command_id or not instance_id:
            state = self._load_addon_state(addon_name)
            if not state:
                raise ValueError(
                    f"No {addon_name} installation found. "
                    "Trigger one via POST .../addons/install"
                )
            command_id = str(state["last_command_id"])
            instance_id = str(state["instance_id"])

        return await asyncio.to_thread(
            self._get_install_status_sync, command_id, instance_id, addon_name
        )
