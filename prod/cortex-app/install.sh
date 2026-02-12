#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROD_DIR="$(dirname "$SCRIPT_DIR")"

source "${PROD_DIR}/scripts/common.sh"

readonly COMPONENT_NAME="Cortex Application"
readonly NAMESPACE="cortex-app"
readonly POD_READY_TIMEOUT=300

main() {
    log_step "1" "Initializing ${COMPONENT_NAME} installation"
    init_logging

    log_info "Component: ${COMPONENT_NAME}"
    log_info "Namespace: ${NAMESPACE}"
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

    log_step "3" "Validating configuration files"
    if ! validate_config_files; then
        log_error "Configuration validation failed"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi

    log_step "4" "Creating namespace"
    if ! apply_manifest "${SCRIPT_DIR}/00_namespace.yaml" "Namespace"; then
        log_error "Failed to create namespace"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi

    if ! wait_for_namespace "${NAMESPACE}" 60; then
        log_error "Namespace did not become active"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi

    log_step "5" "Applying ConfigMap"
    if ! apply_manifest "${SCRIPT_DIR}/01_configmap.yaml" "ConfigMap"; then
        log_error "Failed to apply ConfigMap"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi

    log_step "6" "Creating secrets"
    if ! create_secrets; then
        log_error "Failed to create secrets"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi

    log_step "7" "Deploying application"
    if ! deploy_application; then
        log_error "Failed to deploy application"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi

    log_step "8" "Applying Service and ServiceAccount"
    if ! apply_manifest "${SCRIPT_DIR}/04_service.yaml" "Service and ServiceAccount"; then
        log_error "Failed to apply Service"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi

    log_step "9" "Waiting for pods to be ready"
    if ! wait_for_deployment "${NAMESPACE}" "cortex-app" "${POD_READY_TIMEOUT}"; then
        log_error "Deployment did not become ready"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi

    log_step "10" "Applying Ingress"
    if ! apply_manifest "${SCRIPT_DIR}/05_ingress.yaml" "Ingress"; then
        log_warn "Failed to apply Ingress"
    fi

    log_step "11" "Applying HPA"
    if ! apply_manifest "${SCRIPT_DIR}/06_hpa.yaml" "HorizontalPodAutoscaler"; then
        log_warn "Failed to apply HPA"
    fi

    log_step "12" "Applying ServiceMonitor"
    if ! apply_manifest "${SCRIPT_DIR}/07_servicemonitor.yaml" "ServiceMonitor"; then
        log_warn "Failed to apply ServiceMonitor (may require Prometheus CRDs)"
    fi

    log_step "13" "Applying PodDisruptionBudget"
    if ! apply_manifest "${SCRIPT_DIR}/08_pdb.yaml" "PodDisruptionBudget"; then
        log_warn "Failed to apply PDB"
    fi

    log_step "14" "Verifying installation"
    verify_installation

    print_summary "success" "${COMPONENT_NAME}"
    print_access_info
}

validate_config_files() {
    log_info "Validating configuration files..."

    local required_files=(
        "${SCRIPT_DIR}/00_namespace.yaml"
        "${SCRIPT_DIR}/01_configmap.yaml"
        "${SCRIPT_DIR}/03_deployment.yaml"
        "${SCRIPT_DIR}/04_service.yaml"
        "${SCRIPT_DIR}/05_ingress.yaml"
    )

    for file in "${required_files[@]}"; do
        if [[ ! -f "$file" ]]; then
            log_error "Required file not found: ${file}"
            return 1
        fi
    done

    log_success "Configuration files validated"
    return 0
}

