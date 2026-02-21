# External Secrets Operator for Cortex

This directory contains the configuration for [External Secrets Operator (ESO)](https://external-secrets.io/) to securely manage secrets for the Cortex platform using AWS Secrets Manager.

## Overview

External Secrets Operator synchronizes secrets from AWS Secrets Manager to Kubernetes Secrets, providing:

- **Secure storage**: Secrets stored in AWS Secrets Manager with encryption at rest
- **Automatic sync**: Secrets automatically synced to Kubernetes on a configurable interval
- **Secret rotation**: Support for automatic secret rotation
- **IAM-based access control**: Fine-grained access control using IAM policies
- **No secrets in git**: Secrets are never stored in configuration files

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     AWS Secrets Manager                          │
│  ┌─────────────────────┐  ┌─────────────────────────────────┐   │
│  │ cortex/prod/        │  │ cortex/prod/                    │   │
│  │ app-secrets         │  │ ingestion-secrets               │   │
│  └─────────────────────┘  └─────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ IRSA (IAM Roles for Service Accounts)
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     EKS Cluster                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  External Secrets Operator (namespace: external-secrets) │    │
│  │  ┌─────────────────────────────────────────────────────┐ │   │
│  │  │         ClusterSecretStore                          │ │   │
│  │  │         (aws-secrets-manager)                       │ │   │
│  │  └─────────────────────────────────────────────────────┘ │   │
│  └─────────────────────────────────────────────────────────┘    │
│                              │                                   │
│              ┌───────────────┴───────────────┐                  │
│              ▼                               ▼                  │
│  ┌─────────────────────┐      ┌─────────────────────────────┐   │
│  │  namespace:         │      │  namespace:                 │   │
│  │  cortex-app         │      │  cortex-ingestion           │   │
│  │  ┌───────────────┐  │      │  ┌───────────────────────┐  │   │
│  │  │ExternalSecret │  │      │  │ExternalSecret         │  │   │
│  │  └───────────────┘  │      │  └───────────────────────┘  │   │
│  │         │           │      │           │                 │   │
│  │         ▼           │      │           ▼                 │   │
│  │  ┌───────────────┐  │      │  ┌───────────────────────┐  │   │
│  │  │K8s Secret     │  │      │  │K8s Secret             │   │  │
│  │  │cortex-app-    │  │      │  │cortex-ingestion-      │   │  │
│  │  │secrets        │  │      │  │secrets                │   │  │
│  │  └───────────────┘  │      │  └───────────────────────┘  │   │
│  └─────────────────────┘      └─────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

## Prerequisites

- EKS cluster with OIDC provider enabled
- AWS CLI configured with appropriate permissions
- `kubectl` configured to access your cluster
- `eksctl` installed for IRSA setup
- `helm` installed for ESO installation

## Quick Start

### 1. Setup IRSA (IAM Roles for Service Accounts)

This creates the IAM policy and role that allows External Secrets to access AWS Secrets Manager:

```bash
# Set your cluster name
export CLUSTER_NAME="cortex-prod"
export AWS_REGION="us-east-1"

# Run the IRSA setup script
chmod +x setup-irsa.sh
./setup-irsa.sh --cluster $CLUSTER_NAME --region $AWS_REGION
```

### 2. Create Secrets in AWS Secrets Manager

Upload your secrets to AWS Secrets Manager:

```bash
# Option A: Use the provided script (reads from secrets.env files)
chmod +x create-aws-secrets.sh
./create-aws-secrets.sh --region us-east-1

# Option B: Create manually
aws secretsmanager create-secret \
  --name cortex/prod/app-secrets \
  --secret-string '{
    "OPENAI_API_KEY": "sk-...",
    "ANTHROPIC_API_KEY": "sk-ant-...",
    "MONGODB_CLUSTER_CONNECTION_URI": "mongodb+srv://...",
    "FIREBASE_TYPE": "service_account",
    "FIREBASE_PROJECT_ID": "gen-lang-client-0460151295",
    "FIREBASE_PRIVATE_KEY": "-----BEGIN PRIVATE KEY-----\n...",
    ...
  }' \
  --region us-east-1
```

### 3. Install External Secrets Operator

```bash
chmod +x install.sh
./install.sh
```

### 4. Verify Installation

```bash
# Check ESO pods
kubectl get pods -n external-secrets

# Check ClusterSecretStore status
kubectl get clustersecretstore aws-secrets-manager -o yaml

# Check ExternalSecrets status
kubectl get externalsecret -A

# Verify synced secrets
kubectl get secrets -n cortex-app
kubectl get secrets -n cortex-ingestion
```

## File Structure

```
external-secrets/
├── README.md                              # This file
├── 00_iam-policy.json                     # IAM policy for Secrets Manager access
├── 01_cluster-secret-store.yaml           # ClusterSecretStore configuration
├── 02_external-secret-cortex-app.yaml     # ExternalSecret for cortex-app
├── 03_external-secret-cortex-ingestion.yaml # ExternalSecret for cortex-ingestion
├── install.sh                             # ESO installation script
├── setup-irsa.sh                          # IRSA setup script
└── create-aws-secrets.sh                  # Script to upload secrets to AWS
```

## Configuration

### Refresh Interval

By default, secrets are refreshed every hour. To change this, edit the `refreshInterval` field in the ExternalSecret manifests:

```yaml
spec:
  refreshInterval: 15m  # Refresh every 15 minutes
```

### Adding New Secrets

1. Add the secret value to AWS Secrets Manager:
   ```bash
   aws secretsmanager put-secret-value \
     --secret-id cortex/prod/app-secrets \
     --secret-string '{"NEW_SECRET": "value", ...existing secrets...}' \
     --region us-east-1
   ```

2. Add the mapping to the ExternalSecret manifest:
   ```yaml
   - secretKey: NEW_SECRET
     remoteRef:
       key: cortex/prod/app-secrets
       property: NEW_SECRET
   ```

3. Apply the updated manifest:
   ```bash
   kubectl apply -f 02_external-secret-cortex-app.yaml
   ```

## Troubleshooting

### ExternalSecret not syncing

Check the ExternalSecret status:
```bash
kubectl describe externalsecret cortex-app-external-secrets -n cortex-app
```

Common issues:
- **SecretStore not ready**: Check ClusterSecretStore status
- **IAM permissions**: Verify IRSA is configured correctly
- **Secret not found**: Verify the secret exists in AWS Secrets Manager

### ClusterSecretStore not ready

Check the store status:
```bash
kubectl describe clustersecretstore aws-secrets-manager
```

Verify IRSA:
```bash
kubectl get sa external-secrets -n external-secrets -o yaml
# Should have annotation: eks.amazonaws.com/role-arn
```

### IAM Permission Errors

Test IAM permissions:
```bash
# Get a shell in the ESO pod
kubectl exec -it -n external-secrets deployment/external-secrets -- sh

# Try to access secrets (requires curl/aws cli in pod)
# Or check logs:
kubectl logs -n external-secrets -l app.kubernetes.io/name=external-secrets
```

## Migrating from secrets.env

If you're migrating from the file-based secrets approach:

1. Upload existing secrets to AWS Secrets Manager using `create-aws-secrets.sh`
2. Install External Secrets Operator
3. Apply ExternalSecret manifests
4. Verify secrets are synced: `kubectl get secrets -n cortex-app`
5. Delete the old manually-created secret (if it exists):
   ```bash
   kubectl delete secret cortex-app-secrets -n cortex-app
   ```
6. The ExternalSecret will recreate it with data from AWS

## Security Best Practices

1. **Least privilege**: The IAM policy only allows access to secrets under `cortex/*`
2. **Encryption**: Secrets are encrypted at rest in AWS Secrets Manager
3. **Audit logging**: Enable CloudTrail for audit logging of secret access
4. **Secret rotation**: Use AWS Secrets Manager rotation for supported secrets
5. **No local secrets**: Delete `secrets.env` files after migrating to ESO

## Related Resources

- [External Secrets Operator Documentation](https://external-secrets.io/)
- [AWS Secrets Manager](https://aws.amazon.com/secrets-manager/)
- [EKS IRSA](https://docs.aws.amazon.com/eks/latest/userguide/iam-roles-for-service-accounts.html)
