#!/bin/bash
# =============================================================================
# Orphaned EBS Volume Cleanup - By Namespace
# =============================================================================
# Deletes orphaned EBS volumes from deleted/empty Milvus and FalkorDB namespaces.
# For orphaned volumes inside ACTIVE namespaces, use cleanup-orphaned-ebs-by-id.sh
# Region: us-east-1
#
# IMPORTANT: Review the dry-run output before running with --execute
# =============================================================================

set -euo pipefail

REGION="us-east-1"
DRY_RUN=true

DELETED_NAMESPACES=(
    milvus-g6nzayijls
    milvus-iezp43lade
    milvus-qxzndm5x26
)

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --dry-run     Show what would be deleted (default)"
    echo "  --execute     Actually delete the volumes"
    echo "  --namespace   Delete volumes for a specific namespace"
    echo "  --all         Delete ALL orphaned volumes (DANGEROUS)"
    echo "  --help        Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 --dry-run                    # Preview deletions"
    echo "  $0 --execute                    # Delete confirmed orphaned volumes"
    echo "  $0 --namespace milvus-acme      # Delete only milvus-acme volumes"
    echo ""
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

get_orphaned_volumes_for_namespace() {
    local namespace=$1
    aws ec2 describe-volumes \
        --region "$REGION" \
        --filters "Name=status,Values=available" "Name=tag:kubernetes.io/created-for/pvc/namespace,Values=$namespace" \
        --query 'Volumes[*].[VolumeId,Size]' \
        --output text 2>/dev/null || echo ""
}

get_all_orphaned_volumes() {
    aws ec2 describe-volumes \
        --region "$REGION" \
        --filters "Name=status,Values=available" \
        --query 'Volumes[*].[VolumeId,Size,Tags[?Key==`kubernetes.io/created-for/pvc/namespace`]|[0].Value]' \
        --output text 2>/dev/null || echo ""
}

delete_volume() {
    local volume_id=$1
    if [ "$DRY_RUN" = true ]; then
        echo "  [DRY-RUN] Would delete: $volume_id"
    else
        if aws ec2 delete-volume --region "$REGION" --volume-id "$volume_id" 2>/dev/null; then
            log_success "Deleted: $volume_id"
        else
            log_error "Failed to delete: $volume_id"
        fi
    fi
}

cleanup_namespace() {
    local namespace=$1
    log_info "Processing namespace: $namespace"
    
    local volumes
    volumes=$(get_orphaned_volumes_for_namespace "$namespace")
    
    if [ -z "$volumes" ]; then
        log_warn "  No orphaned volumes found for $namespace"
        return
    fi
    
    local count=0
    local total_size=0
    
    while IFS=$'\t' read -r volume_id size; do
        if [ -n "$volume_id" ]; then
            delete_volume "$volume_id"
            ((count++))
            ((total_size+=size))
        fi
    done <<< "$volumes"
    
    log_info "  Namespace $namespace: $count volumes, ${total_size}GB"
}

cleanup_all_orphaned() {
    log_warn "Cleaning up ALL orphaned volumes..."
    
    local volumes
    volumes=$(get_all_orphaned_volumes)
    
    if [ -z "$volumes" ]; then
        log_info "No orphaned volumes found"
        return
    fi
    
    local count=0
    local total_size=0
    
    while IFS=$'\t' read -r volume_id size namespace; do
        if [ -n "$volume_id" ]; then
            echo "  Namespace: ${namespace:-unknown}"
            delete_volume "$volume_id"
            ((count++))
            ((total_size+=size))
        fi
    done <<< "$volumes"
    
    log_info "Total: $count volumes, ${total_size}GB"
}

show_summary() {
    echo ""
    echo "=============================================="
    echo "ORPHANED EBS VOLUME SUMMARY"
    echo "=============================================="
    echo ""
    
    local grand_total_count=0
    local grand_total_size=0
    
    printf "%-30s %10s %10s %15s\n" "NAMESPACE" "VOLUMES" "SIZE (GB)" "COST/MONTH"
    printf "%-30s %10s %10s %15s\n" "------------------------------" "----------" "----------" "---------------"
    
    for namespace in "${DELETED_NAMESPACES[@]}"; do
        local volumes
        volumes=$(get_orphaned_volumes_for_namespace "$namespace")
        
        if [ -n "$volumes" ]; then
            local count=0
            local total_size=0
            
            while IFS=$'\t' read -r volume_id size; do
                if [ -n "$volume_id" ]; then
                    ((count++))
                    ((total_size+=size))
                fi
            done <<< "$volumes"
            
            local cost
            cost=$(echo "scale=2; $total_size * 0.08" | bc)
            printf "%-30s %10d %10d %15s\n" "$namespace" "$count" "$total_size" "\$$cost"
            
            ((grand_total_count+=count))
            ((grand_total_size+=total_size))
        fi
    done
    
    local grand_total_cost
    grand_total_cost=$(echo "scale=2; $grand_total_size * 0.08" | bc)
    
    printf "%-30s %10s %10s %15s\n" "------------------------------" "----------" "----------" "---------------"
    printf "%-30s %10d %10d %15s\n" "TOTAL (Safe to Delete)" "$grand_total_count" "$grand_total_size" "\$$grand_total_cost/mo"
    echo ""
}

# Parse arguments
SPECIFIC_NAMESPACE=""
DELETE_ALL=false

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
        --namespace)
            SPECIFIC_NAMESPACE="$2"
            shift 2
            ;;
        --all)
            DELETE_ALL=true
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

# Main execution
echo ""
echo "=============================================="
echo "ORPHANED EBS VOLUME CLEANUP"
echo "Region: $REGION"
echo "Mode: $([ "$DRY_RUN" = true ] && echo 'DRY-RUN' || echo 'EXECUTE')"
echo "=============================================="
echo ""

if [ "$DRY_RUN" = true ]; then
    log_warn "Running in DRY-RUN mode. No volumes will be deleted."
    log_warn "Use --execute to actually delete volumes."
    echo ""
fi

# Show summary first
show_summary

if [ "$DRY_RUN" = false ]; then
    echo ""
    read -p "Are you sure you want to delete these volumes? (yes/no): " confirm
    if [ "$confirm" != "yes" ]; then
        log_info "Aborted."
        exit 0
    fi
fi

echo ""
log_info "Starting cleanup..."
echo ""

if [ -n "$SPECIFIC_NAMESPACE" ]; then
    cleanup_namespace "$SPECIFIC_NAMESPACE"
elif [ "$DELETE_ALL" = true ]; then
    cleanup_all_orphaned
else
    for namespace in "${DELETED_NAMESPACES[@]}"; do
        cleanup_namespace "$namespace"
    done
fi

echo ""
if [ "$DRY_RUN" = true ]; then
    log_info "Dry-run complete. Run with --execute to delete volumes."
else
    log_success "Cleanup complete!"
fi
