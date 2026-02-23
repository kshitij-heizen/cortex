#!/bin/bash
# =============================================================================
# Orphaned EBS Volume Cleanup - By Volume ID
# =============================================================================
# Deletes specific orphaned EBS volumes from ACTIVE namespaces where
# deleted tenants left behind unattached volumes.
# Region: us-east-1
#
# These volumes can't be cleaned by namespace tag because the namespace
# is still active with other tenants. Each volume ID was verified:
#   1. EBS status = available (unattached)
#   2. PVC name tag does NOT match any active PVC in the namespace
#
# IMPORTANT: Review the dry-run output before running with --execute
# =============================================================================

set -euo pipefail

REGION="us-east-1"
COST_PER_GB=0.08  # gp3 us-east-1 $/GB/month
DRY_RUN=true

# Group names (indexed array)
GROUP_NAMES=(
    "falkordb-cortex-common"
    "falkordb-cortexai"
    "falkordb-g6nzayijls"
    "clickhouse"
    "default-vespa"
)

# Volume IDs per group (space-separated strings, indexed to match GROUP_NAMES)
# falkordb-cortex-common: deleted tenants (tenant-00, tenant-03, tenant-08, tenant-09, ahkxi6k, pzjjwx7ugb, 5c7oz5, 2bbo7i, xvyicyueav)
# Active tenants: 7d4pti, 7rvcv6, gsrkzo, hhogmo, j6hock, tenant-01, tenant-02, tenant-5, uqrs5r
GROUP_VOLS_0="vol-087ff9417498a974c vol-04c7a46dd22039721 vol-04eff29a9a2032348 vol-06124093437aa56ba vol-0b9edc0d58dba1f21 vol-01dbffd9acd7ecb95 vol-0a6645091ec64230d vol-05cd3663b58ebd328 vol-08dc599071d4f2246 vol-05f2c547bcf8baf20 vol-0ed4df7edf7268e69 vol-00bd06172eccfb466 vol-075d6ba5e9d110187 vol-009b03ba7b6a429d5 vol-02390ee6f228cffb2 vol-0074a01923bb874dd vol-0db202aff20e5fa3b"

# falkordb-cortexai: deleted tenants (tenant-2, tenant4, trial-tenant12)
# Active tenants: tenant-5, tenant1
GROUP_VOLS_1="vol-0c2f0cf4feaa0dc89 vol-07862e3d0fb37d23b vol-024495b1a07244807 vol-02b63106dc776e9a7 vol-012981a4708dbf2d7 vol-00fe7dd78c608e73b"

# falkordb-g6nzayijls: deleted tenants (all old tenant IDs)
# Active tenants: 6k4ksjzm6d, evocokdrzb, k5vtg4hi72
GROUP_VOLS_2="vol-03d68040b3fa6ce92 vol-0562f01741a0d6bb6 vol-007bac02caf94bd23 vol-0a69cd3de3a627df9 vol-0a6e14e19db64ebd3 vol-0a55962f289a8702e vol-0115b31fde904d27f vol-0470d0c4e761036e2 vol-05b440a1f6da67778 vol-0189799a6d4937871 vol-0bf257c42fd6b571e vol-0e7fad6bd279b0d40 vol-05b9359f1d761a6b9 vol-062161d90a0193429 vol-0677b0089b7fb760d vol-0e9ae4d8981003978 vol-0cbb45e6e69684f28 vol-04b99c6ae60ac8197 vol-0ad6ab2bb45550868 vol-0513ad6233c39fd4c vol-01fc99c821d19efcc vol-044524fb8efd0bfca vol-0befadedcf2951e94"

# clickhouse: old cluster volumes (5GB/40GB). Active cluster uses different vol-IDs with 50Gi/10Gi.
GROUP_VOLS_3="vol-0371d43f03fb36c04 vol-0a949039d8ed87a7b vol-0a26a4eb853119c22 vol-08ab2a8652166d3d5"

# default namespace: old vespa-content volumes from Nov 2025. No matching PVC exists.
GROUP_VOLS_4="vol-09ec5fc4aa38dacea vol-084ca229b3a83cdb5"

