#!/bin/bash

# Cortex Production Infrastructure Deployment Script
# Orchestrates the deployment of all infrastructure components

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source common utilities
source "${SCRIPT_DIR}/scripts/common.sh"

# Configuration
readonly DEPLOYMENT_NAME="Cortex Production Infrastructure"
readonly COMPONENTS=("nginx" "graphdb" "monitoring" "cortex-app" "cortex-ingestion")
readonly START_TIME=$(date +%s)

# Component directories
readonly NGINX_DIR="${SCRIPT_DIR}/nginx"
readonly GRAPHDB_DIR="${SCRIPT_DIR}/falkordb"
readonly MONITORING_DIR="${SCRIPT_DIR}/monitoring"
readonly CORTEX_APP_DIR="${SCRIPT_DIR}/cortex-app"
readonly CORTEX_INGESTION_DIR="${SCRIPT_DIR}/cortex-ingestion"

# Deployment state
declare -A COMPONENT_STATUS

usage() {
    cat << EOF
Usage: $(basename "$0") [OPTIONS] [COMPONENTS...]

Deploy Cortex production infrastructure components.

OPTIONS:
    -h, --help          Show this help message
    -l, --list          List available components
    -s, --skip-confirm  Skip confirmation prompt
    --dry-run           Validate configuration without deploying

COMPONENTS:
    nginx            NGINX Ingress Controller with NLB
    graphdb          Graph Database cluster (KubeBlocks)
    monitoring       Prometheus and Grafana monitoring stack
    cortex-app       Cortex FastAPI Application
    cortex-ingestion Cortex Ingestion Service (Kafka consumer)
    all              Deploy all components (default)

EXAMPLES:
    $(basename "$0")                    Deploy all components
    $(basename "$0") nginx monitoring   Deploy only nginx and monitoring
    $(basename "$0") --dry-run          Validate without deploying

EOF
}

list_components() {
    echo "Available components:"
    echo ""
    echo "  nginx            - NGINX Ingress Controller with AWS NLB"
    echo "  graphdb          - Graph Database cluster using KubeBlocks"
    echo "  monitoring       - Prometheus and Grafana monitoring stack"
    echo "  cortex-app       - Cortex FastAPI Application"
    echo "  cortex-ingestion - Cortex Ingestion Service (Kafka consumer)"
    echo ""
}

validate_environment() {
    log_step "ENV" "Validating deployment environment"
    
    log_info "Checking prerequisites..."
    if ! check_prerequisites; then
        log_error "Prerequisites check failed"
        return 1
    fi
    
    log_info "Checking cluster connection..."
    if ! check_cluster_connection; then
        log_error "Cluster connection check failed"
        return 1
    fi
    
    log_info "Validating component scripts..."
    local scripts=(
        "${NGINX_DIR}/install.sh"
        "${GRAPHDB_DIR}/install.sh"
        "${MONITORING_DIR}/install.sh"
        "${CORTEX_APP_DIR}/install.sh"
        "${CORTEX_INGESTION_DIR}/install.sh"
    )
    
    for script in "${scripts[@]}"; do
        if [[ ! -f "$script" ]]; then
            log_error "Component script not found: ${script}"
            return 1
        fi
        if [[ ! -x "$script" ]]; then
            log_warn "Script not executable, fixing: ${script}"
            chmod +x "$script"
        fi
    done
    
    log_success "Environment validation passed"
    return 0
}

confirm_deployment() {
    local components=("$@")
    
    echo ""
    echo "========================================"
    echo "Deployment Configuration"
    echo "========================================"
    echo ""
    echo "Cluster: $(kubectl config current-context)"
    echo "Components to deploy:"
    for component in "${components[@]}"; do
        echo "  - ${component}"
    done
    echo ""
    echo "Log directory: ${LOG_DIR}"
    echo ""
    
    read -r -p "Proceed with deployment? [y/N]: " response
    case "$response" in
        [yY][eE][sS]|[yY])
            return 0
            ;;
        *)
            log_info "Deployment cancelled by user"
            return 1
            ;;
    esac
}

