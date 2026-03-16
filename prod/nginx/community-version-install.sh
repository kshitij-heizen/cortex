#!/bin/bash

# NGINX Ingress Controller Installation Script
# Installs and configures NGINX Ingress Controller with NLB for AWS EKS

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROD_DIR="$(dirname "$SCRIPT_DIR")"

# Source common utilities
source "${PROD_DIR}/scripts/common.sh"

# Configuration
readonly COMPONENT_NAME="NGINX Ingress Controller"
readonly NAMESPACE="ingress-nginx"
readonly HELM_RELEASE_NAME="ingress-nginx"
readonly HELM_REPO_NAME="ingress-nginx"
readonly HELM_REPO_URL="https://kubernetes.github.io/ingress-nginx"
readonly HELM_CHART_VERSION="4.11.3"
readonly POD_READY_TIMEOUT=300

main() {
    init_logging
    log_step "1" "Initializing ${COMPONENT_NAME} installation"
    
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
    
    log_step "5" "Installing NGINX Ingress Controller via Helm"
    install_nginx_ingress
    
    log_step "6" "Waiting for NGINX Ingress Controller pods to be ready"
    if ! wait_for_pods_ready "${NAMESPACE}" "app.kubernetes.io/name=ingress-nginx" "${POD_READY_TIMEOUT}"; then
        log_error "NGINX Ingress Controller pods did not become ready"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi
    
    log_step "7" "Applying NLB service configuration"
    if ! apply_nlb_config; then
        log_error "Failed to apply NLB configuration"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi
    
    log_step "8" "Verifying Load Balancer provisioning"
    if ! verify_load_balancer; then
        log_warn "Load balancer may still be provisioning. Check status manually."
    fi
    
    log_step "9" "Verifying installation"
    verify_installation
    
    print_summary "success" "${COMPONENT_NAME}"
    log_info "NGINX Ingress Controller is ready to accept traffic"
}

install_nginx_ingress() {
    log_info "Checking if Helm release already exists..."
    
    local helm_args=(
        "${HELM_RELEASE_NAME}"
        "${HELM_REPO_NAME}/ingress-nginx"
        --namespace "${NAMESPACE}"
        --version "${HELM_CHART_VERSION}"
        --set controller.replicaCount=2
        --set controller.nodeSelector.role=general
        --set controller.service.type=LoadBalancer
        --set controller.service.externalTrafficPolicy=Local
        --set controller.metrics.enabled=true
        --set controller.metrics.serviceMonitor.enabled=true
        --set controller.metrics.serviceMonitor.additionalLabels.release=monitoring
        --set controller.config.use-forwarded-headers="true"
        --set controller.config.compute-full-forwarded-for="true"
        --set controller.config.use-proxy-protocol="false"
        --set defaultBackend.enabled=false
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
    
    log_success "NGINX Ingress Controller Helm release deployed"
    return 0
}

apply_nlb_config() {
    local nlb_manifest="${SCRIPT_DIR}/nlb.yaml"
    
    if [[ ! -f "$nlb_manifest" ]]; then
        log_error "NLB manifest not found: ${nlb_manifest}"
        return 1
    fi
    
    log_info "Applying NLB service configuration..."
    if ! apply_manifest "$nlb_manifest" "NLB Service Configuration"; then
        return 1
    fi
    
    log_success "NLB configuration applied"
    return 0
}

verify_load_balancer() {
    log_info "Waiting for Load Balancer to be provisioned..."
    
    local timeout=180
    local elapsed=0
    local poll_interval=15
    
    while [[ $elapsed -lt $timeout ]]; do
        local lb_hostname
        lb_hostname=$(kubectl get svc ingress-nginx-controller -n "${NAMESPACE}" \
            -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo "")
        
        if [[ -n "$lb_hostname" ]]; then
            log_success "Load Balancer provisioned: ${lb_hostname}"
            return 0
        fi
        
        log_info "Load Balancer not ready yet, waiting... (elapsed: ${elapsed}s)"
        sleep "$poll_interval"
        elapsed=$((elapsed + poll_interval))
    done
    
    log_warn "Load Balancer hostname not available after ${timeout}s"
    log_info "This may be normal for NLB provisioning. Verify manually with:"
    log_info "  kubectl get svc ingress-nginx-controller -n ${NAMESPACE}"
    return 1
}

verify_installation() {
    log_info "Verifying NGINX Ingress Controller installation..."
    
    log_info "Ingress Controller pods:"
    kubectl get pods -n "${NAMESPACE}" -l app.kubernetes.io/name=ingress-nginx 2>&1 | tee -a "${LOG_FILE}"
    
    log_info "Ingress Controller services:"
    kubectl get svc -n "${NAMESPACE}" 2>&1 | tee -a "${LOG_FILE}"
    
    log_info "Checking IngressClass..."
    if kubectl get ingressclass nginx &> /dev/null; then
        log_success "IngressClass 'nginx' is available"
    else
        log_warn "IngressClass 'nginx' not found"
    fi
    
    log_success "NGINX Ingress Controller verification complete"
}

trap cleanup EXIT
main "$@"
