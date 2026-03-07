#!/bin/bash

set -euo pipefail

# ============================================================================
# External Secrets IRSA Setup Script
# This script creates the IAM role and policy needed for External Secrets
# to access AWS Secrets Manager using IRSA (IAM Roles for Service Accounts)
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }

# Configuration - UPDATE THESE VALUES
CLUSTER_NAME="${CLUSTER_NAME:-cortex-prod}"
AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text)}"

readonly NAMESPACE="external-secrets"
readonly SERVICE_ACCOUNT="external-secrets"
readonly POLICY_NAME="CortexExternalSecretsPolicy"
readonly ROLE_NAME="CortexExternalSecretsRole"

usage() {
    cat << EOF
Usage: $(basename "$0") [OPTIONS]

Setup IRSA for External Secrets Operator to access AWS Secrets Manager.

OPTIONS:
    -h, --help              Show this help message
    -c, --cluster NAME      EKS cluster name (default: cortex-prod)
    -r, --region REGION     AWS region (default: us-east-1)
    --dry-run               Show what would be done without making changes

ENVIRONMENT VARIABLES:
    CLUSTER_NAME            EKS cluster name
    AWS_REGION              AWS region
    AWS_ACCOUNT_ID          AWS account ID (auto-detected if not set)

EXAMPLES:
    $(basename "$0") -c my-cluster -r us-west-2
    CLUSTER_NAME=cortex-prod $(basename "$0")

EOF
}