deploy_nginx() {
    log_step "NGINX" "Deploying NGINX Ingress Controller"
    
    local install_script="${NGINX_DIR}/install.sh"
    
    if [[ ! -x "$install_script" ]]; then
        chmod +x "$install_script"
    fi
    
    if ! "${install_script}"; then
        log_error "NGINX deployment failed"
        COMPONENT_STATUS["nginx"]="FAILED"
        return 1
    fi
    
    COMPONENT_STATUS["nginx"]="SUCCESS"
    log_success "NGINX Ingress Controller deployed successfully"
    return 0
}

deploy_graphdb() {
    log_step "GRAPHDB" "Deploying Graph Database"
    
    local install_script="${GRAPHDB_DIR}/install.sh"
    
    if [[ ! -x "$install_script" ]]; then
        chmod +x "$install_script"
    fi
    
    if ! "${install_script}"; then
        log_error "Graph Database deployment failed"
        COMPONENT_STATUS["graphdb"]="FAILED"
        return 1
    fi
    
    COMPONENT_STATUS["graphdb"]="SUCCESS"
    log_success "Graph Database deployed successfully"
    return 0
}

deploy_monitoring() {
    log_step "MONITORING" "Deploying Monitoring Stack"
    
    local install_script="${MONITORING_DIR}/install.sh"
    
    if [[ ! -x "$install_script" ]]; then
        chmod +x "$install_script"
    fi
    
    if ! "${install_script}"; then
        log_error "Monitoring stack deployment failed"
        COMPONENT_STATUS["monitoring"]="FAILED"
        return 1
    fi
    
    COMPONENT_STATUS["monitoring"]="SUCCESS"
    log_success "Monitoring stack deployed successfully"
    return 0
}

deploy_cortex_app() {
    log_step "CORTEX-APP" "Deploying Cortex Application"
    
    local install_script="${CORTEX_APP_DIR}/install.sh"
    
    if [[ ! -x "$install_script" ]]; then
        chmod +x "$install_script"
    fi
    
    if ! "${install_script}"; then
        log_error "Cortex Application deployment failed"
        COMPONENT_STATUS["cortex-app"]="FAILED"
        return 1
    fi
    
    COMPONENT_STATUS["cortex-app"]="SUCCESS"
    log_success "Cortex Application deployed successfully"
    return 0
}

deploy_cortex_ingestion() {
    log_step "CORTEX-INGESTION" "Deploying Cortex Ingestion Service"
    
    local install_script="${CORTEX_INGESTION_DIR}/install.sh"
    
    if [[ ! -x "$install_script" ]]; then
        chmod +x "$install_script"
    fi
    
    if ! "${install_script}"; then
        log_error "Cortex Ingestion Service deployment failed"
        COMPONENT_STATUS["cortex-ingestion"]="FAILED"
        return 1
    fi
    
    COMPONENT_STATUS["cortex-ingestion"]="SUCCESS"
    log_success "Cortex Ingestion Service deployed successfully"
    return 0
}

deploy_component() {
    local component="$1"
    
    case "$component" in
        nginx)
            deploy_nginx
            ;;
        graphdb)
            deploy_graphdb
            ;;
        monitoring)
            deploy_monitoring
            ;;
        cortex-app)
            deploy_cortex_app
            ;;
        cortex-ingestion)
            deploy_cortex_ingestion
            ;;
        *)
            log_error "Unknown component: ${component}"
            return 1
            ;;
    esac
}

print_deployment_summary() {
    local end_time
    end_time=$(date +%s)
    local duration=$((end_time - START_TIME))
    local minutes=$((duration / 60))
    local seconds=$((duration % 60))
    
    echo ""
    echo "========================================"
    echo "Deployment Summary"
    echo "========================================"
    echo ""
    echo "Duration: ${minutes}m ${seconds}s"
    echo ""
    echo "Component Status:"
    
    local all_success=true
    for component in "${!COMPONENT_STATUS[@]}"; do
        local status="${COMPONENT_STATUS[$component]}"
        if [[ "$status" == "SUCCESS" ]]; then
            echo -e "  ${GREEN}[SUCCESS]${NC} ${component}"
        else
            echo -e "  ${RED}[FAILED]${NC} ${component}"
            all_success=false
        fi
    done
    
    echo ""
    echo "Log file: ${LOG_FILE}"
    echo ""
    
    if [[ "$all_success" == true ]]; then
        echo -e "${GREEN}========================================"
        echo "All components deployed successfully"
        echo -e "========================================${NC}"
    else
        echo -e "${RED}========================================"
        echo "Some components failed to deploy"
        echo "Check the log file for details"
        echo -e "========================================${NC}"
        return 1
    fi
    
    return 0
}

