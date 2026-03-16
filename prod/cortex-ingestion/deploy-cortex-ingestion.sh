#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROD_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$(dirname "$PROD_DIR")")"
APP_DIR="${PROJECT_ROOT}/cortex-ingestion"

readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m'

readonly LOG_DIR="${LOG_DIR:-/tmp/cortex-deploy-logs}"
readonly LOG_FILE="${LOG_DIR}/cortex-ingestion-deploy-$(date +%Y%m%d-%H%M%S).log"

log_info() { echo -e "${BLUE}[INFO]${NC} $1" | tee -a "${LOG_FILE}"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1" | tee -a "${LOG_FILE}"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1" | tee -a "${LOG_FILE}"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2 | tee -a "${LOG_FILE}"; }
log_step() {
    echo "" | tee -a "${LOG_FILE}"
    echo -e "${BLUE}========================================${NC}" | tee -a "${LOG_FILE}"
    echo -e "${BLUE}Step $1: $2${NC}" | tee -a "${LOG_FILE}"
    echo -e "${BLUE}========================================${NC}" | tee -a "${LOG_FILE}"
}

usage() {
    cat << EOF
Usage: $(basename "$0") [OPTIONS]

Deploy Cortex Ingestion Service to EKS cluster.

OPTIONS:
    -h, --help              Show this help message
    -e, --env FILE          Path to environment file (default: secrets.env)
    -t, --tag TAG           Docker image tag (default: git commit SHA)
    -r, --registry REGISTRY ECR registry URL (required)
    --skip-build            Skip Docker build and push
    --skip-confirm          Skip confirmation prompt
    --dry-run               Validate without deploying

REQUIRED ENVIRONMENT:
    AWS_REGION              AWS region (default: us-east-1)
    ECR_REGISTRY            ECR registry URL (e.g., 123456789.dkr.ecr.us-east-1.amazonaws.com)

EXAMPLES:
    $(basename "$0") -r 123456789.dkr.ecr.us-east-1.amazonaws.com
    $(basename "$0") -r 123456789.dkr.ecr.us-east-1.amazonaws.com -t v1.0.0
    $(basename "$0") --skip-build -r 123456789.dkr.ecr.us-east-1.amazonaws.com -t latest

EOF
}

check_prerequisites() {
    log_info "Checking prerequisites..."

    local required_commands=("kubectl" "docker" "aws" "curl")
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

check_cluster_connection() {
    log_info "Checking Kubernetes cluster connection..."

    if ! kubectl cluster-info &> /dev/null; then
        log_error "Unable to connect to Kubernetes cluster"
        log_info "Please configure kubectl to connect to your EKS cluster:"
        log_info "  aws eks update-kubeconfig --name <cluster-name> --region <region>"
        return 1
    fi

    local context
    context=$(kubectl config current-context)
    log_success "Connected to cluster: ${context}"
    return 0
}

check_ecr_login() {
    log_info "Logging into ECR..."

    if ! aws ecr get-login-password --region "${AWS_REGION}" | \
        docker login --username AWS --password-stdin "${ECR_REGISTRY}" 2>&1 | tee -a "${LOG_FILE}"; then
        log_error "Failed to login to ECR"
        return 1
    fi

    log_success "ECR login successful"
    return 0
}

create_ecr_repository() {
    log_info "Checking ECR repository..."

    local repo_name="cortex-ingestion"

    if aws ecr describe-repositories --repository-names "${repo_name}" --region "${AWS_REGION}" &> /dev/null; then
        log_info "ECR repository '${repo_name}' already exists"
        return 0
    fi

    log_info "Creating ECR repository '${repo_name}'..."

    if ! aws ecr create-repository \
        --repository-name "${repo_name}" \
        --region "${AWS_REGION}" \
        --image-scanning-configuration scanOnPush=true \
        --encryption-configuration encryptionType=AES256 2>&1 | tee -a "${LOG_FILE}"; then
        log_error "Failed to create ECR repository"
        return 1
    fi

    aws ecr put-lifecycle-policy \
        --repository-name "${repo_name}" \
        --region "${AWS_REGION}" \
        --lifecycle-policy-text '{
            "rules": [
                {
                    "rulePriority": 1,
                    "description": "Keep last 30 images",
                    "selection": {
                        "tagStatus": "any",
                        "countType": "imageCountMoreThan",
                        "countNumber": 30
                    },
                    "action": {
                        "type": "expire"
                    }
                }
            ]
        }' 2>&1 | tee -a "${LOG_FILE}" || log_warn "Failed to set lifecycle policy"

    log_success "ECR repository created"
    return 0
}

