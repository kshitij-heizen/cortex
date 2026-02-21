#!/bin/bash

# Monitoring Stack Installation Script
# Installs and configures Prometheus and Grafana using kube-prometheus-stack

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROD_DIR="$(dirname "$SCRIPT_DIR")"

# Source common utilities
source "${PROD_DIR}/scripts/common.sh"

# Configuration
readonly COMPONENT_NAME="Monitoring Stack"
readonly NAMESPACE="monitoring"
readonly HELM_RELEASE_NAME="monitoring"
readonly HELM_REPO_NAME="prometheus-community"
readonly HELM_REPO_URL="https://prometheus-community.github.io/helm-charts"
readonly HELM_CHART_VERSION="65.1.1"
readonly VALUES_FILE="${SCRIPT_DIR}/monitoring-values.yaml"

# Timeouts
readonly POD_READY_TIMEOUT=600
readonly HELM_TIMEOUT=600

main() {
    init_logging
    log_step "1" "Initializing ${COMPONENT_NAME} installation"
    
    log_info "Component: ${COMPONENT_NAME}"
    log_info "Namespace: ${NAMESPACE}"
    log_info "Helm Chart Version: ${HELM_CHART_VERSION}"
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
    
    log_step "4" "Setting up Helm repository"
    if ! ensure_helm_repo "${HELM_REPO_NAME}" "${HELM_REPO_URL}"; then
        log_error "Failed to setup Helm repository"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi
    
    log_step "5" "Creating namespace"
    if ! ensure_namespace "${NAMESPACE}"; then
        log_error "Failed to create namespace"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi
    
    if ! wait_for_namespace "${NAMESPACE}" 60; then
        log_error "Namespace did not become active"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi
    
    log_step "6" "Installing kube-prometheus-stack"
    if ! install_monitoring_stack; then
        log_error "Failed to install monitoring stack"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi
    
    log_step "7" "Waiting for monitoring pods to be ready"
    if ! wait_for_monitoring_pods; then
        log_error "Monitoring pods did not become ready"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi
    
    log_step "8" "Creating Prometheus basic auth secret"
    if ! create_prometheus_auth_secret; then
        log_warn "Failed to create Prometheus auth secret. Manual creation may be required."
    fi
    
    log_step "9" "Deploying ingress resources"
    if ! deploy_ingress_resources; then
        log_error "Failed to deploy ingress resources"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi
    
    log_step "10" "Verifying installation"
    verify_installation
    
    print_summary "success" "${COMPONENT_NAME}"
    print_access_info
}

validate_config_files() {
    log_info "Validating configuration files..."
    
    if [[ ! -f "$VALUES_FILE" ]]; then
        log_error "Values file not found: ${VALUES_FILE}"
        return 1
    fi
    
    local required_files=(
        "${SCRIPT_DIR}/grafana-ingress.yaml"
        "${SCRIPT_DIR}/prometheus-ingress.yaml"
    )
    
    for file in "${required_files[@]}"; do
        if [[ ! -f "$file" ]]; then
            log_warn "Optional file not found: ${file}"
        fi
    done
    
    log_success "Configuration files validated"
    return 0
}

install_monitoring_stack() {
    log_info "Installing kube-prometheus-stack..."
    
    local helm_args=(
        "${HELM_RELEASE_NAME}"
        "${HELM_REPO_NAME}/kube-prometheus-stack"
        --namespace "${NAMESPACE}"
        --version "${HELM_CHART_VERSION}"
        --values "${VALUES_FILE}"
        --set prometheus.prometheusSpec.nodeSelector.role=general
        --set grafana.nodeSelector.role=general
        --wait
        --timeout "${HELM_TIMEOUT}s"
    )
    
    if helm_release_exists "${NAMESPACE}" "${HELM_RELEASE_NAME}"; then
        log_info "Helm release exists, upgrading..."
        if ! helm upgrade "${helm_args[@]}" 2>&1 | tee -a "${LOG_FILE}"; then
            log_error "Helm upgrade failed"
            return 1
        fi
    else
        log_info "Installing new Helm release..."
        if ! helm install "${helm_args[@]}" 2>&1 | tee -a "${LOG_FILE}"; then
            log_error "Helm install failed"
            return 1
        fi
    fi
    
    log_success "Monitoring stack Helm release deployed"
    return 0
}

wait_for_monitoring_pods() {
    log_info "Waiting for Prometheus pods..."
    if ! wait_for_pods_ready "${NAMESPACE}" "app.kubernetes.io/name=prometheus" "${POD_READY_TIMEOUT}"; then
        log_warn "Prometheus pods may not be fully ready"
    fi
    
    log_info "Waiting for Grafana pods..."
    if ! wait_for_deployment "${NAMESPACE}" "${HELM_RELEASE_NAME}-grafana" 300; then
        log_error "Grafana deployment is not ready"
        return 1
    fi
    
    log_info "Waiting for kube-state-metrics pods..."
    if ! wait_for_deployment "${NAMESPACE}" "${HELM_RELEASE_NAME}-kube-state-metrics" 180; then
        log_warn "kube-state-metrics may not be fully ready"
    fi
    
    log_info "Waiting for Prometheus Operator..."
    if ! wait_for_deployment "${NAMESPACE}" "${HELM_RELEASE_NAME}-kube-prom-operator" 180; then
        log_warn "Prometheus Operator may not be fully ready"
    fi
    
    # Verify all pods are running
    log_info "Final pod status check..."
    if ! wait_for_pods_ready "${NAMESPACE}" "" "${POD_READY_TIMEOUT}"; then
        log_warn "Some pods may still be initializing"
    fi
    
    log_success "Monitoring pods are ready"
    return 0
}

