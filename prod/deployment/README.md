# Cortex Production Deployment Scripts

Professional automation scripts for setting up production infrastructure on AWS EKS.

## Scripts Overview

### 1. EKS Cluster Setup (`cluster/eks.py`)
Creates and configures an EKS cluster with proper networking and IAM setup.

**Features:**
- Prerequisite validation (eksctl, kubectl, aws-cli)
- AWS credentials verification
- Customizable Karpenter tag keys
- Interactive subnet tagging with table display
- Comprehensive error handling and logging

**Usage:**
```bash
cd cluster
python3 eks.py
```

### 2. Karpenter Installation (`karpenter.py`)
Installs and configures Karpenter autoscaler with node pools.

**Features:**
- Helm installation validation
- Cluster connection verification
- Automatic configuration parsing from EKS setup
- Node pool zone updates
- EC2NodeClass configuration with custom tag keys

**Usage:**
```bash
python3 karpenter.py
```

## Installation Order

1. **EKS Cluster** - Run `cluster/eks.py` first
2. **Karpenter** - Run `karpenter.py` after cluster is ready
3. **Additional Components** - Nginx, Prometheus, etc. (coming soon)

## Prerequisites

### Required Tools
- Python 3.7+
- eksctl
- kubectl
- helm
- aws-cli

### Python Dependencies
```bash
pip install -r requirements.txt
```

## Configuration

All scripts read from a shared configuration file structure:

```yaml
eks-cluster:
  name: cortex-prod
  region: us-east-1

karpenter:
  tag-key: "cortex/karpenter-discovery"

vpc:
  id: vpc-xxxxx
  subnets:
    public:
      us-east-1a: { id: subnet-xxxxx }
```

## Output Files

- `cluster/cortex.eks.yaml` - Generated EKS cluster configuration
- `generated/` - Generated Karpenter configurations
- `*-setup-*.log` - Timestamped execution logs

## Next Steps

After running these scripts:
1. Verify cluster: `kubectl get nodes`
2. Check Karpenter: `kubectl get nodepools`
3. Install application components
