#!/bin/bash

# Graph Database Installation Script
# Installs and configures the graph database cluster using KubeBlocks on AWS EKS

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROD_DIR="$(dirname "$SCRIPT_DIR")"

# Source common utilities
source "${PROD_DIR}/scripts/common.sh"

# Configuration
readonly COMPONENT_NAME="Graph Database"
readonly NAMESPACE="falkordb"
readonly KUBEBLOCKS_NAMESPACE="kb-system"
readonly KUBEBLOCKS_VERSION="1.0.1"
readonly ADDON_VERSION="1.0.1"
readonly HELM_REPO_NAME="kubeblocks"
readonly HELM_REPO_URL="https://apecloud.github.io/helm-charts"
readonly CLUSTER_NAME="falkordb-prod"
readonly KUBEBLOCKS_CRD_URL="https://github.com/apecloud/kubeblocks/releases/download/v${KUBEBLOCKS_VERSION}/kubeblocks_crds.yaml"
readonly KBCLI_INSTALL_URL="https://kubeblocks.io/installer/install_cli.sh"

# Timeouts
readonly KUBEBLOCKS_READY_TIMEOUT=600
readonly CLUSTER_READY_TIMEOUT=900
readonly POD_READY_TIMEOUT=600

main() {
    log_step "1" "Initializing ${COMPONENT_NAME} installation"
    init_logging
    
    log_info "Component: ${COMPONENT_NAME}"
    log_info "Namespace: ${NAMESPACE}"
    log_info "KubeBlocks Version: ${KUBEBLOCKS_VERSION}"
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
    
    log_step "3" "Installing KubeBlocks CRDs"
    if ! install_kubeblocks_crds; then
        log_error "Failed to install KubeBlocks CRDs"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi
    
    log_step "4" "Installing KubeBlocks operator"
    if ! install_kubeblocks; then
        log_error "Failed to install KubeBlocks"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi
    
    log_step "5" "Waiting for KubeBlocks pods to be ready"
    if ! wait_for_pods_ready "${KUBEBLOCKS_NAMESPACE}" "" "${KUBEBLOCKS_READY_TIMEOUT}"; then
        log_error "KubeBlocks pods did not become ready"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi
    
    log_step "6" "Installing kbcli and graph database addon"
    if ! install_addon; then
        log_error "Failed to install graph database addon"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi
    
    log_step "7" "Creating graph database namespace and storage classes"
    if ! setup_namespace_and_storage; then
        log_error "Failed to setup namespace and storage"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi
    
    log_step "8" "Deploying graph database cluster"
    if ! deploy_cluster; then
        log_error "Failed to deploy graph database cluster"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi
    
    log_step "9" "Waiting for graph database cluster to be ready"
    if ! wait_for_cluster_ready; then
        log_error "Graph database cluster did not become ready"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi
    
    log_step "10" "Deploying exporters and service monitors"
    if ! deploy_exporters; then
        log_error "Failed to deploy exporters"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi
    
    log_step "11" "Configuring NGINX TCP proxy"
    if ! configure_tcp_proxy; then
        log_error "Failed to configure TCP proxy"
        print_summary "failure" "${COMPONENT_NAME}"
        exit 1
    fi
    
    log_step "12" "Verifying installation"
    verify_installation
    
    print_summary "success" "${COMPONENT_NAME}"
    log_info "Graph database cluster is ready"
    print_connection_info
}

install_kubeblocks_crds() {
    log_info "Installing KubeBlocks CRDs from ${KUBEBLOCKS_CRD_URL}..."
    
    if ! kubectl create -f "${KUBEBLOCKS_CRD_URL}" 2>&1 | tee -a "${LOG_FILE}"; then
        # Check if CRDs already exist
        if kubectl get crd clusters.apps.kubeblocks.io &> /dev/null; then
            log_info "KubeBlocks CRDs already exist, continuing..."
            return 0
        fi
        log_error "Failed to install KubeBlocks CRDs"
        return 1
    fi
    
    log_info "Waiting for CRDs to be established..."
    local crds=("clusters.apps.kubeblocks.io" "opsrequests.operations.kubeblocks.io")
    for crd in "${crds[@]}"; do
        if ! wait_for_crd "$crd" 120; then
            log_error "CRD ${crd} did not become established"
            return 1
        fi
    done
    
    log_success "KubeBlocks CRDs installed"
    return 0
}

