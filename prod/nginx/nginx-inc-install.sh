#!/bin/bash

# NGINX Inc Ingress Controller Installation Script
# Installs NGINX Inc (nginx-stable) Ingress Controller with TLS passthrough support for AWS EKS
# This is the FREE open-source version from NGINX Inc (not NGINX Plus)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROD_DIR="$(dirname "$SCRIPT_DIR")"

# Source common utilities
source "${PROD_DIR}/scripts/common.sh"

# Configuration
readonly COMPONENT_NAME="NGINX Inc Ingress Controller"
readonly NAMESPACE="nginx-inc"
readonly HELM_RELEASE_NAME="nginx-inc"
readonly HELM_REPO_NAME="nginx-stable"
readonly HELM_REPO_URL="https://helm.nginx.com/stable"
readonly HELM_CHART_VERSION="1.4.0"
readonly POD_READY_TIMEOUT=300
readonly VALUES_FILE="${SCRIPT_DIR}/nginx-inc-values.yaml"

main() {
    init_logging
    log_step "1" "Initializing ${COMPONENT_NAME} installation"
    
    log_info "Component: ${COMPONENT_NAME}"
    log_info "Namespace: ${NAMESPACE}"
    log_info "Script directory: ${SCRIPT_DIR}"
    log_info "Values file: ${VALUES_FILE}"
    
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
    
    if ! validate_values_file; then
        log_error "Values file validation failed"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi
    
    log_step "3" "Setting up Helm repository"
    if ! ensure_helm_repo "${HELM_REPO_NAME}" "${HELM_REPO_URL}"; then
        log_error "Failed to setup Helm repository"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi
    
    log_step "4" "Creating namespace"
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
    
    log_step "5" "Installing NGINX Inc Ingress Controller via Helm"
    install_nginx_inc
    
    log_step "6" "Waiting for NGINX Inc Ingress Controller pods to be ready"
    if ! wait_for_pods_ready "${NAMESPACE}" "app.kubernetes.io/name=nginx-ingress" "${POD_READY_TIMEOUT}"; then
        log_error "NGINX Inc Ingress Controller pods did not become ready"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi
    
    log_step "7" "Verifying Load Balancer provisioning"
    if ! verify_load_balancer; then
        log_warn "Load balancer may still be provisioning. Check status manually."
    fi
    
    log_step "8" "Verifying installation"
    verify_installation
    
    print_summary "success" "${COMPONENT_NAME}"
    log_info "NGINX Inc Ingress Controller is ready to accept traffic"
    log_info "IngressClass: nginx-inc (use this in your Ingress resources)"
}

validate_values_file() {
    if [[ ! -f "${VALUES_FILE}" ]]; then
        log_error "Values file not found: ${VALUES_FILE}"
        return 1
    fi
    log_success "Values file found: ${VALUES_FILE}"
    return 0
}

install_nginx_inc() {
    log_info "Checking if Helm release already exists..."
    
    local helm_args=(
        "${HELM_RELEASE_NAME}"
        "${HELM_REPO_NAME}/nginx-ingress"
        --namespace "${NAMESPACE}"
        --version "${HELM_CHART_VERSION}"
        -f "${VALUES_FILE}"
        --wait
        --timeout 300s
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
    
    log_success "NGINX Inc Ingress Controller Helm release deployed"
    return 0
}

verify_load_balancer() {
    log_info "Waiting for Load Balancer to be provisioned..."
    
    local timeout=180
    local elapsed=0
    local poll_interval=15
    
    while [[ $elapsed -lt $timeout ]]; do
        local lb_hostname
        lb_hostname=$(kubectl get svc "${HELM_RELEASE_NAME}-nginx-ingress-controller" -n "${NAMESPACE}" \
            -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo "")
        
        if [[ -n "$lb_hostname" ]]; then
            log_success "Load Balancer provisioned: ${lb_hostname}"
            log_info "DNS: Point your domain to this hostname"
            return 0
        fi
        
        log_info "Load Balancer not ready yet, waiting... (elapsed: ${elapsed}s)"
        sleep "$poll_interval"
        elapsed=$((elapsed + poll_interval))
    done
    
    log_warn "Load Balancer hostname not available after ${timeout}s"
    log_info "This may be normal for NLB provisioning. Verify manually with:"
    log_info "  kubectl get svc -n ${NAMESPACE}"
    return 1
}

verify_installation() {
    log_info "Verifying NGINX Inc Ingress Controller installation..."
    
    log_info "Ingress Controller pods:"
    kubectl get pods -n "${NAMESPACE}" -l app.kubernetes.io/name=nginx-ingress 2>&1 | tee -a "${LOG_FILE}"
    
    log_info "Ingress Controller services:"
    kubectl get svc -n "${NAMESPACE}" 2>&1 | tee -a "${LOG_FILE}"
    
    log_info "Checking IngressClass..."
    if kubectl get ingressclass nginx-inc &> /dev/null; then
        log_success "IngressClass 'nginx-inc' is available"
    else
        log_warn "IngressClass 'nginx-inc' not found"
    fi
    
    log_info "Checking GlobalConfiguration..."
    if kubectl get globalconfigurations.k8s.nginx.org -n "${NAMESPACE}" &> /dev/null; then
        log_success "GlobalConfiguration CRD is available"
        kubectl get globalconfigurations.k8s.nginx.org -n "${NAMESPACE}" 2>&1 | tee -a "${LOG_FILE}" || true
    else
        log_info "GlobalConfiguration not yet created or CRD not available"
    fi
    
    log_success "NGINX Inc Ingress Controller verification complete"
}

trap cleanup EXIT
main "$@"
