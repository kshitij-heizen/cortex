#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VALUES_FILE=""
AUTO_CONFIRM=false
START_FROM=""
STEPS=("eks" "karpenter" "monitoring" "nginx" "argocd")

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

print_header() {
    echo -e "\n${CYAN}============================================================${NC}"
    echo -e "${CYAN}$1${NC}"
    echo -e "${CYAN}============================================================${NC}\n"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_info() {
    echo -e "${YELLOW}ℹ $1${NC}"
}

check_python() {
    if ! command -v python3 &> /dev/null; then
        print_error "python3 is not installed"
        exit 1
    fi
    print_success "Python 3 is available"
}

install_dependencies() {
    print_info "Checking Python dependencies..."
    
    if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
        pip3 install -q -r "$SCRIPT_DIR/requirements.txt"
        print_success "Python dependencies installed"
    else
        print_info "No requirements.txt found, skipping dependency installation"
    fi
}

deploy_eks_cluster() {
    print_header "Step 1: EKS Cluster Setup"
    
    if [ ! -f "$SCRIPT_DIR/cluster/eks.py" ]; then
        print_error "EKS setup script not found: $SCRIPT_DIR/cluster/eks.py"
        exit 1
    fi
    
    cd "$SCRIPT_DIR/cluster"
    
    if [ "$AUTO_CONFIRM" = true ]; then
        python3 eks.py --values "$VALUES_FILE" -y
    else
        python3 eks.py --values "$VALUES_FILE"
    fi
    
    if [ $? -eq 0 ]; then
        print_success "EKS cluster setup completed"
    else
        print_error "EKS cluster setup failed"
        exit 1
    fi
    
    cd "$SCRIPT_DIR"
}

deploy_karpenter() {
    print_header "Step 2: Karpenter Installation"
    
    if ! check_cluster_exists; then
        print_error "No Kubernetes cluster connection found."
        print_info "Please run 'eks' step first or configure kubectl context."
        exit 1
    fi
    
    if [ ! -f "$SCRIPT_DIR/karpenter.py" ]; then
        print_error "Karpenter setup script not found: $SCRIPT_DIR/karpenter.py"
        exit 1
    fi
    
    if [ "$AUTO_CONFIRM" = true ]; then
        python3 "$SCRIPT_DIR/karpenter.py" -y
    else
        python3 "$SCRIPT_DIR/karpenter.py"
    fi
    
    if [ $? -eq 0 ]; then
        print_success "Karpenter installation completed"
    else
        print_error "Karpenter installation failed"
        exit 1
    fi
}

deploy_nginx() {
    print_header "Step 4: Nginx Ingress Controllers"
    
    if ! check_cluster_exists; then
        print_error "No Kubernetes cluster connection found."
        print_info "Please run 'eks' step first or configure kubectl context."
        exit 1
    fi
    
    local nginx_dir="$SCRIPT_DIR/../nginx"
    
    # Install Community NGINX Ingress Controller
    print_info "Installing Community NGINX Ingress Controller..."
    if [ -f "$nginx_dir/community-version-install.sh" ]; then
        if bash "$nginx_dir/community-version-install.sh"; then
            print_success "Community NGINX Ingress Controller installed"
        else
            print_error "Community NGINX Ingress Controller installation failed"
            exit 1
        fi
    else
        print_error "Community NGINX install script not found: $nginx_dir/community-version-install.sh"
        exit 1
    fi
    
    # Install NGINX Inc Ingress Controller (for TLS passthrough/SNI routing)
    print_info "Installing NGINX Inc Ingress Controller..."
    if [ -f "$nginx_dir/nginx-inc-install.sh" ]; then
        if bash "$nginx_dir/nginx-inc-install.sh"; then
            print_success "NGINX Inc Ingress Controller installed"
        else
            print_error "NGINX Inc Ingress Controller installation failed"
            exit 1
        fi
    else
        print_error "NGINX Inc install script not found: $nginx_dir/nginx-inc-install.sh"
        exit 1
    fi
    
    print_success "All Nginx Ingress Controllers installed successfully"
    print_info "Available IngressClasses:"
    echo "  - nginx (Community version)"
    echo "  - nginx-inc (NGINX Inc version with TLS passthrough)"
}

deploy_monitoring() {
    print_header "Step 3: Monitoring Stack (Prometheus & Grafana)"
    
    if ! check_cluster_exists; then
        print_error "No Kubernetes cluster connection found."
        print_info "Please run 'eks' step first or configure kubectl context."
        exit 1
    fi
    
    local monitoring_dir="$SCRIPT_DIR/../monitoring"
    
    print_info "Installing Prometheus & Grafana monitoring stack..."
    if [ -f "$monitoring_dir/install.sh" ]; then
        if bash "$monitoring_dir/install.sh"; then
            print_success "Monitoring stack installed"
        else
            print_error "Monitoring stack installation failed"
            exit 1
        fi
    else
        print_error "Monitoring install script not found: $monitoring_dir/install.sh"
        exit 1
    fi
}

deploy_argocd() {
    print_header "Step 5: ArgoCD"
    print_info "ArgoCD installation not yet implemented"
}

# Alias for backward compatibility
deploy_prometheus() {
    deploy_monitoring
}

check_cluster_exists() {
    if kubectl cluster-info &> /dev/null; then
        return 0
    fi
    return 1
}

check_karpenter_exists() {
    if kubectl get deployment -n karpenter karpenter &> /dev/null; then
        return 0
    fi
    return 1
}

get_step_index() {
    local step=$1
    for i in "${!STEPS[@]}"; do
        if [ "${STEPS[$i]}" = "$step" ]; then
            echo $i
            return
        fi
    done
    echo -1
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            -f|--values)
                VALUES_FILE="$2"
                shift 2
                ;;
            -y|--yes)
                AUTO_CONFIRM=true
                shift
                ;;
            --start-from)
                START_FROM="$2"
                shift 2
                ;;
            all|eks|karpenter|monitoring|nginx|prometheus|argocd|help|--help|-h)
                COMMAND="$1"
                shift
                ;;
            *)
                print_error "Unknown option: $1"
                show_usage
                exit 1
                ;;
        esac
    done
}

