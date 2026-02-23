#!/bin/bash
# =============================================================================
# Find Orphaned FalkorDB EBS Volumes
# =============================================================================
# Lists all available (unattached) EBS volumes tagged to falkordb-* namespaces,
# cross-references with active K8s PVCs, and outputs orphaned ones.
#
# Output: namespace, volume ID, PVC name, size, and whether the namespace exists.
# =============================================================================

set -euo pipefail

REGION="us-east-1"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${BOLD}Finding all available FalkorDB EBS volumes in ${REGION}...${NC}"
echo ""

# Get all available EBS volumes tagged to falkordb-* namespaces
all_vols=$(aws ec2 describe-volumes \
    --region "$REGION" \
    --filters "Name=status,Values=available" \
    --query 'Volumes[*].{VolumeId:VolumeId,Size:Size,NS:Tags[?Key==`kubernetes.io/created-for/pvc/namespace`]|[0].Value,PVC:Tags[?Key==`kubernetes.io/created-for/pvc/name`]|[0].Value}' \
    --output json 2>/dev/null | jq -r '.[] | select(.NS != null and (.NS | startswith("falkordb-"))) | [.NS, .VolumeId, .PVC // "unknown", (.Size|tostring)] | @tsv' | sort)

if [ -z "$all_vols" ]; then
    echo "No available FalkorDB EBS volumes found."
    exit 0
fi

# Get list of existing namespaces
existing_ns=$(kubectl get ns -o jsonpath='{.items[*].metadata.name}')

# Collect active PVCs per namespace (cache to avoid repeated kubectl calls)
declare_active_pvcs=""
checked_ns=""

total_orphaned=0
total_size=0
total_cost=0

# Print header
echo -e "${BOLD}$(printf "%-35s %-25s %-60s %8s %s" "NAMESPACE" "VOLUME_ID" "PVC_NAME" "SIZE_GB" "STATUS")${NC}"
printf "%-35s %-25s %-60s %8s %s\n" "-----------------------------------" "-------------------------" "------------------------------------------------------------" "--------" "----------"

prev_ns=""
while IFS=$'\t' read -r ns vol_id pvc size; do
    [ -z "$ns" ] && continue

    # Check if namespace exists
    ns_exists=false
    if echo "$existing_ns" | tr ' ' '\n' | grep -q "^${ns}$"; then
        ns_exists=true
    fi

    # Determine if volume is orphaned
    orphaned=false
    status=""

    if [ "$ns_exists" = false ]; then
        orphaned=true
        status="NS_GONE"
    else
        # Check PVCs and pods in namespace (cache per namespace)
        if [ "$ns" != "$prev_ns" ]; then
            active_pvcs=$(kubectl get pvc -n "$ns" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null || echo "")
            pod_count=$(kubectl get pods -n "$ns" --no-headers 2>/dev/null | wc -l | tr -d ' ')
            prev_ns="$ns"
        fi

        if [ -z "$active_pvcs" ]; then
            orphaned=true
            status="NS_EMPTY"
        elif [ "$pod_count" = "0" ]; then
            orphaned=true
            status="NO_PODS"
        elif ! echo "$active_pvcs" | grep -q "^${pvc}$"; then
            orphaned=true
            status="PVC_GONE"
        else
            status="ACTIVE"
        fi
    fi

    if [ "$orphaned" = true ]; then
        case "$status" in
            NS_GONE)  color="$RED" ;;
            NS_EMPTY) color="$YELLOW" ;;
            NO_PODS)  color="$YELLOW" ;;
            PVC_GONE) color="$CYAN" ;;
        esac
        echo -e "${color}$(printf "%-35s %-25s %-60s %8s %s" "$ns" "$vol_id" "$pvc" "${size}" "$status")${NC}"
        ((total_orphaned++))
        ((total_size+=size))
    fi
done <<< "$all_vols"

total_cost=$(echo "scale=2; $total_size * 0.08" | bc)
annual_cost=$(echo "scale=2; $total_size * 0.08 * 12" | bc)

echo ""
printf "%-35s %-25s %-60s %8s %s\n" "-----------------------------------" "-------------------------" "------------------------------------------------------------" "--------" "----------"
echo ""
echo -e "${BOLD}Summary:${NC}"
echo -e "  Orphaned volumes: ${BOLD}${total_orphaned}${NC}"
echo -e "  Total size:       ${BOLD}${total_size} GB${NC}"
echo -e "  Monthly cost:     ${BOLD}\$${total_cost}/month${NC}"
echo -e "  Annual cost:      ${BOLD}\$${annual_cost}/year${NC}"
echo ""
echo -e "${BOLD}Legend:${NC}"
echo -e "  ${RED}NS_GONE${NC}   - Namespace no longer exists"
echo -e "  ${YELLOW}NS_EMPTY${NC}  - Namespace exists but has no active PVCs"
echo -e "  ${YELLOW}NO_PODS${NC}   - Namespace has PVCs but no running pods (helm uninstalled, PVCs retained)"
echo -e "  ${CYAN}PVC_GONE${NC}  - Namespace active, but this specific PVC is gone (deleted tenant)"
echo ""
echo -e "Copy volume IDs to cleanup-orphaned-ebs-by-id.sh or cleanup-orphaned-ebs.sh as needed."