print_post_deployment_info() {
    echo ""
    echo "========================================"
    echo "Post-Deployment Information"
    echo "========================================"
    echo ""
    echo "1. DNS Configuration:"
    echo "   Get the NLB hostname:"
    echo "   kubectl get svc ingress-nginx-controller -n ingress-nginx -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'"
    echo ""
    echo "   Configure DNS records to point to this NLB:"
    echo "   - api.usecortex.ai"
    echo "   - ingestion.usecortex.ai"
    echo "   - grafana-prod.usecortex.ai"
    echo "   - prometheus-prod.usecortex.ai"
    echo ""
    echo "2. Access Credentials:"
    echo ""
    echo "   Cortex API:"
    echo "   - URL: https://api.usecortex.ai"
    echo "   - Internal: http://cortex-app.cortex-app.svc.cluster.local:80"
    echo ""
    echo "   Cortex Ingestion:"
    echo "   - URL: https://ingestion.usecortex.ai"
    echo "   - Internal: http://cortex-ingestion.cortex-ingestion.svc.cluster.local:80"
    echo ""
    echo "   Grafana:"
    echo "   - URL: https://grafana-prod.usecortex.ai"
    echo "   - Username: admin"
    echo "   - Password: kubectl get secret monitoring-grafana -n monitoring -o jsonpath='{.data.admin-password}' | base64 -d"
    echo ""
    echo "   Graph Database:"
    echo "   - Internal Host: falkordb-prod-falkordb-falkordb.falkordb.svc.cluster.local:6379"
    echo "   - Password: kubectl get secret falkordb-prod-falkordb-account-default -n falkordb -o jsonpath='{.data.password}' | base64 -d"
    echo ""
    echo "3. Verification Commands:"
    echo "   kubectl get pods -A | grep -E 'ingress-nginx|falkordb|monitoring|cortex-app|cortex-ingestion'"
    echo "   kubectl get svc -A | grep -E 'ingress-nginx|falkordb|monitoring|cortex-app|cortex-ingestion'"
    echo "   kubectl get ingress -A"
    echo ""
    echo "========================================"
}

dry_run() {
    log_info "Performing dry run validation..."
    
    if ! validate_environment; then
        log_error "Dry run validation failed"
        return 1
    fi
    
    log_info "Validating YAML manifests..."
    
    local manifests=(
        "${NGINX_DIR}/nlb.yaml"
        "${GRAPHDB_DIR}/00_namespace.yaml"
        "${GRAPHDB_DIR}/011_general_storage-class.yaml"
        "${GRAPHDB_DIR}/01_storage-claim.yaml"
        "${GRAPHDB_DIR}/02_kubeblocks.yaml"
        "${GRAPHDB_DIR}/03_service-monitor.yaml"
        "${GRAPHDB_DIR}/04_tcp-nginx.yaml"
        "${GRAPHDB_DIR}/05_redis-exporter.yaml"
        "${GRAPHDB_DIR}/06_falkordb-stats-exporter.yaml"
        "${MONITORING_DIR}/monitoring-values.yaml"
        "${MONITORING_DIR}/grafana-ingress.yaml"
        "${MONITORING_DIR}/prometheus-ingress.yaml"
        "${CORTEX_APP_DIR}/00_namespace.yaml"
        "${CORTEX_APP_DIR}/01_configmap.yaml"
        "${CORTEX_APP_DIR}/04_service.yaml"
        "${CORTEX_APP_DIR}/05_ingress.yaml"
        "${CORTEX_APP_DIR}/06_hpa.yaml"
        "${CORTEX_APP_DIR}/07_servicemonitor.yaml"
        "${CORTEX_APP_DIR}/08_pdb.yaml"
        "${CORTEX_INGESTION_DIR}/00_namespace.yaml"
        "${CORTEX_INGESTION_DIR}/01_configmap.yaml"
        "${CORTEX_INGESTION_DIR}/04_service.yaml"
        "${CORTEX_INGESTION_DIR}/05_ingress.yaml"
        "${CORTEX_INGESTION_DIR}/06_hpa.yaml"
        "${CORTEX_INGESTION_DIR}/07_servicemonitor.yaml"
        "${CORTEX_INGESTION_DIR}/08_pdb.yaml"
    )
    
    local validation_passed=true
    for manifest in "${manifests[@]}"; do
        if [[ -f "$manifest" ]]; then
            if kubectl apply --dry-run=client -f "$manifest" &> /dev/null; then
                log_info "Valid: ${manifest}"
            else
                log_error "Invalid: ${manifest}"
                validation_passed=false
            fi
        else
            log_warn "Not found: ${manifest}"
        fi
    done
    
    if [[ "$validation_passed" == true ]]; then
        log_success "Dry run validation passed. Ready to deploy."
        return 0
    else
        log_error "Dry run validation failed. Fix errors before deploying."
        return 1
    fi
}