install_kubeblocks() {
    log_info "Setting up KubeBlocks Helm repository..."
    if ! ensure_helm_repo "${HELM_REPO_NAME}" "${HELM_REPO_URL}"; then
        return 1
    fi
    
    log_info "Creating KubeBlocks namespace..."
    if ! ensure_namespace "${KUBEBLOCKS_NAMESPACE}"; then
        return 1
    fi
    
    local helm_args=(
        "kubeblocks"
        "${HELM_REPO_NAME}/kubeblocks"
        --namespace "${KUBEBLOCKS_NAMESPACE}"
        --version "${KUBEBLOCKS_VERSION}"
        --wait
        --timeout 600s
    )
    
    if helm_release_exists "${KUBEBLOCKS_NAMESPACE}" "kubeblocks"; then
        log_info "KubeBlocks Helm release exists, upgrading..."
        if ! helm upgrade "${helm_args[@]}" 2>&1 | tee -a "${LOG_FILE}"; then
            log_error "Helm upgrade failed"
            return 1
        fi
    else
        log_info "Installing KubeBlocks..."
        if ! helm install "${helm_args[@]}" 2>&1 | tee -a "${LOG_FILE}"; then
            log_error "Helm install failed"
            return 1
        fi
    fi
    
    log_success "KubeBlocks installed"
    return 0
}

install_addon() {
    log_info "Checking if kbcli is installed..."
    
    if ! command -v kbcli &> /dev/null; then
        log_info "Installing kbcli..."
        if ! curl -fsSL "${KBCLI_INSTALL_URL}" | bash 2>&1 | tee -a "${LOG_FILE}"; then
            log_error "Failed to install kbcli"
            return 1
        fi
        
        # Reload PATH to include kbcli
        export PATH="${PATH}:${HOME}/.kbcli/bin"
        
        if ! command -v kbcli &> /dev/null; then
            log_error "kbcli not found after installation"
            return 1
        fi
    fi
    
    log_info "kbcli version:"
    kbcli version 2>&1 | tee -a "${LOG_FILE}" || true
    
    log_info "Installing graph database addon..."
    if ! kbcli addon install falkordb --version "${ADDON_VERSION}" 2>&1 | tee -a "${LOG_FILE}"; then
        # Check if addon already exists
        if kbcli addon list 2>/dev/null | grep -q "falkordb.*Enabled"; then
            log_info "Graph database addon already installed and enabled"
            return 0
        fi
        log_error "Failed to install graph database addon"
        return 1
    fi
    
    # Wait for addon to be enabled
    log_info "Waiting for graph database addon to be enabled..."
    local timeout=120
    local elapsed=0
    while [[ $elapsed -lt $timeout ]]; do
        if kbcli addon list 2>/dev/null | grep -q "falkordb.*Enabled"; then
            log_success "Graph database addon is enabled"
            return 0
        fi
        sleep 5
        elapsed=$((elapsed + 5))
    done
    
    log_warn "Addon may still be initializing. Continuing..."
    return 0
}

setup_namespace_and_storage() {
    log_info "Creating graph database namespace..."
    
    local namespace_manifest="${SCRIPT_DIR}/00_namespace.yaml"
    if [[ -f "$namespace_manifest" ]]; then
        if ! apply_manifest "$namespace_manifest" "Graph database namespace"; then
            return 1
        fi
    else
        if ! ensure_namespace "${NAMESPACE}"; then
            return 1
        fi
    fi
    
    if ! wait_for_namespace "${NAMESPACE}" 60; then
        return 1
    fi
    
    log_info "Creating storage classes..."
    
    local storage_manifests=(
        "${SCRIPT_DIR}/011_general_storage-class.yaml"
        "${SCRIPT_DIR}/01_storage-claim.yaml"
    )
    
    for manifest in "${storage_manifests[@]}"; do
        if [[ -f "$manifest" ]]; then
            if ! apply_manifest "$manifest" "Storage class"; then
                return 1
            fi
        else
            log_warn "Storage manifest not found: ${manifest}"
        fi
    done
    
    log_success "Namespace and storage classes configured"
    return 0
}

deploy_cluster() {
    local cluster_manifest="${SCRIPT_DIR}/02_kubeblocks.yaml"
    
    if [[ ! -f "$cluster_manifest" ]]; then
        log_error "Cluster manifest not found: ${cluster_manifest}"
        return 1
    fi
    
    log_info "Checking if cluster already exists..."
    if kubectl get cluster "${CLUSTER_NAME}" -n "${NAMESPACE}" &> /dev/null; then
        log_info "Graph database cluster already exists"
        
        local phase
        phase=$(kubectl get cluster "${CLUSTER_NAME}" -n "${NAMESPACE}" -o jsonpath='{.status.phase}' 2>/dev/null || echo "Unknown")
        log_info "Current cluster phase: ${phase}"
        
        if [[ "$phase" == "Running" ]]; then
            log_success "Cluster is already running"
            return 0
        fi
    fi
    
    log_info "Deploying graph database cluster..."
    if ! apply_manifest "$cluster_manifest" "Graph database cluster"; then
        return 1
    fi
    
    log_success "Cluster deployment initiated"
    return 0
}

