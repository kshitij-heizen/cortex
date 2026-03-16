#!/bin/bash

# Common utilities for Cortex infrastructure deployment
# This script provides logging, validation, and health check functions

set -euo pipefail

# Configuration
readonly LOG_DIR="${LOG_DIR:-/tmp/cortex-deploy-logs}"
readonly LOG_FILE="${LOG_DIR}/deploy-$(date +%Y%m%d-%H%M%S).log"
readonly DEFAULT_TIMEOUT=300
readonly DEFAULT_POLL_INTERVAL=10

# Colors for terminal output
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m'

# Initialize logging
init_logging() {
    mkdir -p "${LOG_DIR}"
    touch "${LOG_FILE}"
    log_info "Logging initialized. Log file: ${LOG_FILE}"
}

# Logging functions
log() {
    local level="$1"
    local message="$2"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${timestamp}] [${level}] ${message}" | tee -a "${LOG_FILE}"
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
    log "INFO" "$1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
    log "SUCCESS" "$1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
    log "WARN" "$1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
    log "ERROR" "$1"
}

log_step() {
    local step_num="$1"
    local step_desc="$2"
    echo ""
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}Step ${step_num}: ${step_desc}${NC}"
    echo -e "${BLUE}========================================${NC}"
    log "STEP" "Step ${step_num}: ${step_desc}"
}

# Check if required commands are available
check_prerequisites() {
    log_info "Checking prerequisites..."
    
    local required_commands=("kubectl" "helm" "curl")
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
    
    log_success "All prerequisites satisfied"
    return 0
}

# Check cluster connectivity
check_cluster_connection() {
    log_info "Checking Kubernetes cluster connection..."
    
    if ! kubectl cluster-info &> /dev/null; then
        log_error "Unable to connect to Kubernetes cluster"
        return 1
    fi
    
    local context
    context=$(kubectl config current-context)
    log_success "Connected to cluster: ${context}"
    return 0
}

# Wait for namespace to be active
wait_for_namespace() {
    local namespace="$1"
    local timeout="${2:-60}"
    local elapsed=0
    
    log_info "Waiting for namespace '${namespace}' to be active..."
    
    while [[ $elapsed -lt $timeout ]]; do
        local phase
        phase=$(kubectl get namespace "$namespace" -o jsonpath='{.status.phase}' 2>/dev/null || echo "NotFound")
        
        if [[ "$phase" == "Active" ]]; then
            log_success "Namespace '${namespace}' is active"
            return 0
        fi
        
        sleep 2
        elapsed=$((elapsed + 2))
    done
    
    log_error "Timeout waiting for namespace '${namespace}' to be active"
    return 1
}

# Wait for pods in a namespace to be ready
wait_for_pods_ready() {
    local namespace="$1"
    local label_selector="${2:-}"
    local timeout="${3:-${DEFAULT_TIMEOUT}}"
    local poll_interval="${4:-${DEFAULT_POLL_INTERVAL}}"
    local elapsed=0
    
    local selector_msg=""
    if [[ -n "$label_selector" ]]; then
        selector_msg=" with selector '${label_selector}'"
    fi
    
    log_info "Waiting for pods in namespace '${namespace}'${selector_msg} to be ready (timeout: ${timeout}s)..."
    
    while [[ $elapsed -lt $timeout ]]; do
        local pods_info
        if [[ -n "$label_selector" ]]; then
            pods_info=$(kubectl get pods -n "$namespace" -l "$label_selector" --no-headers 2>/dev/null || echo "")
        else
            pods_info=$(kubectl get pods -n "$namespace" --no-headers 2>/dev/null || echo "")
        fi
        
        if [[ -z "$pods_info" ]]; then
            log_info "No pods found yet, waiting..."
            sleep "$poll_interval"
            elapsed=$((elapsed + poll_interval))
            continue
        fi
        
        local total_pods ready_pods not_ready_pods
        total_pods=$(echo "$pods_info" | wc -l | tr -d ' ')
        not_ready_pods=$(echo "$pods_info" | grep -v "Running\|Completed" | grep -v "^$" | wc -l | tr -d ' ')
        ready_pods=$((total_pods - not_ready_pods))
        
        # Check if all running pods have all containers ready
        local pods_with_issues=0
        while IFS= read -r line; do
            if [[ -z "$line" ]]; then
                continue
            fi
            local status ready_info
            status=$(echo "$line" | awk '{print $3}')
            ready_info=$(echo "$line" | awk '{print $2}')
            
            if [[ "$status" == "Running" ]]; then
                local ready total
                ready=$(echo "$ready_info" | cut -d'/' -f1)
                total=$(echo "$ready_info" | cut -d'/' -f2)
                if [[ "$ready" != "$total" ]]; then
                    pods_with_issues=$((pods_with_issues + 1))
                fi
            elif [[ "$status" != "Completed" ]]; then
                pods_with_issues=$((pods_with_issues + 1))
            fi
        done <<< "$pods_info"
        
        log_info "Pod status: ${ready_pods}/${total_pods} ready, ${pods_with_issues} with issues (elapsed: ${elapsed}s)"
        
        if [[ $pods_with_issues -eq 0 && $total_pods -gt 0 ]]; then
            log_success "All ${total_pods} pods are ready in namespace '${namespace}'"
            return 0
        fi
        
        sleep "$poll_interval"
        elapsed=$((elapsed + poll_interval))
    done
    
    log_error "Timeout waiting for pods in namespace '${namespace}'"
    log_error "Current pod status:"
    if [[ -n "$label_selector" ]]; then
        kubectl get pods -n "$namespace" -l "$label_selector" 2>/dev/null | tee -a "${LOG_FILE}" || true
    else
        kubectl get pods -n "$namespace" 2>/dev/null | tee -a "${LOG_FILE}" || true
    fi
    return 1
}