create_prometheus_auth_secret() {
    log_info "Creating Prometheus basic auth secret..."
    
    local secret_name="prometheus-basic-auth"
    
    # Check if secret already exists
    if kubectl get secret "$secret_name" -n "${NAMESPACE}" &> /dev/null; then
        log_info "Secret '${secret_name}' already exists"
        return 0
    fi
    
    # Check if htpasswd is available
    if ! command -v htpasswd &> /dev/null; then
        log_warn "htpasswd not found. Please create the secret manually:"
        log_info "  1. Install apache2-utils: apt-get install apache2-utils (or brew install httpd on macOS)"
        log_info "  2. Create htpasswd file: htpasswd -c auth admin"
        log_info "  3. Create secret: kubectl create secret generic ${secret_name} --from-file=auth -n ${NAMESPACE}"
        return 1
    fi
    
    # Generate a random password and create the secret
    local temp_auth_file
    temp_auth_file=$(mktemp)
    local prometheus_password
    prometheus_password=$(openssl rand -base64 12 | tr -d '=' | head -c 16)
    
    log_info "Generating htpasswd file..."
    if ! htpasswd -bc "$temp_auth_file" admin "$prometheus_password" 2>&1 | tee -a "${LOG_FILE}"; then
        rm -f "$temp_auth_file"
        log_error "Failed to create htpasswd file"
        return 1
    fi
    
    log_info "Creating Kubernetes secret..."
    if ! kubectl create secret generic "$secret_name" \
        --from-file=auth="$temp_auth_file" \
        -n "${NAMESPACE}" 2>&1 | tee -a "${LOG_FILE}"; then
        rm -f "$temp_auth_file"
        log_error "Failed to create secret"
        return 1
    fi
    
    rm -f "$temp_auth_file"
    
    log_success "Prometheus basic auth secret created"
    log_info "Prometheus admin password: ${prometheus_password}"
    log_warn "Please save this password securely. It will not be displayed again."
    
    return 0
}

deploy_ingress_resources() {
    log_info "Deploying ingress resources..."
    
    local ingress_files=(
        "${SCRIPT_DIR}/grafana-ingress.yaml"
        "${SCRIPT_DIR}/prometheus-ingress.yaml"
    )
    
    for ingress_file in "${ingress_files[@]}"; do
        if [[ -f "$ingress_file" ]]; then
            local ingress_name
            ingress_name=$(basename "$ingress_file" .yaml)
            if ! apply_manifest "$ingress_file" "${ingress_name}"; then
                log_warn "Failed to apply ${ingress_file}"
            fi
        else
            log_warn "Ingress file not found: ${ingress_file}"
        fi
    done
    
    log_success "Ingress resources deployed"
    return 0
}

verify_installation() {
    log_info "Verifying monitoring stack installation..."
    
    log_info "Helm release status:"
    helm status "${HELM_RELEASE_NAME}" -n "${NAMESPACE}" 2>&1 | head -20 | tee -a "${LOG_FILE}" || true
    
    log_info "Monitoring pods:"
    kubectl get pods -n "${NAMESPACE}" 2>&1 | tee -a "${LOG_FILE}" || true
    
    log_info "Monitoring services:"
    kubectl get svc -n "${NAMESPACE}" 2>&1 | tee -a "${LOG_FILE}" || true
    
    log_info "Ingress resources:"
    kubectl get ingress -n "${NAMESPACE}" 2>&1 | tee -a "${LOG_FILE}" || true
    
    log_info "PersistentVolumeClaims:"
    kubectl get pvc -n "${NAMESPACE}" 2>&1 | tee -a "${LOG_FILE}" || true
    
    log_info "ServiceMonitors:"
    kubectl get servicemonitor -n "${NAMESPACE}" 2>&1 | tee -a "${LOG_FILE}" || true
    
    log_success "Monitoring stack verification complete"
}

print_access_info() {
    echo ""
    log_info "Access Information:"
    echo "========================================"
    echo ""
    echo "Grafana:"
    echo "  URL: https://grafana-prod.usecortex.ai"
    echo "  Username: admin"
    echo "  Password: (defined in monitoring-values.yaml)"
    echo ""
    echo "Prometheus:"
    echo "  URL: https://prometheus-prod.usecortex.ai"
    echo "  Username: admin"
    echo "  Password: (from prometheus-basic-auth secret)"
    echo ""
    echo "Internal Endpoints:"
    echo "  Prometheus: http://${HELM_RELEASE_NAME}-kube-prom-prometheus.${NAMESPACE}.svc:9090"
    echo "  Grafana: http://${HELM_RELEASE_NAME}-grafana.${NAMESPACE}.svc:80"
    echo ""
    echo "To retrieve Grafana admin password:"
    echo "  kubectl get secret ${HELM_RELEASE_NAME}-grafana -n ${NAMESPACE} -o jsonpath='{.data.admin-password}' | base64 -d"
    echo "========================================"
}

trap cleanup EXIT
main "$@"