get_group_vols() {
    local idx=$1
    case $idx in
        0) echo "$GROUP_VOLS_0" ;;
        1) echo "$GROUP_VOLS_1" ;;
        2) echo "$GROUP_VOLS_2" ;;
        3) echo "$GROUP_VOLS_3" ;;
        4) echo "$GROUP_VOLS_4" ;;
    esac
}

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Deletes orphaned EBS volumes by specific volume ID."
    echo "These are volumes from deleted tenants inside active namespaces."
    echo ""
    echo "Options:"
    echo "  --dry-run       Show what would be deleted (default)"
    echo "  --execute       Actually delete the volumes"
    echo "  --group NAME    Only process volumes from a specific group/namespace"
    echo "  --list-groups   List available groups"
    echo "  --help          Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 --dry-run                              # Preview all deletions"
    echo "  $0 --execute                              # Delete all orphaned volumes"
    echo "  $0 --group falkordb-cortex-common         # Only that group"
    echo ""
}

log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1"; }

get_volume_info() {
    local vol_id=$1
    aws ec2 describe-volumes \
        --region "$REGION" \
        --volume-ids "$vol_id" \
        --query 'Volumes[0].{State:State,Size:Size,PVC:Tags[?Key==`kubernetes.io/created-for/pvc/name`]|[0].Value}' \
        --output json 2>/dev/null || echo '{"State":"not-found","Size":0,"PVC":"unknown"}'
}

find_group_index() {
    local name=$1
    local i=0
    for g in "${GROUP_NAMES[@]}"; do
        if [ "$g" = "$name" ]; then
            echo "$i"
            return
        fi
        ((i++))
    done
    echo "-1"
}

show_summary() {
    echo ""
    echo -e "${BOLD}==============================================================${NC}"
    echo -e "${BOLD}  ORPHANED EBS VOLUMES - BY VOLUME ID${NC}"
    echo -e "${BOLD}==============================================================${NC}"
    echo ""

    local grand_total_vols=0
    local grand_available_vols=0
    local grand_available_gb=0
    local grand_skipped=0

    printf "  ${BOLD}%-30s %8s %8s %10s %12s${NC}\n" "GROUP" "VOLUMES" "AVAIL" "SIZE (GB)" "SAVINGS/MO"
    printf "  %-30s %8s %8s %10s %12s\n" "------------------------------" "--------" "--------" "----------" "------------"

    local i=0
    for group in "${GROUP_NAMES[@]}"; do
        local vols
        vols=$(get_group_vols $i)
        local group_total=0
        local group_available=0
        local group_gb=0

        for vol_id in $vols; do
            [ -z "$vol_id" ] && continue
            ((group_total++))

            local info
            info=$(get_volume_info "$vol_id")
            local state
            state=$(echo "$info" | jq -r '.State // "not-found"')
            local size
            size=$(echo "$info" | jq -r '.Size // 0')

            if [ "$state" = "available" ]; then
                ((group_available++))
                ((group_gb+=size))
            fi
        done

        local group_cost
        group_cost=$(echo "scale=2; $group_gb * $COST_PER_GB" | bc)
        local group_skipped=$((group_total - group_available))

        printf "  %-30s %8d %8d %10d %12s\n" "$group" "$group_total" "$group_available" "$group_gb" "\$$group_cost"

        ((grand_total_vols+=group_total))
        ((grand_available_vols+=group_available))
        ((grand_available_gb+=group_gb))
        ((grand_skipped+=group_skipped))
        ((i++))
    done

    local grand_cost
    grand_cost=$(echo "scale=2; $grand_available_gb * $COST_PER_GB" | bc)
    local grand_annual
    grand_annual=$(echo "scale=2; $grand_available_gb * $COST_PER_GB * 12" | bc)

    printf "  %-30s %8s %8s %10s %12s\n" "------------------------------" "--------" "--------" "----------" "------------"
    printf "  ${BOLD}%-30s %8d %8d %10d %12s${NC}\n" "TOTAL" "$grand_total_vols" "$grand_available_vols" "$grand_available_gb" "\$$grand_cost"

    echo ""
    echo -e "  ${CYAN}Monthly savings:${NC}  ${BOLD}\$$grand_cost/month${NC}"
    echo -e "  ${CYAN}Annual savings:${NC}   ${BOLD}\$$grand_annual/year${NC}"
    echo -e "  ${CYAN}Volumes to delete:${NC} $grand_available_vols"
    if [ "$grand_skipped" -gt 0 ]; then
        echo -e "  ${YELLOW}Already gone/in-use:${NC} $grand_skipped (will be skipped)"
    fi
    echo ""
}

