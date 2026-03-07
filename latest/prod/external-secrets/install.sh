#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROD_DIR="$(dirname "$SCRIPT_DIR")"

source "${PROD_DIR}/scripts/common.sh"

readonly COMPONENT_NAME="External Secrets Operator"
readonly NAMESPACE="external-secrets"
readonly ESO_VERSION="${ESO_VERSION:-0.9.11}"

main() {
    log_step "1" "Initializing ${COMPONENT_NAME} installation"
    init_logging

    log_info "Component: ${COMPONENT_NAME}"
    log_info "Version: ${ESO_VERSION}"
    log_info "Script directory: ${SCRIPT_DIR}"

    log_step "2" "Validating prerequisites"
    if ! check_prerequisites; then
        log_error "Prerequisites check failed"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi

    if ! check_cluster_connection; then
        log_error "Cluster connection check failed"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi

    log_step "3" "Adding External Secrets Helm repository"
    if ! helm repo add external-secrets https://charts.external-secrets.io 2>&1 | tee -a "${LOG_FILE}"; then
        log_warn "Helm repo may already exist"
    fi
    helm repo update 2>&1 | tee -a "${LOG_FILE}"

    log_step "4" "Installing External Secrets Operator"
    if ! helm upgrade --install external-secrets external-secrets/external-secrets \
        --namespace "${NAMESPACE}" \
        --create-namespace \
        --version "${ESO_VERSION}" \
        --set installCRDs=true \
        --set webhook.port=9443 \
        --wait \
        --timeout 5m 2>&1 | tee -a "${LOG_FILE}"; then
        log_error "Failed to install External Secrets Operator"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi

    log_step "5" "Waiting for ESO pods to be ready"
    if ! kubectl wait --for=condition=ready pod \
        -l app.kubernetes.io/name=external-secrets \
        -n "${NAMESPACE}" \
        --timeout=120s 2>&1 | tee -a "${LOG_FILE}"; then
        log_error "ESO pods did not become ready"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi

    log_step "6" "Creating ClusterSecretStore"
    if ! kubectl apply -f "${SCRIPT_DIR}/01_cluster-secret-store.yaml" 2>&1 | tee -a "${LOG_FILE}"; then
        log_error "Failed to create ClusterSecretStore"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi

    log_step "7" "Verifying ClusterSecretStore"
    sleep 5
    if ! kubectl get clustersecretstore aws-secrets-manager -o jsonpath='{.status.conditions[0].status}' 2>&1 | grep -q "True"; then
        log_warn "ClusterSecretStore may not be ready yet. Check status with:"
        log_warn "  kubectl get clustersecretstore aws-secrets-manager -o yaml"
    else
        log_success "ClusterSecretStore is ready"
    fi

    log_step "8" "Applying ExternalSecrets for cortex-app"
    if ! kubectl apply -f "${SCRIPT_DIR}/02_external-secret-cortex-app.yaml" 2>&1 | tee -a "${LOG_FILE}"; then
        log_warn "Failed to apply cortex-app ExternalSecret"
    fi

    log_step "9" "Applying ExternalSecrets for cortex-ingestion"
    if ! kubectl apply -f "${SCRIPT_DIR}/03_external-secret-cortex-ingestion.yaml" 2>&1 | tee -a "${LOG_FILE}"; then
        log_warn "Failed to apply cortex-ingestion ExternalSecret"
    fi

    log_step "10" "Verifying installation"
    verify_installation

    print_summary "success" "${COMPONENT_NAME}"
    print_next_steps
}

verify_installation() {
    log_info "Verifying External Secrets installation..."

    log_info "External Secrets pods:"
    kubectl get pods -n "${NAMESPACE}" 2>&1 | tee -a "${LOG_FILE}" || true

    log_info "ClusterSecretStores:"
    kubectl get clustersecretstore 2>&1 | tee -a "${LOG_FILE}" || true

    log_info "ExternalSecrets:"
    kubectl get externalsecret -A 2>&1 | tee -a "${LOG_FILE}" || true

    log_success "External Secrets verification complete"
}

print_next_steps() {
    echo ""
    log_info "Next Steps:"
    echo "========================================"
    echo ""
    echo "1. Create secrets in AWS Secrets Manager:"
    echo "   aws secretsmanager create-secret \\"
    echo "     --name cortex/prod/app-secrets \\"
    echo "     --secret-string file://secrets.json \\"
    echo "     --region us-east-1"
    echo ""
    echo "2. Verify ExternalSecrets are syncing:"
    echo "   kubectl get externalsecret -A"
    echo "   kubectl get secrets -n cortex-app"
    echo ""
    echo "3. Check sync status:"
    echo "   kubectl describe externalsecret cortex-app-external-secrets -n cortex-app"
    echo ""
    echo "4. View synced secret:"
    echo "   kubectl get secret cortex-app-secrets -n cortex-app -o yaml"
    echo ""
    echo "========================================"
}

trap cleanup EXIT
main "$@"