check_prerequisites() {
    log_info "Checking prerequisites..."

    local required_commands=("aws" "kubectl" "eksctl")
    local missing_commands=()

    for cmd in "${required_commands[@]}"; do
        if ! command -v "$cmd" &> /dev/null; then
            missing_commands+=("$cmd")
        fi
    done

    if [[ ${#missing_commands[@]} -gt 0 ]]; then
        log_error "Missing required commands: ${missing_commands[*]}"
        return 1
    fi

    # Check AWS credentials
    if ! aws sts get-caller-identity &> /dev/null; then
        log_error "AWS credentials not configured or invalid"
        return 1
    fi

    log_success "Prerequisites check passed"
    return 0
}

check_oidc_provider() {
    log_info "Checking OIDC provider for cluster ${CLUSTER_NAME}..."

    local oidc_id
    oidc_id=$(aws eks describe-cluster --name "${CLUSTER_NAME}" --region "${AWS_REGION}" \
        --query "cluster.identity.oidc.issuer" --output text 2>/dev/null | cut -d '/' -f 5)

    if [[ -z "$oidc_id" ]]; then
        log_error "Could not get OIDC provider ID for cluster ${CLUSTER_NAME}"
        return 1
    fi

    if ! aws iam list-open-id-connect-providers | grep -q "$oidc_id"; then
        log_warn "OIDC provider not found. Creating..."
        if ! eksctl utils associate-iam-oidc-provider \
            --cluster "${CLUSTER_NAME}" \
            --region "${AWS_REGION}" \
            --approve; then
            log_error "Failed to create OIDC provider"
            return 1
        fi
        log_success "OIDC provider created"
    else
        log_success "OIDC provider already exists"
    fi

    return 0
}

create_iam_policy() {
    log_info "Creating IAM policy ${POLICY_NAME}..."

    local policy_arn="arn:aws:iam::${AWS_ACCOUNT_ID}:policy/${POLICY_NAME}"

    # Check if policy already exists
    if aws iam get-policy --policy-arn "$policy_arn" &> /dev/null; then
        log_info "Policy already exists, updating..."
        
        # Get the current version count
        local versions
        versions=$(aws iam list-policy-versions --policy-arn "$policy_arn" \
            --query 'Versions[?IsDefaultVersion==`false`].VersionId' --output text)
        
        # Delete old versions if we have 5 (max)
        for version in $versions; do
            aws iam delete-policy-version --policy-arn "$policy_arn" --version-id "$version" 2>/dev/null || true
        done
        
        # Create new version
        aws iam create-policy-version \
            --policy-arn "$policy_arn" \
            --policy-document "file://${SCRIPT_DIR}/00_iam-policy.json" \
            --set-as-default
        
        log_success "Policy updated"
    else
        # Create new policy
        if ! aws iam create-policy \
            --policy-name "${POLICY_NAME}" \
            --policy-document "file://${SCRIPT_DIR}/00_iam-policy.json" \
            --description "Policy for External Secrets Operator to access Cortex secrets"; then
            log_error "Failed to create IAM policy"
            return 1
        fi
        log_success "Policy created"
    fi

    echo "$policy_arn"
    return 0
}

create_irsa() {
    log_info "Creating IRSA for External Secrets..."

    local policy_arn="arn:aws:iam::${AWS_ACCOUNT_ID}:policy/${POLICY_NAME}"

    # Check if service account already exists with a role
    if kubectl get sa "${SERVICE_ACCOUNT}" -n "${NAMESPACE}" &> /dev/null; then
        local existing_role
        existing_role=$(kubectl get sa "${SERVICE_ACCOUNT}" -n "${NAMESPACE}" \
            -o jsonpath='{.metadata.annotations.eks\.amazonaws\.com/role-arn}' 2>/dev/null || echo "")
        
        if [[ -n "$existing_role" ]]; then
            log_info "Service account already has role: ${existing_role}"
            log_info "Updating role policy attachment..."
            
            local role_name
            role_name=$(echo "$existing_role" | cut -d '/' -f 2)
            
            # Attach the policy to existing role
            aws iam attach-role-policy \
                --role-name "$role_name" \
                --policy-arn "$policy_arn" 2>/dev/null || true
            
            log_success "Role policy updated"
            return 0
        fi
    fi

    # Create new IRSA
    if ! eksctl create iamserviceaccount \
        --name "${SERVICE_ACCOUNT}" \
        --namespace "${NAMESPACE}" \
        --cluster "${CLUSTER_NAME}" \
        --region "${AWS_REGION}" \
        --attach-policy-arn "${policy_arn}" \
        --role-name "${ROLE_NAME}" \
        --override-existing-serviceaccounts \
        --approve; then
        log_error "Failed to create IRSA"
        return 1
    fi

    log_success "IRSA created successfully"
    return 0
}

verify_setup() {
    log_info "Verifying IRSA setup..."

    # Check service account annotation
    local role_arn
    role_arn=$(kubectl get sa "${SERVICE_ACCOUNT}" -n "${NAMESPACE}" \
        -o jsonpath='{.metadata.annotations.eks\.amazonaws\.com/role-arn}' 2>/dev/null || echo "")

    if [[ -z "$role_arn" ]]; then
        log_error "Service account does not have IAM role annotation"
        return 1
    fi

    log_success "Service account has IAM role: ${role_arn}"

    # Restart external-secrets pods to pick up new credentials
    log_info "Restarting External Secrets pods..."
    kubectl rollout restart deployment -n "${NAMESPACE}" 2>/dev/null || true

    log_success "IRSA setup verified"
    return 0
}

print_summary() {
    echo ""
    echo "========================================"
    echo -e "${GREEN}IRSA Setup Complete${NC}"
    echo "========================================"
    echo ""
    echo "Cluster:          ${CLUSTER_NAME}"
    echo "Region:           ${AWS_REGION}"
    echo "Account ID:       ${AWS_ACCOUNT_ID}"
    echo "Namespace:        ${NAMESPACE}"
    echo "Service Account:  ${SERVICE_ACCOUNT}"
    echo "IAM Policy:       ${POLICY_NAME}"
    echo "IAM Role:         ${ROLE_NAME}"
    echo ""
    echo "Next steps:"
    echo "1. Create your secrets in AWS Secrets Manager:"
    echo "   aws secretsmanager create-secret --name cortex/prod/app-secrets \\"
    echo "     --secret-string '{\"OPENAI_API_KEY\":\"sk-...\"}' --region ${AWS_REGION}"
    echo ""
    echo "2. Apply the ExternalSecret manifests:"
    echo "   kubectl apply -f ${SCRIPT_DIR}/02_external-secret-cortex-app.yaml"
    echo "   kubectl apply -f ${SCRIPT_DIR}/03_external-secret-cortex-ingestion.yaml"
    echo ""
    echo "3. Verify secrets are syncing:"
    echo "   kubectl get externalsecret -A"
    echo ""
    echo "========================================"
}

main() {
    local dry_run=false

    while [[ $# -gt 0 ]]; do
        case "$1" in
            -h|--help)
                usage
                exit 0
                ;;
            -c|--cluster)
                CLUSTER_NAME="$2"
                shift 2
                ;;
            -r|--region)
                AWS_REGION="$2"
                shift 2
                ;;
            --dry-run)
                dry_run=true
                shift
                ;;
            *)
                log_error "Unknown option: $1"
                usage
                exit 1
                ;;
        esac
    done

    echo ""
    echo "========================================"
    echo "External Secrets IRSA Setup"
    echo "========================================"
    echo ""

    log_info "Cluster: ${CLUSTER_NAME}"
    log_info "Region: ${AWS_REGION}"
    log_info "Account ID: ${AWS_ACCOUNT_ID}"

    if [[ "$dry_run" == true ]]; then
        log_info "DRY RUN - No changes will be made"
        exit 0
    fi

    if ! check_prerequisites; then
        exit 1
    fi

    if ! check_oidc_provider; then
        exit 1
    fi

    if ! create_iam_policy; then
        exit 1
    fi

    if ! create_irsa; then
        exit 1
    fi

    if ! verify_setup; then
        log_warn "Setup may not be complete. Please verify manually."
    fi

    print_summary
}

main "$@"