build_docker_image() {
    log_info "Building Docker image..."

    local image_uri="${ECR_REGISTRY}/cortex-ingestion:${IMAGE_TAG}"

    cd "${APP_DIR}"

    if ! docker build \
        --platform linux/amd64 \
        -t "${image_uri}" \
        -t "${ECR_REGISTRY}/cortex-ingestion:latest" \
        -f Dockerfile \
        . 2>&1 | tee -a "${LOG_FILE}"; then
        log_error "Docker build failed"
        return 1
    fi

    log_success "Docker image built: ${image_uri}"
    return 0
}

push_docker_image() {
    log_info "Pushing Docker image to ECR..."

    local image_uri="${ECR_REGISTRY}/cortex-ingestion:${IMAGE_TAG}"

    if ! docker push "${image_uri}" 2>&1 | tee -a "${LOG_FILE}"; then
        log_error "Failed to push image"
        return 1
    fi

    if ! docker push "${ECR_REGISTRY}/cortex-ingestion:latest" 2>&1 | tee -a "${LOG_FILE}"; then
        log_warn "Failed to push latest tag"
    fi

    log_success "Docker image pushed: ${image_uri}"
    return 0
}

setup_secrets() {
    log_info "Setting up secrets..."

    local secrets_template="${SCRIPT_DIR}/secrets.env.template"
    local secrets_file="${SCRIPT_DIR}/secrets.env"

    if [[ ! -f "$secrets_file" ]]; then
        if [[ -f "$secrets_template" ]]; then
            log_warn "secrets.env not found, creating from template..."
            cp "$secrets_template" "$secrets_file"
            log_warn "Please edit ${secrets_file} and fill in your secret values"
            log_warn "Then run this script again"
            return 1
        else
            log_error "Neither secrets.env nor secrets.env.template found"
            return 1
        fi
    fi

    local empty_secrets=0
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" =~ ^# ]] && continue
        if [[ -z "$value" ]]; then
            log_warn "Empty value for: ${key}"
            empty_secrets=$((empty_secrets + 1))
        fi
    done < "$secrets_file"

    if [[ $empty_secrets -gt 0 ]]; then
        log_warn "${empty_secrets} secrets have empty values"
    fi

    log_success "Secrets file validated"
    return 0
}

create_iam_role() {
    log_info "Checking IAM role for service account..."

    local cluster_name
    cluster_name=$(kubectl config current-context | sed 's/.*\///' | sed 's/@.*//')

    if [[ -z "${CORTEX_INGESTION_IAM_ROLE_ARN:-}" ]]; then
        log_warn "CORTEX_INGESTION_IAM_ROLE_ARN not set"
        log_info "The service account will not have AWS IAM permissions"
        log_info "To create an IAM role for the service account, run:"
        log_info "  eksctl create iamserviceaccount \\"
        log_info "    --cluster=${cluster_name} \\"
        log_info "    --namespace=cortex-ingestion \\"
        log_info "    --name=cortex-ingestion \\"
        log_info "    --attach-policy-arn=arn:aws:iam::aws:policy/AmazonS3FullAccess \\"
        log_info "    --attach-policy-arn=arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess \\"
        log_info "    --approve"
    else
        log_success "IAM role configured: ${CORTEX_INGESTION_IAM_ROLE_ARN}"
    fi

    return 0
}

