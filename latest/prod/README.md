# Cortex Production Infrastructure

Deployment scripts for Cortex production Kubernetes infrastructure on AWS EKS.

## Prerequisites

- AWS EKS cluster created via `eksctl`
- `kubectl` configured with cluster access
- `helm` v3.x installed
- `curl` available

## Directory Structure

```
prod/
├── deploy.sh              # Main orchestration script
├── scripts/
│   └── common.sh          # Shared logging and utility functions
├── nginx/
│   ├── install.sh         # NGINX Ingress Controller installer
│   └── nlb.yaml           # NLB service configuration
├── falkordb/
│   ├── install.sh         # Graph database installer
│   └── *.yaml             # Cluster and exporter manifests
└── monitoring/
    ├── install.sh         # Monitoring stack installer
    └── *.yaml             # Values and ingress manifests
```

## Usage

### Deploy All Components

```bash
./deploy.sh
```

### Deploy Specific Components

```bash
./deploy.sh nginx              # NGINX Ingress only
./deploy.sh graphdb            # Graph database only
./deploy.sh monitoring         # Monitoring stack only
./deploy.sh nginx monitoring   # Multiple components
```

### Options

```bash
./deploy.sh --help          # Show help message
./deploy.sh --list          # List available components
./deploy.sh --dry-run       # Validate without deploying
./deploy.sh --skip-confirm  # Skip confirmation prompt
```

### Run Individual Installers

```bash
./nginx/install.sh
./falkordb/install.sh
./monitoring/install.sh
```

## Deployment Order

The scripts deploy components in the following order:

1. **NGINX Ingress Controller** - Ingress controller with AWS NLB
2. **Graph Database** - KubeBlocks-managed database cluster
3. **Monitoring Stack** - Prometheus and Grafana

Each component waits for all pods to be ready before the next component starts.

## Logging

Logs are written to `/tmp/cortex-deploy-logs/` with timestamps. Each deployment creates a new log file:

```
/tmp/cortex-deploy-logs/deploy-YYYYMMDD-HHMMSS.log
```

## Post-Deployment

### DNS Configuration

After deployment, configure DNS records to point to the NLB:

```bash
kubectl get svc ingress-nginx-controller -n ingress-nginx \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'
```

Required DNS records:
- `grafana-prod.usecortex.ai`
- `prometheus-prod.usecortex.ai`

### Access Credentials

**Grafana:**
```bash
kubectl get secret monitoring-grafana -n monitoring \
  -o jsonpath='{.data.admin-password}' | base64 -d
```

**Graph Database:**
```bash
kubectl get secret falkordb-prod-falkordb-account-default -n falkordb \
  -o jsonpath='{.data.password}' | base64 -d
```

## Verification

Check all deployed pods:

```bash
kubectl get pods -n ingress-nginx
kubectl get pods -n falkordb
kubectl get pods -n monitoring
```

Check services and ingress:

```bash
kubectl get svc -A | grep -E 'ingress-nginx|falkordb|monitoring'
kubectl get ingress -A
```
