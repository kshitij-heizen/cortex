# Karpenter Node Provisioning

Karpenter automatically provisions EC2 nodes based on pending pod requirements.

## Files

```
karpenter/
├── node-class.yaml    # EC2NodeClass - AWS config (AMI, subnets, security groups)
├── node-pool.yaml     # NodePools - instance types, labels, taints, limits
└── README.md
```

## Quick Commands

### Check Status

```bash
# View NodePools and their status
kubectl get nodepools

# View EC2NodeClass
kubectl get ec2nodeclasses

# View all nodes with their roles and which NodePool provisioned them
kubectl get nodes -L role,karpenter.sh/nodepool

# Check Karpenter controller logs
kubectl logs -n karpenter -l app.kubernetes.io/name=karpenter --tail=50

# Check for pending pods (triggers Karpenter to provision)
kubectl get pods -A --field-selector=status.phase=Pending
```

### Test Provisioning

```bash
# Deploy a test pod that requires memory-pool-xlarge
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: karpenter-test
  namespace: default
spec:
  nodeSelector:
    role: memory-db-large-scalable
  tolerations:
    - key: workload
      value: database-large-scalable
      effect: NoSchedule
  containers:
    - name: test
      image: public.ecr.aws/amazonlinux/amazonlinux:2023
      command: ["sleep", "3600"]
      resources:
        requests:
          memory: "1Gi"
EOF

# Watch node provisioning
kubectl get nodes -w -L role,karpenter.sh/nodepool

# Cleanup test pod
kubectl delete pod karpenter-test
```

## Rollback to AWS Managed Nodegroups

If something goes wrong, here's how to disable Karpenter and use only AWS managed nodegroups:

### Step 1: Delete NodePools (stops new provisioning)

```bash
kubectl delete nodepools --all
```

### Step 2: Delete EC2NodeClass

```bash
kubectl delete ec2nodeclasses --all
```

### Step 3: Drain Karpenter-provisioned nodes (if any)

```bash
# List Karpenter nodes (have karpenter.sh/nodepool label)
kubectl get nodes -l karpenter.sh/nodepool

# Drain each Karpenter node
kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data

# Karpenter will NOT provision new nodes since NodePools are deleted
# Pods will reschedule to AWS managed nodegroup nodes
```

### Step 4: (Optional) Uninstall Karpenter completely

```bash
helm uninstall karpenter -n karpenter
kubectl delete namespace karpenter
```

### Step 5: Scale up AWS managed nodegroups

In AWS Console or via eksctl:

```bash
eksctl scale nodegroup --cluster=cortex-prod --name=<nodegroup-name> --nodes=<desired>
```

## NodePool Configuration

### memory-pool-xlarge (FalkorDB data nodes)

| Setting       | Value                                                                  |
| ------------- | ---------------------------------------------------------------------- |
| Instances     | r6i.large, r6i.xlarge, r6i.2xlarge, r6a.large, r6a.xlarge, r6a.2xlarge |
| Label         | `role: memory-db-large-scalable`                                     |
| Taint         | `workload=database-large-scalable:NoSchedule`                        |
| Capacity      | on-demand                                                              |
| CPU Limit     | 100 vCPUs total                                                        |
| Consolidation | WhenEmpty after 30m, protected from Drifted/Underutilized              |

### general-pool (sentinels, grafana, general workloads)

| Setting       | Value                                                              |
| ------------- | ------------------------------------------------------------------ |
| Instances     | m6i.medium/large/xlarge, m6a.medium/large/xlarge, m7i.medium/large |
| Label         | `role: general`                                                  |
| Taint         | none                                                               |
| Capacity      | on-demand                                                          |
| CPU Limit     | 100 vCPUs total                                                    |
| Consolidation | WhenEmptyOrUnderutilized after 5m                                  |

## Troubleshooting

### Pods stuck in Pending

```bash
# Check pod events
kubectl describe pod <pod-name>

# Check Karpenter logs for provisioning errors
kubectl logs -n karpenter -l app.kubernetes.io/name=karpenter --tail=100 | grep -i error

# Common issues:
# - Pod nodeSelector doesn't match any NodePool labels
# - Pod doesn't tolerate NodePool taints
# - NodePool CPU limit reached
# - No available capacity in AWS for requested instance types
```

### Node not being created

```bash
# Check if NodePool is ready
kubectl get nodepools

# Check EC2NodeClass status
kubectl describe ec2nodeclass default

# Verify subnets have karpenter.sh/discovery tag
aws ec2 describe-subnets --filters "Name=tag:karpenter.sh/discovery,Values=cortex-prod"
```

### Node created but pod not scheduling

```bash
# Check node labels and taints
kubectl describe node <node-name>

# Verify pod tolerations match node taints
kubectl get pod <pod-name> -o yaml | grep -A10 tolerations
```

## Related Files

- FalkorDB values: `prod/falkordb/helm/falkor-chart/values.yaml`
  - `dataNode.nodeSelector.role: memory-db-large-scalable`
  - `dataNode.tolerations: workload=database-large-scalable:NoSchedule`
  - `sentinelNode.nodeSelector.role: general`

## IAM Roles

Created during Karpenter setup:

- `KarpenterControllerRole-cortex-prod` - Karpenter controller (IRSA)
- `KarpenterNodeRole-cortex-prod` - EC2 instances launched by Karpenter