deploy_manifests() {
    log_info "Deploying Kubernetes manifests..."

    export ECR_REGISTRY
    export IMAGE_TAG
    export CORTEX_INGESTION_IAM_ROLE_ARN="${CORTEX_INGESTION_IAM_ROLE_ARN:-}"

    if ! "${SCRIPT_DIR}/install.sh"; then
        log_error "Deployment failed"
        return 1
    fi

    log_success "Deployment completed"
    return 0
}

wait_for_rollout() {
    log_info "Waiting for rollout to complete..."

    if ! kubectl rollout status deployment/cortex-ingestion -n cortex-ingestion --timeout=300s 2>&1 | tee -a "${LOG_FILE}"; then
        log_error "Rollout did not complete within timeout"
        log_info "Checking pod status..."
        kubectl get pods -n cortex-ingestion 2>&1 | tee -a "${LOG_FILE}"
        kubectl describe pods -n cortex-ingestion -l app.kubernetes.io/name=cortex-ingestion 2>&1 | tail -50 | tee -a "${LOG_FILE}"
        return 1
    fi

    log_success "Rollout completed successfully"
    return 0
}

verify_deployment() {
    log_info "Verifying deployment..."

    echo ""
    log_info "Deployment:"
    kubectl get deployment -n cortex-ingestion 2>&1 | tee -a "${LOG_FILE}"

    echo ""
    log_info "Pods:"
    kubectl get pods -n cortex-ingestion -o wide 2>&1 | tee -a "${LOG_FILE}"

    echo ""
    log_info "Services:"
    kubectl get svc -n cortex-ingestion 2>&1 | tee -a "${LOG_FILE}"

    echo ""
    log_info "Ingress:"
    kubectl get ingress -n cortex-ingestion 2>&1 | tee -a "${LOG_FILE}"

    echo ""
    log_info "HPA:"
    kubectl get hpa -n cortex-ingestion 2>&1 | tee -a "${LOG_FILE}"

    local pod_name
    pod_name=$(kubectl get pod -n cortex-ingestion -l app.kubernetes.io/name=cortex-ingestion -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

    if [[ -n "$pod_name" ]]; then
        echo ""
        log_info "Testing health endpoint..."
        if kubectl exec -n cortex-ingestion "$pod_name" -- curl -s http://localhost:8000/health 2>&1 | tee -a "${LOG_FILE}"; then
            log_success "Health check passed"
        else
            log_warn "Health check failed or not available yet"
        fi
    fi

    return 0
}

print_summary() {
    local nlb_hostname
    nlb_hostname=$(kubectl get svc ingress-nginx-controller -n ingress-nginx -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo "pending")

    echo ""
    echo "========================================"
    echo -e "${GREEN}Cortex Ingestion Deployment Complete${NC}"
    echo "========================================"
    echo ""
    echo "Image: ${ECR_REGISTRY}/cortex-ingestion:${IMAGE_TAG}"
    echo ""
    echo "Access:"
    echo "  External: https://ingestion.usecortex.ai"
    echo "  Load Balancer: ${nlb_hostname}"
    echo ""
    echo "DNS Configuration:"
    echo "  Point ingestion.usecortex.ai to: ${nlb_hostname}"
    echo ""
    echo "Commands:"
    echo "  View logs:    kubectl logs -f -l app.kubernetes.io/name=cortex-ingestion -n cortex-ingestion"
    echo "  Scale:        kubectl scale deployment cortex-ingestion -n cortex-ingestion --replicas=5"
    echo "  Restart:      kubectl rollout restart deployment/cortex-ingestion -n cortex-ingestion"
    echo ""
    echo "Log file: ${LOG_FILE}"
    echo "========================================"
}

confirm_deployment() {
    echo ""
    echo "========================================"
    echo "Deployment Configuration"
    echo "========================================"
    echo ""
    echo "Cluster: $(kubectl config current-context)"
    echo "Registry: ${ECR_REGISTRY}"
    echo "Image Tag: ${IMAGE_TAG}"
    echo "Skip Build: ${SKIP_BUILD}"
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

main() {
    local env_file="${SCRIPT_DIR}/secrets.env"
    local skip_confirm=false
    local dry_run=false

    SKIP_BUILD=false
    AWS_REGION="${AWS_REGION:-us-east-1}"
    ECR_REGISTRY="${ECR_REGISTRY:-}"
    IMAGE_TAG=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            -h|--help)
                usage
                exit 0
                ;;
            -e|--env)
                env_file="$2"
                shift 2
                ;;
            -t|--tag)
                IMAGE_TAG="$2"
                shift 2
                ;;
            -r|--registry)
                ECR_REGISTRY="$2"
                shift 2
                ;;
            --skip-build)
                SKIP_BUILD=true
                shift
                ;;
            --skip-confirm)
                skip_confirm=true
                shift
                ;;
            --dry-run)
                dry_run=true
                shift
                ;;
            *)
                log_error "Unknown option: $1"
                usage
                exit 1
                ;;
        esac
    done

    mkdir -p "${LOG_DIR}"
    touch "${LOG_FILE}"

    echo ""
    echo "========================================"
    echo "Cortex Ingestion Deployment"
    echo "========================================"
    echo ""

    log_info "Log file: ${LOG_FILE}"

    if [[ -z "$IMAGE_TAG" ]]; then
        if [[ -d "${APP_DIR}/.git" ]] || git -C "${APP_DIR}" rev-parse --git-dir &> /dev/null; then
            IMAGE_TAG=$(git -C "${APP_DIR}" rev-parse --short HEAD 2>/dev/null || echo "latest")
        else
            IMAGE_TAG="latest"
        fi
        log_info "Using image tag: ${IMAGE_TAG}"
    fi

    log_step "1" "Checking prerequisites"
    if ! check_prerequisites; then
        exit 1
    fi

    log_step "2" "Checking cluster connection"
    if ! check_cluster_connection; then
        exit 1
    fi

    if [[ -z "$ECR_REGISTRY" ]]; then
        log_error "ECR_REGISTRY is required"
        log_info "Use -r or --registry to specify the ECR registry URL"
        log_info "Example: $(basename "$0") -r 123456789.dkr.ecr.us-east-1.amazonaws.com"
        exit 1
    fi

    export ECR_REGISTRY
    export IMAGE_TAG
    export AWS_REGION

    if [[ "$dry_run" == true ]]; then
        log_info "Dry run mode - validating configuration..."
        log_success "Configuration is valid"
        log_info "Would deploy image: ${ECR_REGISTRY}/cortex-ingestion:${IMAGE_TAG}"
        exit 0
    fi

    if [[ "$skip_confirm" == false ]]; then
        if ! confirm_deployment; then
            exit 0
        fi
    fi

    log_step "3" "Setting up secrets"
    if ! setup_secrets; then
        exit 1
    fi

    if [[ "$SKIP_BUILD" == false ]]; then
        log_step "4" "Logging into ECR"
        if ! check_ecr_login; then
            exit 1
        fi

        log_step "5" "Creating ECR repository"
        if ! create_ecr_repository; then
            exit 1
        fi

        log_step "6" "Building Docker image"
        if ! build_docker_image; then
            exit 1
        fi

        log_step "7" "Pushing Docker image"
        if ! push_docker_image; then
            exit 1
        fi
    else
        log_info "Skipping Docker build and push"
    fi

    log_step "8" "Checking IAM role"
    create_iam_role

    log_step "9" "Deploying to Kubernetes"
    if ! deploy_manifests; then
        exit 1
    fi

    log_step "10" "Waiting for rollout"
    if ! wait_for_rollout; then
        exit 1
    fi

    log_step "11" "Verifying deployment"
    verify_deployment

    print_summary

    exit 0
}

trap 'log_error "Script interrupted"; exit 1' INT TERM
main "$@"

