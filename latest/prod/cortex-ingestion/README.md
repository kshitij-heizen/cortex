# Cortex Ingestion Service Deployment

Kubernetes deployment manifests and scripts for the Cortex Ingestion Service (Kafka consumer for document processing).

## Prerequisites

- AWS EKS cluster with kubectl configured
- AWS CLI configured with appropriate permissions
- Docker installed and running
- Helm v3.x installed

## Directory Structure

```
cortex-ingestion/
├── deploy-cortex-ingestion.sh # Main deployment script (run this)
├── install.sh                 # Kubernetes manifest installer
├── secrets.env.template       # Template for secrets
├── secrets.env                # Your secrets (create from template)
├── 00_namespace.yaml          # Namespace
├── 01_configmap.yaml          # ConfigMap with environment variables
├── 02_secrets.yaml            # Secrets template (not used directly)
├── 03_deployment.yaml         # Deployment spec
├── 04_service.yaml            # Service and ServiceAccount
├── 05_ingress.yaml            # Ingress configuration
├── 06_hpa.yaml                # Horizontal Pod Autoscaler
├── 07_servicemonitor.yaml     # Prometheus ServiceMonitor
├── 08_pdb.yaml                # Pod Disruption Budget
└── README.md
```

## Quick Start

### 1. Configure Secrets

```bash
cp secrets.env.template secrets.env
```

Edit `secrets.env` and fill in your secret values:

```bash
MONGODB_CLUSTER_CONNECTION_URI=mongodb+srv://...
FALKORDB_PASSWORD=your-password
OPENAI_API_KEY=sk-xxx
GEMINI_API_KEY=xxx
# ... etc
```

### 2. Deploy

```bash
./deploy-cortex-ingestion.sh -r <your-ecr-registry>
```

Example:

```bash
./deploy-cortex-ingestion.sh -r 123456789012.dkr.ecr.us-east-1.amazonaws.com
```

## Deployment Options

```bash
# Full deployment with custom tag
./deploy-cortex-ingestion.sh -r 123456789012.dkr.ecr.us-east-1.amazonaws.com -t v1.0.0

# Skip Docker build (use existing image)
./deploy-cortex-ingestion.sh -r 123456789012.dkr.ecr.us-east-1.amazonaws.com -t latest --skip-build

# Non-interactive deployment
./deploy-cortex-ingestion.sh -r 123456789012.dkr.ecr.us-east-1.amazonaws.com --skip-confirm

# Dry run (validate only)
./deploy-cortex-ingestion.sh -r 123456789012.dkr.ecr.us-east-1.amazonaws.com --dry-run
```

## Environment Variables

Required:

| Variable | Description |
|----------|-------------|
| `ECR_REGISTRY` | ECR registry URL |

Optional:

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_REGION` | us-east-1 | AWS region |
| `IMAGE_TAG` | git SHA | Docker image tag |
| `CORTEX_INGESTION_IAM_ROLE_ARN` | - | IAM role for service account |

## IAM Role for Service Account

To enable AWS service access from pods, create an IAM role:

```bash
eksctl create iamserviceaccount \
  --cluster=<cluster-name> \
  --namespace=cortex-ingestion \
  --name=cortex-ingestion \
  --attach-policy-arn=arn:aws:iam::aws:policy/AmazonS3FullAccess \
  --attach-policy-arn=arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess \
  --approve
```

Then set the role ARN:

```bash
export CORTEX_INGESTION_IAM_ROLE_ARN=arn:aws:iam::123456789012:role/cortex-ingestion-sa-role
./deploy-cortex-ingestion.sh -r ...
```

## DNS Configuration

After deployment, configure DNS to point to the NLB:

```bash
kubectl get svc ingress-nginx-controller -n ingress-nginx \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'
```

Create DNS records:
- `ingestion.usecortex.ai` → NLB hostname

## Verification

```bash
# Check pods
kubectl get pods -n cortex-ingestion

# Check deployment
kubectl get deployment -n cortex-ingestion

# View logs
kubectl logs -f -l app.kubernetes.io/name=cortex-ingestion -n cortex-ingestion

# Test health endpoint
kubectl exec -it $(kubectl get pod -n cortex-ingestion -l app.kubernetes.io/name=cortex-ingestion -o jsonpath='{.items[0].metadata.name}') -n cortex-ingestion -- curl http://localhost:8000/health
```

## Scaling

Manual scaling:

```bash
kubectl scale deployment cortex-ingestion -n cortex-ingestion --replicas=5
```

HPA is configured to auto-scale between 2-10 replicas based on CPU/memory.

## Kafka Consumer

The ingestion service runs a Kafka consumer that listens for document ingestion messages. Check the readiness endpoint to verify the consumer is running:

```bash
kubectl exec -it <pod-name> -n cortex-ingestion -- curl http://localhost:8000/readiness
```

## Troubleshooting

### Pods not starting

```bash
kubectl describe pod -n cortex-ingestion -l app.kubernetes.io/name=cortex-ingestion
kubectl logs -n cortex-ingestion -l app.kubernetes.io/name=cortex-ingestion --previous
```

### Kafka connection issues

Check if Kafka bootstrap servers are configured correctly in the ConfigMap:

```bash
kubectl get configmap cortex-ingestion-config -n cortex-ingestion -o yaml
```

### Image pull errors

Verify ECR login:

```bash
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <registry>
```

## Updating

To update the application:

```bash
# Build and deploy new version
./deploy-cortex-ingestion.sh -r <registry> -t <new-tag>

# Or update existing deployment with new image
kubectl set image deployment/cortex-ingestion cortex-ingestion=<registry>/cortex-ingestion:<new-tag> -n cortex-ingestion
```

## Rollback

```bash
# View rollout history
kubectl rollout history deployment/cortex-ingestion -n cortex-ingestion

# Rollback to previous version
kubectl rollout undo deployment/cortex-ingestion -n cortex-ingestion

# Rollback to specific revision
kubectl rollout undo deployment/cortex-ingestion -n cortex-ingestion --to-revision=2
```