validate_values_file() {
    if [ -z "$VALUES_FILE" ]; then
        print_error "Values file is required. Use -f or --values to specify."
        show_usage
        exit 1
    fi
    
    if [ ! -f "$VALUES_FILE" ]; then
        print_error "Values file not found: $VALUES_FILE"
        exit 1
    fi
    
    VALUES_FILE="$(cd "$(dirname "$VALUES_FILE")" && pwd)/$(basename "$VALUES_FILE")"
    print_success "Using values file: $VALUES_FILE"
}

show_usage() {
    cat << EOF
Usage: $0 -f <values-file> [OPTIONS] [COMMAND]

Cortex Production Deployment Script

Required:
    -f, --values      Path to your values YAML file (e.g., cortex.values.yaml)

Options:
    -y, --yes         Skip all confirmation prompts (auto-confirm)
    --start-from      Start from a specific step (eks, karpenter, monitoring, nginx, argocd)

Commands:
    all               Deploy everything [default]
    eks               Deploy only EKS cluster
    karpenter         Deploy only Karpenter (requires existing cluster)
    monitoring        Deploy only Prometheus/Grafana monitoring stack
    nginx             Deploy only Nginx Ingress Controllers
    argocd            Deploy only ArgoCD (coming soon)
    help              Show this help message

Examples:
    $0 -f cortex.values.yaml all                    # Full deployment with prompts
    $0 -f cortex.values.yaml -y all                 # Full deployment, no prompts
    $0 -f values.yaml --start-from karpenter        # Resume from Karpenter step
    $0 -f values.yaml karpenter                     # Run only Karpenter step
    $0 -f values.yaml -y --start-from nginx         # Resume from Nginx, no prompts

EOF
}

run_step() {
    local step=$1
    case "$step" in
        eks)
            deploy_eks_cluster
            ;;
        karpenter)
            deploy_karpenter
            ;;
        nginx)
            deploy_nginx
            ;;
        monitoring)
            deploy_monitoring
            ;;
        argocd)
            deploy_argocd
            ;;
    esac
}

run_from_step() {
    local start_step=$1
    local start_index=$(get_step_index "$start_step")
    
    if [ "$start_index" = "-1" ]; then
        print_error "Unknown step: $start_step"
        print_info "Valid steps: ${STEPS[*]}"
        exit 1
    fi
    
    print_info "Starting from step: $start_step"
    
    if [ "$start_index" -gt 0 ]; then
        print_info "Skipping: ${STEPS[*]:0:$start_index}"
        
        if ! check_cluster_exists; then
            print_error "No cluster connection found but skipping EKS step."
            print_info "Please ensure kubectl is configured for your cluster."
            exit 1
        fi
        print_success "Cluster connection verified"
    fi
    
    for i in $(seq $start_index $((${#STEPS[@]} - 1))); do
        run_step "${STEPS[$i]}"
        
        if [ $i -lt $((${#STEPS[@]} - 1)) ] && [ "$AUTO_CONFIRM" != true ]; then
            echo ""
            read -p "$(echo -e ${YELLOW}Press Enter to continue to next step...${NC})"
        fi
    done
}

main() {
    COMMAND="all"
    
    if [ $# -eq 0 ]; then
        show_usage
        exit 1
    fi
    
    parse_args "$@"
    
    if [ "$COMMAND" = "help" ]; then
        show_usage
        exit 0
    fi
    
    print_header "Cortex Production Deployment"
    
    if [ "$AUTO_CONFIRM" = true ]; then
        print_info "Auto-confirm mode enabled (-y)"
    fi
    
    validate_values_file
    check_python
    install_dependencies
    
    export VALUES_FILE
    export AUTO_CONFIRM
    
    if [ -n "$START_FROM" ]; then
        run_from_step "$START_FROM"
    else
        case "$COMMAND" in
            all)
                run_from_step "eks"
                ;;
            *)
                run_step "$COMMAND"
                ;;
        esac
    fi
    
    print_header "Deployment Complete"
    print_success "All components deployed successfully!"
    print_info "Next steps:"
    echo "  1. Verify cluster: kubectl get nodes"
    echo "  2. Check Karpenter: kubectl get nodepools"
    echo "  3. Check Monitoring: kubectl get pods -n monitoring"
    echo "  4. Check Nginx (Community): kubectl get svc -n ingress-nginx"
    echo "  5. Check Nginx (Inc): kubectl get svc -n nginx-inc"
}

main "$@"
