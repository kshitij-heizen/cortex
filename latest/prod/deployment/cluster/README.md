# EKS Cluster Setup Script

Professional script to create and configure an EKS cluster for production deployment.

## Prerequisites

### Required Tools

- **eksctl** - EKS cluster management tool
- **kubectl** - Kubernetes command-line tool
- **aws-cli** - AWS command-line interface

### AWS Configuration

- AWS credentials must be configured (`aws configure`)
- Appropriate IAM permissions for EKS cluster creation

## Installation

1. Install Python dependencies:

```bash
pip install -r requirements.txt
```

2. Ensure all required tools are installed:

```bash
# macOS
brew install eksctl kubectl awscli

# Linux
# Follow official installation guides for each tool
```

## Configuration

1. **Base Configuration**: `example-config.yaml`

   - Template EKS cluster configuration
   - Contains default settings for VPC, IAM, addons, and node groups
2. **Input Configuration**: Your custom values file

   - Must contain:
     - `eks-cluster.name` - Cluster name
     - `eks-cluster.region` - AWS region
     - `vpc.id` - VPC ID
     - `vpc.subnets.private` - Private subnet mappings
     - `vpc.subnets.public` - Public subnet mappings (optional)

## Usage

Run the script:

```bash
python3 eks.py
```

The script will:
1. ✓ Check prerequisites (eksctl, kubectl, aws-cli)
2. ✓ Verify AWS credentials
3. ✓ Validate configuration files
4. ✓ Generate cluster configuration
5. ✓ Read Karpenter tag key from config (or use default)
6. ✓ Display current subnet tags and request confirmation
7. ✓ Tag subnets for Karpenter discovery
8. ✓ Create EKS cluster (15-20 minutes)

## Features

- **Colored Output**: Easy-to-read terminal output with status indicators
- **Comprehensive Error Handling**: Validates all inputs and prerequisites
- **Logging**: Detailed logs saved to timestamped files
- **Interactive Prompts**: User confirmation before cluster creation
- **Cost Warning**: Alerts about AWS costs before proceeding
- **Smart Subnet Tagging**: Safe and interactive subnet tagging for Karpenter
  - **Config-based tag key** - Specify in your values file or use default
  - **Displays existing tags** in a formatted table before making changes
  - **Requires user confirmation** before tagging any subnets
  - **Detailed summary** of all tagging operations

## Output

- **Generated Config**: `cortex.eks.yaml` - Final cluster configuration
- **Log File**: `eks-setup-YYYYMMDD-HHMMSS.log` - Detailed execution log

## Karpenter Subnet Requirements

For Karpenter to work properly, all subnets must be tagged with a discovery tag.

### Customizable Tag Key

**Default tag key**: `cortex/karpenter-discovery`

The script uses a custom tag key by default to avoid conflicts with existing Karpenter installations that may use the standard `karpenter.sh/discovery` tag.

**Configuration Options:**

1. **Use default** - If you don't specify anything, the script uses `cortex/karpenter-discovery`
2. **Custom tag key** - Add to your values file:
   ```yaml
   karpenter:
     tag-key: "your-custom-key"
   ```

The tag value will always be set to your cluster name.

### Subnet Tagging Process

The script provides a safe, interactive tagging workflow:

1. **Displays current tags** - Shows all existing tags for each subnet in a table format
2. **Analyzes requirements** - Identifies which subnets need tagging
3. **Asks for confirmation** - Shows exactly which subnets will be tagged before proceeding
4. **Tags subnets** - Only proceeds if you confirm
5. **Provides summary** - Reports success/failure for each operation

**Example table display:**
```
Subnet ID                 AZ              Type       Tags
================================================================================
subnet-abc123            us-east-1a      private    Name=Private-1A, Environment=prod
subnet-def456            us-east-1b      private    Name=Private-1B (+2 more)
subnet-ghi789            us-east-1a      public     (no tags)
```

**Note**: You need appropriate IAM permissions to tag EC2 resources (`ec2:CreateTags`, `ec2:DescribeTags`).

## Next Steps

After successful cluster creation:

1. Verify cluster: `kubectl get nodes`
2. Install Karpenter (autoscaling)
3. Install additional components:
   - Nginx Ingress Controller
   - Prometheus & Grafana (monitoring)
   - ClickHouse (analytics)
   - Vector (log aggregation)
   - FalkorDB & Milvus (databases)
   - ArgoCD (GitOps)

## Troubleshooting

- **Missing tools**: Install prerequisites listed above
- **AWS credentials**: Run `aws configure` or set environment variables
- **Permission errors**: Ensure IAM user has EKS creation permissions
- **Partial creation**: Check A	WS console if interrupted

## Example Input Configuration

```yaml
eks-cluster:
  name: my-production-cluster
  region: us-east-1

# Optional: Customize Karpenter tag key
# If not specified, defaults to: cortex/karpenter-discovery
karpenter:
  tag-key: "cortex/karpenter-discovery"

vpc:
  id: vpc-0123456789abcdef
  subnets:
    private:
      us-east-1a: { id: subnet-private-1a }
      us-east-1b: { id: subnet-private-1b }
    public:
      us-east-1a: { id: subnet-public-1a }
      us-east-1b: { id: subnet-public-1b }
```