create_secrets() {
    log_info "Verifying secrets from External Secrets Operator..."

    # Check if ExternalSecret exists and is synced
    if kubectl get externalsecret cortex-app-external-secrets -n "${NAMESPACE}" &> /dev/null; then
        local status
        status=$(kubectl get externalsecret cortex-app-external-secrets -n "${NAMESPACE}" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')
        
        if [[ "$status" == "True" ]]; then
            log_success "ExternalSecret is synced and ready"
            
            # Verify the K8s secret was created
            if kubectl get secret cortex-app-secrets -n "${NAMESPACE}" &> /dev/null; then
                local key_count
                key_count=$(kubectl get secret cortex-app-secrets -n "${NAMESPACE}" -o jsonpath='{.data}' | jq 'keys | length')
                log_success "Secret cortex-app-secrets exists with ${key_count} keys"
                return 0
            else
                log_error "ExternalSecret is ready but K8s secret was not created"
                return 1
            fi
        else
            log_error "ExternalSecret exists but is not ready. Status: ${status}"
            log_info "Check: kubectl describe externalsecret cortex-app-external-secrets -n ${NAMESPACE}"
            return 1
        fi
    else
        log_error "ExternalSecret not found. Please run external-secrets/install.sh first"
        log_info "Secrets are managed by External Secrets Operator syncing from AWS Secrets Manager"
        return 1
    fi
}

deploy_application() {
    log_info "Deploying application..."

    local deployment_file="${SCRIPT_DIR}/03_deployment.yaml"

    if [[ -z "${ECR_REGISTRY:-}" ]]; then
        log_error "ECR_REGISTRY environment variable is not set"
        return 1
    fi

    if [[ -z "${IMAGE_TAG:-}" ]]; then
        log_warn "IMAGE_TAG not set, using 'latest'"
        IMAGE_TAG="latest"
    fi

    log_info "Using image: ${ECR_REGISTRY}/cortex-app:${IMAGE_TAG}"

    local temp_deployment
    temp_deployment=$(mktemp)

    sed -e "s|\${ECR_REGISTRY}|${ECR_REGISTRY}|g" \
        -e "s|\${IMAGE_TAG}|${IMAGE_TAG}|g" \
        -e "s|\${CORTEX_APP_IAM_ROLE_ARN}|${CORTEX_APP_IAM_ROLE_ARN:-}|g" \
        "${deployment_file}" > "${temp_deployment}"

    sed -e "s|\${ECR_REGISTRY}|${ECR_REGISTRY}|g" \
        -e "s|\${IMAGE_TAG}|${IMAGE_TAG}|g" \
        -e "s|\${CORTEX_APP_IAM_ROLE_ARN}|${CORTEX_APP_IAM_ROLE_ARN:-}|g" \
        "${SCRIPT_DIR}/04_service.yaml" > "${SCRIPT_DIR}/04_service.yaml.tmp"
    mv "${SCRIPT_DIR}/04_service.yaml.tmp" "${SCRIPT_DIR}/04_service.yaml.rendered"

    if ! kubectl apply -f "${temp_deployment}" 2>&1 | tee -a "${LOG_FILE}"; then
        rm -f "${temp_deployment}"
        log_error "Failed to apply deployment"
        return 1
    fi

    rm -f "${temp_deployment}"
    log_success "Application deployed"
    return 0
}

verify_installation() {
    log_info "Verifying Cortex Application installation..."

    log_info "Deployment status:"
    kubectl get deployment -n "${NAMESPACE}" 2>&1 | tee -a "${LOG_FILE}" || true

    log_info "Pods:"
    kubectl get pods -n "${NAMESPACE}" 2>&1 | tee -a "${LOG_FILE}" || true

    log_info "Services:"
    kubectl get svc -n "${NAMESPACE}" 2>&1 | tee -a "${LOG_FILE}" || true

    log_info "Ingress:"
    kubectl get ingress -n "${NAMESPACE}" 2>&1 | tee -a "${LOG_FILE}" || true

    log_info "HPA:"
    kubectl get hpa -n "${NAMESPACE}" 2>&1 | tee -a "${LOG_FILE}" || true

    log_success "Cortex Application verification complete"
}

print_access_info() {
    echo ""
    log_info "Access Information:"
    echo "========================================"
    echo ""
    echo "Cortex API:"
    echo "  External URL: https://api.usecortex.ai"
    echo "  Internal URL: http://cortex-app.cortex-app.svc.cluster.local:80"
    echo ""
    echo "Health Check:"
    echo "  kubectl exec -it \$(kubectl get pod -n ${NAMESPACE} -l app.kubernetes.io/name=cortex-app -o jsonpath='{.items[0].metadata.name}') -n ${NAMESPACE} -- curl http://localhost:8080/"
    echo ""
    echo "View Logs:"
    echo "  kubectl logs -f -l app.kubernetes.io/name=cortex-app -n ${NAMESPACE}"
    echo ""
    echo "Scale Deployment:"
    echo "  kubectl scale deployment cortex-app -n ${NAMESPACE} --replicas=5"
    echo ""
    echo "========================================"
}

trap cleanup EXIT
main "$@"