main() {
    local skip_confirm=false
    local perform_dry_run=false
    local components_to_deploy=()
    
    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -h|--help)
                usage
                exit 0
                ;;
            -l|--list)
                list_components
                exit 0
                ;;
            -s|--skip-confirm)
                skip_confirm=true
                shift
                ;;
            --dry-run)
                perform_dry_run=true
                shift
                ;;
            all)
                components_to_deploy=("nginx" "graphdb" "monitoring" "cortex-app" "cortex-ingestion")
                shift
                ;;
            nginx|graphdb|monitoring|cortex-app|cortex-ingestion)
                components_to_deploy+=("$1")
                shift
                ;;
            *)
                log_error "Unknown option or component: $1"
                usage
                exit 1
                ;;
        esac
    done
    
    # Default to all components if none specified
    if [[ ${#components_to_deploy[@]} -eq 0 ]]; then
        components_to_deploy=("nginx" "graphdb" "monitoring" "cortex-app" "cortex-ingestion")
    fi
    
    # Remove duplicates while preserving order
    local unique_components=()
    declare -A seen
    for component in "${components_to_deploy[@]}"; do
        if [[ -z "${seen[$component]:-}" ]]; then
            unique_components+=("$component")
            seen[$component]=1
        fi
    done
    components_to_deploy=("${unique_components[@]}")
    
    echo ""
    echo "========================================"
    echo "${DEPLOYMENT_NAME}"
    echo "========================================"
    echo ""
    
    # Initialize logging
    init_logging
    log_info "Starting deployment process"
    log_info "Log file: ${LOG_FILE}"
    
    # Dry run mode
    if [[ "$perform_dry_run" == true ]]; then
        dry_run
        exit $?
    fi
    
    # Validate environment
    if ! validate_environment; then
        log_error "Environment validation failed"
        exit 1
    fi
    
    # Confirm deployment
    if [[ "$skip_confirm" == false ]]; then
        if ! confirm_deployment "${components_to_deploy[@]}"; then
            exit 0
        fi
    fi
    
    # Deploy components in order
    local deployment_failed=false
    
    for component in "${components_to_deploy[@]}"; do
        log_info "Deploying component: ${component}"
        
        if ! deploy_component "$component"; then
            deployment_failed=true
            log_error "Component deployment failed: ${component}"
            
            # Ask whether to continue with remaining components
            if [[ ${#components_to_deploy[@]} -gt 1 ]]; then
                echo ""
                read -r -p "Continue with remaining components? [y/N]: " response
                case "$response" in
                    [yY][eE][sS]|[yY])
                        log_warn "Continuing with remaining components..."
                        ;;
                    *)
                        log_info "Stopping deployment"
                        break
                        ;;
                esac
            fi
        fi
        
        echo ""
    done
    
    # Print summary
    print_deployment_summary
    
    if [[ "$deployment_failed" == false ]]; then
        print_post_deployment_info
        exit 0
    else
        exit 1
    fi
}

trap cleanup EXIT
main "$@"