wait_for_cluster_ready() {
    log_info "Waiting for graph database cluster to be ready (timeout: ${CLUSTER_READY_TIMEOUT}s)..."
    
    local timeout="${CLUSTER_READY_TIMEOUT}"
    local elapsed=0
    local poll_interval=15
    
    while [[ $elapsed -lt $timeout ]]; do
        local phase
        phase=$(kubectl get cluster "${CLUSTER_NAME}" -n "${NAMESPACE}" -o jsonpath='{.status.phase}' 2>/dev/null || echo "Unknown")
        
        log_info "Cluster phase: ${phase} (elapsed: ${elapsed}s)"
        
        case "$phase" in
            "Running")
                log_success "Graph database cluster is running"
                
                # Verify all pods are ready
                log_info "Verifying all cluster pods are ready..."
                if wait_for_pods_ready "${NAMESPACE}" "app.kubernetes.io/instance=${CLUSTER_NAME}" "${POD_READY_TIMEOUT}"; then
                    return 0
                fi
                ;;
            "Failed"|"Abnormal")
                log_error "Cluster is in ${phase} state"
                kubectl describe cluster "${CLUSTER_NAME}" -n "${NAMESPACE}" 2>&1 | tail -30 | tee -a "${LOG_FILE}" || true
                return 1
                ;;
        esac
        
        sleep "$poll_interval"
        elapsed=$((elapsed + poll_interval))
    done
    
    log_error "Timeout waiting for cluster to be ready"
    kubectl get cluster "${CLUSTER_NAME}" -n "${NAMESPACE}" -o yaml 2>&1 | tee -a "${LOG_FILE}" || true
    return 1
}

deploy_exporters() {
    log_info "Deploying monitoring exporters..."
    
    local exporter_manifests=(
        "${SCRIPT_DIR}/03_service-monitor.yaml"
        "${SCRIPT_DIR}/05_redis-exporter.yaml"
        "${SCRIPT_DIR}/06_falkordb-stats-exporter.yaml"
    )
    
    for manifest in "${exporter_manifests[@]}"; do
        if [[ -f "$manifest" ]]; then
            if ! apply_manifest "$manifest" "Exporter"; then
                log_warn "Failed to apply ${manifest}, continuing..."
            fi
        else
            log_warn "Exporter manifest not found: ${manifest}"
        fi
    done
    
    # Wait for exporter pods to be ready
    log_info "Waiting for exporter pods to be ready..."
    sleep 10  # Give time for pods to be scheduled
    
    if ! wait_for_deployment "${NAMESPACE}" "falkordb-exporter" 180; then
        log_warn "Exporter deployment may not be ready yet"
    fi
    
    if ! wait_for_deployment "${NAMESPACE}" "falkordb-stats-exporter" 180; then
        log_warn "Stats exporter deployment may not be ready yet"
    fi
    
    log_success "Exporters deployed"
    return 0
}

configure_tcp_proxy() {
    local tcp_config="${SCRIPT_DIR}/04_tcp-nginx.yaml"
    
    if [[ ! -f "$tcp_config" ]]; then
        log_warn "TCP proxy configuration not found: ${tcp_config}"
        return 0
    fi
    
    log_info "Configuring NGINX TCP proxy for graph database access..."
    if ! apply_manifest "$tcp_config" "NGINX TCP proxy configuration"; then
        log_warn "Failed to apply TCP proxy config. NGINX may not be installed yet."
        return 0
    fi
    
    log_success "TCP proxy configured"
    return 0
}

verify_installation() {
    log_info "Verifying graph database installation..."
    
    log_info "Cluster status:"
    kubectl get cluster -n "${NAMESPACE}" 2>&1 | tee -a "${LOG_FILE}" || true
    
    log_info "Cluster pods:"
    kubectl get pods -n "${NAMESPACE}" -l "app.kubernetes.io/instance=${CLUSTER_NAME}" 2>&1 | tee -a "${LOG_FILE}" || true
    
    log_info "Exporter pods:"
    kubectl get pods -n "${NAMESPACE}" -l "app=falkordb-exporter" 2>&1 | tee -a "${LOG_FILE}" || true
    kubectl get pods -n "${NAMESPACE}" -l "app=falkordb-stats-exporter" 2>&1 | tee -a "${LOG_FILE}" || true
    
    log_info "Services:"
    kubectl get svc -n "${NAMESPACE}" 2>&1 | tee -a "${LOG_FILE}" || true
    
    log_info "PersistentVolumeClaims:"
    kubectl get pvc -n "${NAMESPACE}" 2>&1 | tee -a "${LOG_FILE}" || true
    
    log_success "Graph database verification complete"
}

print_connection_info() {
    echo ""
    log_info "Connection Information:"
    echo "========================================"
    echo "Namespace: ${NAMESPACE}"
    echo "Cluster Name: ${CLUSTER_NAME}"
    echo ""
    echo "Internal Service Endpoint:"
    echo "  Host: ${CLUSTER_NAME}-falkordb-falkordb.${NAMESPACE}.svc.cluster.local"
    echo "  Port: 6379"
    echo ""
    echo "To retrieve the password:"
    echo "  kubectl get secret ${CLUSTER_NAME}-falkordb-account-default -n ${NAMESPACE} -o jsonpath='{.data.password}' | base64 -d"
    echo "========================================"
}

trap cleanup EXIT
main "$@"
