#!/bin/bash

# Deploy a FalkorDB Tenant
# Usage: ./deploy-tenant.sh <organization> <tenant-id>
# Example: ./deploy-tenant.sh acme-corp tenant-1
#
# Creates a FalkorDB cluster for a specific tenant within an organization.
# Multiple tenants can share the same namespace (falkordb-<org>).
#
# All tenants share port 443 via SNI-based routing (TransportServer).
# Each tenant gets a unique hostname: <org>-<tenant-id>.falkordb.usecortex.ai
#
# Prerequisites:
#   - NGINX Inc Ingress Controller with TLS passthrough
#   - DNS: *.falkordb.usecortex.ai -> NGINX Inc LB
#   - Wildcard cert in falkordb-shared namespace

set -e

ORG=$1
TENANT_ID=$2

if [ -z "$ORG" ] || [ -z "$TENANT_ID" ]; then
    echo "Usage: ./deploy-tenant.sh <organization> <tenant-id>"
    echo "Example: ./deploy-tenant.sh acme-corp tenant-1"
    echo ""
    echo "Arguments:"
    echo "  organization  - Organization name (e.g., acme-corp)"
    echo "  tenant-id     - Unique tenant identifier (e.g., tenant-1 or a1b2c3d4)"
    exit 1
fi

if ! echo "$ORG" | grep -qE '^[a-z0-9]([-a-z0-9]*[a-z0-9])?$'; then
    echo "Error: Organization name must be DNS-safe (lowercase, alphanumeric, hyphens)"
    exit 1
fi

if ! echo "$TENANT_ID" | grep -qE '^[a-z0-9]([-a-z0-9]*[a-z0-9])?$'; then
    echo "Error: Tenant ID must be DNS-safe (lowercase, alphanumeric, hyphens)"
    exit 1
fi

# Validate combined length to avoid K8s 63-char limit
# Format: falkordb-{org}-{tenant}-exporter (longest suffix)
FULL_NAME="falkordb-${ORG}-${TENANT_ID}-stats-exporter"
if [ ${#FULL_NAME} -gt 63 ]; then
    echo "Error: Combined name too long (${#FULL_NAME} chars, max 63)"
    echo "       Full name: ${FULL_NAME}"
    echo "       Reduce organization or tenant-id length"
    exit 1
fi

NAMESPACE="falkordb-${ORG}"
RELEASE_NAME="${ORG}-${TENANT_ID}-falkordb"

echo "=========================================="
echo "Deploying FalkorDB Tenant"
echo "=========================================="
echo "Organization: ${ORG}"
echo "Tenant ID:    ${TENANT_ID}"
echo "Namespace:    ${NAMESPACE}"
echo "Release:      ${RELEASE_NAME}"
echo "=========================================="

# Get script directory for relative path to chart
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHART_DIR="${SCRIPT_DIR}/../falkor-chart"

# Deploy Helm chart
# TransportServer is created automatically for SNI-based routing on port 443
helm upgrade --install "${RELEASE_NAME}" "${CHART_DIR}" \
    --namespace "${NAMESPACE}" \
    --create-namespace \
    --set organization="${ORG}" \
    --set tenantId="${TENANT_ID}" \
    --set namespace="${NAMESPACE}"

echo ""
echo "=========================================="
echo "Deployment initiated!"
echo "=========================================="
echo ""
echo "TransportServer created for SNI routing:"
echo "  kubectl get transportserver -n ${NAMESPACE}"
echo ""
echo "Wait for pods to be ready:"
echo "  kubectl get pods -n ${NAMESPACE} -w"
echo ""
echo "Check certificate status:"
echo "  kubectl get certificate -n ${NAMESPACE}"
echo ""
echo "Password (using shared secret):"
echo "  kubectl get secret falkordb-shared-password -n falkordb-shared -o jsonpath='{.data.password}' | base64 -d"
echo ""
echo "Connection URL (external TLS - port 443):"
echo "  rediss://${ORG}-${TENANT_ID}.falkordb.usecortex.ai:443"
echo ""
echo "Test connection:"
echo "  redis-cli -h ${ORG}-${TENANT_ID}.falkordb.usecortex.ai -p 443 --tls --insecure -a '<password>' PING"
echo ""
echo "Connection URL (internal - no TLS):"
echo "  redis://falkordb-${ORG}-${TENANT_ID}-falkordb-falkordb.${NAMESPACE}.svc.cluster.local:6379"
