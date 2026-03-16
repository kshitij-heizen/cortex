#!/bin/bash

set -euo pipefail

# ============================================================================
# Create AWS Secrets Manager Secrets for Cortex
# This script creates the initial secrets in AWS Secrets Manager
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROD_DIR="$(dirname "$SCRIPT_DIR")"

readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }

AWS_REGION="${AWS_REGION:-us-east-1}"

usage() {
    cat << EOF
Usage: $(basename "$0") [OPTIONS]

Create secrets in AWS Secrets Manager from local secrets.env files.

OPTIONS:
    -h, --help              Show this help message
    -r, --region REGION     AWS region (default: us-east-1)
    --app-only              Only create cortex-app secrets
    --ingestion-only        Only create cortex-ingestion secrets
    --update                Update existing secrets instead of failing

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --region us-west-2 --update

EOF
}

env_to_json() {
    local env_file="$1"
    local json_output="{"
    local first=true

    while IFS='=' read -r key value || [[ -n "$key" ]]; do
        # Skip empty lines and comments
        [[ -z "$key" || "$key" =~ ^# ]] && continue
        
        # Handle multiline values (like FIREBASE_PRIVATE_KEY)
        # The value should already be on one line with \n escape sequences
        
        if [[ "$first" == true ]]; then
            first=false
        else
            json_output+=","
        fi
        
        # Escape special characters for JSON
        value="${value//\\/\\\\}"  # Escape backslashes first
        value="${value//\"/\\\"}"  # Escape quotes
        
        json_output+="\"${key}\":\"${value}\""
    done < "$env_file"

    json_output+="}"
    echo "$json_output"
}

create_secret() {
    local secret_name="$1"
    local secret_value="$2"
    local update_mode="$3"

    log_info "Creating secret: ${secret_name}"

    # Check if secret already exists
    if aws secretsmanager describe-secret --secret-id "$secret_name" --region "$AWS_REGION" &> /dev/null; then
        if [[ "$update_mode" == true ]]; then
            log_info "Secret exists, updating..."
            if aws secretsmanager put-secret-value \
                --secret-id "$secret_name" \
                --secret-string "$secret_value" \
                --region "$AWS_REGION"; then
                log_success "Secret updated: ${secret_name}"
                return 0
            else
                log_error "Failed to update secret: ${secret_name}"
                return 1
            fi
        else
            log_warn "Secret already exists: ${secret_name}"
            log_info "Use --update flag to update existing secrets"
            return 0
        fi
    fi

    # Create new secret
    if aws secretsmanager create-secret \
        --name "$secret_name" \
        --secret-string "$secret_value" \
        --region "$AWS_REGION" \
        --description "Cortex application secrets managed by External Secrets Operator"; then
        log_success "Secret created: ${secret_name}"
        return 0
    else
        log_error "Failed to create secret: ${secret_name}"
        return 1
    fi
}

create_cortex_app_secrets() {
    local update_mode="$1"
    local secrets_file="${PROD_DIR}/cortex-app/secrets.env"

    if [[ ! -f "$secrets_file" ]]; then
        log_error "Secrets file not found: ${secrets_file}"
        return 1
    fi

    log_info "Reading secrets from: ${secrets_file}"
    
    local json_secrets
    json_secrets=$(env_to_json "$secrets_file")
    
    create_secret "cortex/prod/app-secrets" "$json_secrets" "$update_mode"
}

create_cortex_ingestion_secrets() {
    local update_mode="$1"
    local secrets_file="${PROD_DIR}/cortex-ingestion/secrets.env"

    if [[ ! -f "$secrets_file" ]]; then
        log_error "Secrets file not found: ${secrets_file}"
        log_info "Skipping cortex-ingestion secrets"
        return 0
    fi

    log_info "Reading secrets from: ${secrets_file}"
    
    local json_secrets
    json_secrets=$(env_to_json "$secrets_file")
    
    create_secret "cortex/prod/ingestion-secrets" "$json_secrets" "$update_mode"
}

main() {
    local update_mode=false
    local app_only=false
    local ingestion_only=false

    while [[ $# -gt 0 ]]; do
        case "$1" in
            -h|--help)
                usage
                exit 0
                ;;
            -r|--region)
                AWS_REGION="$2"
                shift 2
                ;;
            --app-only)
                app_only=true
                shift
                ;;
            --ingestion-only)
                ingestion_only=true
                shift
                ;;
            --update)
                update_mode=true
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
    echo "Create AWS Secrets Manager Secrets"
    echo "========================================"
    echo ""

    log_info "Region: ${AWS_REGION}"
    log_info "Update mode: ${update_mode}"

    # Verify AWS credentials
    if ! aws sts get-caller-identity &> /dev/null; then
        log_error "AWS credentials not configured or invalid"
        exit 1
    fi

    local success=true

    if [[ "$ingestion_only" != true ]]; then
        if ! create_cortex_app_secrets "$update_mode"; then
            success=false
        fi
    fi

    if [[ "$app_only" != true ]]; then
        if ! create_cortex_ingestion_secrets "$update_mode"; then
            success=false
        fi
    fi

    echo ""
    if [[ "$success" == true ]]; then
        log_success "All secrets created successfully!"
        echo ""
        echo "Next steps:"
        echo "1. Run the External Secrets install script:"
        echo "   ${SCRIPT_DIR}/install.sh"
        echo ""
        echo "2. Verify secrets are syncing:"
        echo "   kubectl get externalsecret -A"
        echo "   kubectl get secrets -n cortex-app"
        echo ""
    else
        log_error "Some secrets failed to create"
        exit 1
    fi
}

main "$@"