# Wait for a specific deployment to be ready
wait_for_deployment() {
    local namespace="$1"
    local deployment_name="$2"
    local timeout="${3:-${DEFAULT_TIMEOUT}}"
    
    log_info "Waiting for deployment '${deployment_name}' in namespace '${namespace}' to be ready..."
    
    if ! kubectl wait --for=condition=available deployment/"$deployment_name" \
        -n "$namespace" --timeout="${timeout}s" 2>/dev/null; then
        log_error "Deployment '${deployment_name}' did not become ready within ${timeout}s"
        kubectl describe deployment "$deployment_name" -n "$namespace" 2>/dev/null | tail -20 | tee -a "${LOG_FILE}" || true
        return 1
    fi
    
    log_success "Deployment '${deployment_name}' is ready"
    return 0
}

# Wait for a statefulset to be ready
wait_for_statefulset() {
    local namespace="$1"
    local statefulset_name="$2"
    local timeout="${3:-${DEFAULT_TIMEOUT}}"
    local poll_interval="${4:-${DEFAULT_POLL_INTERVAL}}"
    local elapsed=0
    
    log_info "Waiting for statefulset '${statefulset_name}' in namespace '${namespace}' to be ready..."
    
    while [[ $elapsed -lt $timeout ]]; do
        local ready_replicas desired_replicas
        ready_replicas=$(kubectl get statefulset "$statefulset_name" -n "$namespace" \
            -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
        desired_replicas=$(kubectl get statefulset "$statefulset_name" -n "$namespace" \
            -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "0")
        
        ready_replicas="${ready_replicas:-0}"
        
        log_info "StatefulSet '${statefulset_name}': ${ready_replicas}/${desired_replicas} replicas ready (elapsed: ${elapsed}s)"
        
        if [[ "$ready_replicas" == "$desired_replicas" && "$desired_replicas" != "0" ]]; then
            log_success "StatefulSet '${statefulset_name}' is ready with ${ready_replicas} replicas"
            return 0
        fi
        
        sleep "$poll_interval"
        elapsed=$((elapsed + poll_interval))
    done
    
    log_error "Timeout waiting for statefulset '${statefulset_name}'"
    kubectl describe statefulset "$statefulset_name" -n "$namespace" 2>/dev/null | tail -20 | tee -a "${LOG_FILE}" || true
    return 1
}

# Wait for helm release to be deployed
wait_for_helm_release() {
    local namespace="$1"
    local release_name="$2"
    local timeout="${3:-${DEFAULT_TIMEOUT}}"
    local poll_interval="${4:-${DEFAULT_POLL_INTERVAL}}"
    local elapsed=0
    
    log_info "Waiting for helm release '${release_name}' in namespace '${namespace}' to be deployed..."
    
    while [[ $elapsed -lt $timeout ]]; do
        local status
        status=$(helm status "$release_name" -n "$namespace" -o json 2>/dev/null | grep -o '"status":"[^"]*"' | cut -d'"' -f4 || echo "not-found")
        
        if [[ "$status" == "deployed" ]]; then
            log_success "Helm release '${release_name}' is deployed"
            return 0
        elif [[ "$status" == "failed" ]]; then
            log_error "Helm release '${release_name}' failed"
            helm status "$release_name" -n "$namespace" 2>/dev/null | tee -a "${LOG_FILE}" || true
            return 1
        fi
        
        log_info "Helm release status: ${status} (elapsed: ${elapsed}s)"
        sleep "$poll_interval"
        elapsed=$((elapsed + poll_interval))
    done
    
    log_error "Timeout waiting for helm release '${release_name}'"
    return 1
}

# Check if a CRD exists
check_crd_exists() {
    local crd_name="$1"
    
    if kubectl get crd "$crd_name" &> /dev/null; then
        return 0
    fi
    return 1
}

# Wait for CRD to be established
wait_for_crd() {
    local crd_name="$1"
    local timeout="${2:-120}"
    local elapsed=0
    
    log_info "Waiting for CRD '${crd_name}' to be established..."
    
    while [[ $elapsed -lt $timeout ]]; do
        if kubectl wait --for=condition=established crd/"$crd_name" --timeout=5s &> /dev/null; then
            log_success "CRD '${crd_name}' is established"
            return 0
        fi
        sleep 5
        elapsed=$((elapsed + 5))
    done
    
    log_error "Timeout waiting for CRD '${crd_name}'"
    return 1
}

# Apply a Kubernetes manifest with logging
apply_manifest() {
    local manifest_path="$1"
    local description="${2:-}"
    
    if [[ ! -f "$manifest_path" ]]; then
        log_error "Manifest file not found: ${manifest_path}"
        return 1
    fi
    
    local msg="Applying manifest: ${manifest_path}"
    if [[ -n "$description" ]]; then
        msg="${description} (${manifest_path})"
    fi
    
    log_info "$msg"
    
    if ! kubectl apply -f "$manifest_path" 2>&1 | tee -a "${LOG_FILE}"; then
        log_error "Failed to apply manifest: ${manifest_path}"
        return 1
    fi
    
    log_success "Successfully applied: ${manifest_path}"
    return 0
}

# Check if a namespace exists
namespace_exists() {
    local namespace="$1"
    kubectl get namespace "$namespace" &> /dev/null
}

# Create namespace if it doesn't exist
ensure_namespace() {
    local namespace="$1"
    
    if namespace_exists "$namespace"; then
        log_info "Namespace '${namespace}' already exists"
        return 0
    fi
    
    log_info "Creating namespace '${namespace}'..."
    if ! kubectl create namespace "$namespace" 2>&1 | tee -a "${LOG_FILE}"; then
        log_error "Failed to create namespace '${namespace}'"
        return 1
    fi
    
    log_success "Namespace '${namespace}' created"
    return 0
}

# Check if helm repo exists
helm_repo_exists() {
    local repo_name="$1"
    helm repo list 2>/dev/null | grep -q "^${repo_name}[[:space:]]"
}

# Add helm repo if it doesn't exist
ensure_helm_repo() {
    local repo_name="$1"
    local repo_url="$2"
    
    if helm_repo_exists "$repo_name"; then
        log_info "Helm repo '${repo_name}' already exists"
    else
        log_info "Adding helm repo '${repo_name}' from ${repo_url}..."
        if ! helm repo add "$repo_name" "$repo_url" 2>&1 | tee -a "${LOG_FILE}"; then
            log_error "Failed to add helm repo '${repo_name}'"
            return 1
        fi
        log_success "Helm repo '${repo_name}' added"
    fi
    
    log_info "Updating helm repos..."
    helm repo update 2>&1 | tee -a "${LOG_FILE}"
    return 0
}

# Check if helm release exists
helm_release_exists() {
    local namespace="$1"
    local release_name="$2"
    helm status "$release_name" -n "$namespace" &> /dev/null
}

# Print summary
print_summary() {
    local status="$1"
    local component="$2"
    
    echo ""
    echo "========================================"
    if [[ "$status" == "success" ]]; then
        echo -e "${GREEN}${component} deployment completed successfully${NC}"
    else
        echo -e "${RED}${component} deployment failed${NC}"
        echo "Check the log file for details: ${LOG_FILE}"
    fi
    echo "========================================"
    echo ""
}

# Cleanup function for traps
cleanup() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        log_error "Script exited with error code: ${exit_code}"
    fi
    exit $exit_code
}

# Export functions for use in other scripts
export -f log log_info log_success log_warn log_error log_step
export -f check_prerequisites check_cluster_connection
export -f wait_for_namespace wait_for_pods_ready wait_for_deployment wait_for_statefulset
export -f wait_for_helm_release wait_for_crd check_crd_exists
export -f apply_manifest namespace_exists ensure_namespace
export -f helm_repo_exists ensure_helm_repo helm_release_exists
export -f print_summary cleanup init_logging
export LOG_FILE LOG_DIR DEFAULT_TIMEOUT DEFAULT_POLL_INTERVAL
export RED GREEN YELLOW BLUE NC