delete_volume() {
    local vol_id=$1
    local pvc_name=$2
    local size=$3

    if [ "$DRY_RUN" = true ]; then
        echo -e "  [DRY-RUN] Would delete: $vol_id (${size}GB) - $pvc_name"
    else
        if aws ec2 delete-volume --region "$REGION" --volume-id "$vol_id" 2>/dev/null; then
            log_success "Deleted: $vol_id (${size}GB) - $pvc_name"
        else
            log_error "Failed to delete: $vol_id"
        fi
    fi
}

process_group() {
    local idx=$1
    local group="${GROUP_NAMES[$idx]}"
    local vols
    vols=$(get_group_vols "$idx")

    echo ""
    log_info "Processing group: $group"

    local count=0
    local total_gb=0
    local skipped=0

    for vol_id in $vols; do
        [ -z "$vol_id" ] && continue

        local info
        info=$(get_volume_info "$vol_id")
        local state
        state=$(echo "$info" | jq -r '.State // "not-found"')
        local size
        size=$(echo "$info" | jq -r '.Size // 0')
        local pvc
        pvc=$(echo "$info" | jq -r '.PVC // "unknown"')

        if [ "$state" = "available" ]; then
            delete_volume "$vol_id" "$pvc" "$size"
            ((count++))
            ((total_gb+=size))
        else
            log_warn "  Skipping $vol_id (status: $state)"
            ((skipped++))
        fi
    done

    local cost
    cost=$(echo "scale=2; $total_gb * $COST_PER_GB" | bc)
    log_info "  Group $group: $count deleted, $skipped skipped, ${total_gb}GB freed (\$$cost/mo saved)"
}

# Parse arguments
SPECIFIC_GROUP=""
LIST_GROUPS=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --execute)
            DRY_RUN=false
            shift
            ;;
        --group)
            SPECIFIC_GROUP="$2"
            shift 2
            ;;
        --list-groups)
            LIST_GROUPS=true
            shift
            ;;
        --help)
            usage
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# List groups mode
if [ "$LIST_GROUPS" = true ]; then
    echo "Available groups:"
    i=0
    for group in "${GROUP_NAMES[@]}"; do
        vols=$(get_group_vols $i)
        count=0
        for v in $vols; do [ -n "$v" ] && ((count++)); done
        echo "  - $group ($count volumes)"
        ((i++))
    done
    exit 0
fi

# Main execution
echo ""
echo -e "${BOLD}==============================================================${NC}"
echo -e "${BOLD}  ORPHANED EBS VOLUME CLEANUP - BY VOLUME ID${NC}"
echo -e "${BOLD}  Region: $REGION${NC}"
echo -e "${BOLD}  Mode: $([ "$DRY_RUN" = true ] && echo 'DRY-RUN' || echo 'EXECUTE')${NC}"
echo -e "${BOLD}==============================================================${NC}"
echo ""

if [ "$DRY_RUN" = true ]; then
    log_warn "Running in DRY-RUN mode. No volumes will be deleted."
    log_warn "Use --execute to actually delete volumes."
fi

# Show summary
show_summary

# Confirm before executing
if [ "$DRY_RUN" = false ]; then
    echo ""
    read -p "Are you sure you want to delete these volumes? Type 'yes' to confirm: " confirm
    if [ "$confirm" != "yes" ]; then
        log_info "Aborted."
        exit 0
    fi
    echo ""
fi

# Process
log_info "Starting cleanup..."

if [ -n "$SPECIFIC_GROUP" ]; then
    idx=$(find_group_index "$SPECIFIC_GROUP")
    if [ "$idx" = "-1" ]; then
        log_error "Unknown group: $SPECIFIC_GROUP"
        echo "Use --list-groups to see available groups."
        exit 1
    fi
    process_group "$idx"
else
    i=0
    for group in "${GROUP_NAMES[@]}"; do
        process_group "$i"
        ((i++))
    done
fi

# Final summary
echo ""
echo -e "${BOLD}==============================================================${NC}"
if [ "$DRY_RUN" = true ]; then
    log_info "Dry-run complete. Run with --execute to delete volumes."
else
    log_success "Cleanup complete!"
fi
echo ""
