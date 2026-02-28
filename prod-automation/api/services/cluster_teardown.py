class ClusterTeardownService:
    """Gracefully tear down all cluster workloads before pulumi destroy."""

    def _build_teardown_script(self, cluster_name: str, region: str) -> str:
        return f"""#!/bin/bash
set -eo pipefail
export PATH="/usr/local/bin:$PATH"
export HOME="${{HOME:-/root}}"
mkdir -p "$HOME/.kube"
export KUBECONFIG="$HOME/.kube/config"

CLUSTER_NAME="{cluster_name}"
REGION="{region}"

echo "==> Configuring kubectl..."
aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$REGION"

# =============================================================================
# PHASE 1: Delete ArgoCD Applications (stops all workloads)
# =============================================================================
echo "==> Phase 1: Deleting ArgoCD applications..."
if kubectl get namespace argocd &>/dev/null; then
    # Delete root app first (prevents re-sync of child apps)
    kubectl delete applications --all -n argocd --timeout=300s || true
    echo "==> Waiting for application resources to terminate..."
    sleep 30
    # Remove finalizers from any stuck applications
    for app in $(kubectl get applications -n argocd -o name 2>/dev/null); do
        kubectl patch $app -n argocd --type merge -p '{{"metadata":{{"finalizers":null}}}}' 2>/dev/null || true
    done
    kubectl delete applications --all -n argocd --timeout=60s 2>/dev/null || true
fi
echo "==> ArgoCD applications deleted!"

# =============================================================================
# PHASE 2: Delete NLBs (created by NGINX ingress)
# =============================================================================
echo "==> Phase 2: Deleting LoadBalancer services..."
kubectl delete svc -n nginx-inc --all --timeout=120s 2>/dev/null || true
# Wait for NLB to deregister from VPC
echo "==> Waiting for NLBs to deregister (3 min)..."
sleep 180

# =============================================================================
# PHASE 3: Delete Karpenter nodes
# =============================================================================
echo "==> Phase 3: Deleting Karpenter managed nodes..."
kubectl delete nodepools --all --timeout=120s 2>/dev/null || true
kubectl delete ec2nodeclasses --all --timeout=120s 2>/dev/null || true
echo "==> Waiting for Karpenter nodes to terminate (2 min)..."
sleep 120

# =============================================================================
# PHASE 4: Delete CRD instances (before removing operators)
# =============================================================================
echo "==> Phase 4: Deleting CRD instances..."

# FalkorDB clusters
kubectl delete clusters.apps.kubeblocks.io --all -A --timeout=120s 2>/dev/null || true

# Milvus clusters
kubectl delete milvus --all -A --timeout=120s 2>/dev/null || true

# ClickHouse installations
kubectl delete clickhouseinstallations --all -A --timeout=120s 2>/dev/null || true

# ClusterIssuers
kubectl delete clusterissuers --all --timeout=60s 2>/dev/null || true

# ClusterSecretStores
kubectl delete clustersecretstores --all --timeout=60s 2>/dev/null || true

# ExternalSecrets
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
for ns in argocd external-secrets cert-manager monitoring clickhouse milvus-operator kubeblocks karpenter nginx-inc falkordb-shared falkordb-cortexai milvus-cortexai cortex-app cortex-ingestion nextjs vector falkordb-dashboard; do
    kubectl delete namespace $ns --timeout=60s 2>/dev/null || true
done

# Remove stuck namespace finalizers
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
echo "Remaining services with LoadBalancer:"
kubectl get svc -A --field-selector spec.type=LoadBalancer 2>/dev/null || true
echo ""
echo "Remaining PVCs:"
kubectl get pvc -A 2>/dev/null || true
echo ""
echo "==> Cluster teardown complete! Safe to run pulumi destroy."
"""
