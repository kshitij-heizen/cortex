# Orphaned EBS Volumes - Investigation & Cleanup Guide

This guide helps you identify and clean up orphaned EBS volumes from deleted Milvus and FalkorDB clusters.

## Quick Reference

| Metric | Value |
|--------|-------|
| **Region** | us-east-1 |
| **gp3 Cost** | $0.08/GB/month |
| **Storage Classes** | `milvus-storage`, `falkordb-prod-gp3`, `falkordb-general-gp3` |

---

## Step 1: Find All Orphaned (Available) EBS Volumes

Orphaned volumes have status `available` - meaning they're not attached to any instance.

```bash
# List all available volumes with K8s metadata
aws ec2 describe-volumes \
  --region us-east-1 \
  --filters "Name=status,Values=available" \
  --query 'Volumes[*].{VolumeId:VolumeId,Size:Size,Created:CreateTime,Namespace:Tags[?Key==`kubernetes.io/created-for/pvc/namespace`]|[0].Value,PVC:Tags[?Key==`kubernetes.io/created-for/pvc/name`]|[0].Value}' \
  --output table
```

### Filter by Database Type

```bash
# Milvus volumes only
aws ec2 describe-volumes \
  --region us-east-1 \
  --filters "Name=status,Values=available" "Name=tag:kubernetes.io/created-for/pvc/namespace,Values=milvus-*" \
  --query 'Volumes[*].{VolumeId:VolumeId,Size:Size,Namespace:Tags[?Key==`kubernetes.io/created-for/pvc/namespace`]|[0].Value}' \
  --output table

# FalkorDB volumes only
aws ec2 describe-volumes \
  --region us-east-1 \
  --filters "Name=status,Values=available" "Name=tag:kubernetes.io/created-for/pvc/namespace,Values=falkordb-*" \
  --query 'Volumes[*].{VolumeId:VolumeId,Size:Size,Namespace:Tags[?Key==`kubernetes.io/created-for/pvc/namespace`]|[0].Value}' \
  --output table
```

---

## Step 2: Get Summary by Namespace

```bash
# Group orphaned volumes by namespace with totals
aws ec2 describe-volumes \
  --region us-east-1 \
  --filters "Name=status,Values=available" \
  --query 'Volumes[*].{Size:Size,Namespace:Tags[?Key==`kubernetes.io/created-for/pvc/namespace`]|[0].Value}' \
  --output json | \
  jq -r 'group_by(.Namespace) | .[] | "\(.[0].Namespace)\t\(length)\t\(map(.Size) | add)"' | \
  sort | \
  column -t -s $'\t' -N "NAMESPACE,VOLUMES,TOTAL_GB"
```

---

## Step 3: Check if Kubernetes Namespace Still Exists

```bash
# List all Milvus namespaces
kubectl get ns | grep milvus

# List all FalkorDB namespaces
kubectl get ns | grep falkordb

# Check if a specific namespace exists
kubectl get ns milvus-acme 2>/dev/null && echo "EXISTS" || echo "DELETED"
```

### Compare orphaned volumes vs active namespaces

```bash
# Get namespaces from orphaned volumes
aws ec2 describe-volumes \
  --region us-east-1 \
  --filters "Name=status,Values=available" \
  --query 'Volumes[*].Tags[?Key==`kubernetes.io/created-for/pvc/namespace`].Value' \
  --output text | tr '\t' '\n' | sort -u > /tmp/orphaned-namespaces.txt

# Get active K8s namespaces
kubectl get ns -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n' | sort -u > /tmp/active-namespaces.txt

# Find namespaces with orphaned volumes but no active K8s namespace (SAFE TO DELETE)
comm -23 /tmp/orphaned-namespaces.txt /tmp/active-namespaces.txt
```

---

## Step 4: Check Kubernetes PV Status

PVs in `Released` or `Failed` state indicate the PVC was deleted but the volume wasn't cleaned up.

```bash
# List all Released/Failed PVs
kubectl get pv | grep -E "Released|Failed"

# Get details with volume handles
kubectl get pv -o json | jq -r '
  .items[] | 
  select(.status.phase=="Released" or .status.phase=="Failed") | 
  "\(.metadata.name)\t\(.status.phase)\t\(.spec.csi.volumeHandle)\t\(.spec.capacity.storage)\t\(.spec.claimRef.namespace)"
' | column -t -s $'\t' -N "PV_NAME,STATUS,VOLUME_ID,SIZE,NAMESPACE"
```

---

## Step 5: Calculate Cost

```bash
# Total GB of orphaned volumes
TOTAL_GB=$(aws ec2 describe-volumes \
  --region us-east-1 \
  --filters "Name=status,Values=available" \
  --query 'sum(Volumes[*].Size)' \
  --output text)

echo "Total orphaned storage: ${TOTAL_GB} GB"
echo "Estimated monthly cost: \$$(echo "$TOTAL_GB * 0.08" | bc)"
```

---

## Step 6: Delete Orphaned Volumes

### Option A: Delete by Namespace

```bash
# Replace NAMESPACE with the target namespace
NAMESPACE="milvus-acme"

# Preview (dry-run)
aws ec2 describe-volumes \
  --region us-east-1 \
  --filters "Name=status,Values=available" "Name=tag:kubernetes.io/created-for/pvc/namespace,Values=$NAMESPACE" \
  --query 'Volumes[*].VolumeId' \
  --output text

# Delete (DESTRUCTIVE)
aws ec2 describe-volumes \
  --region us-east-1 \
  --filters "Name=status,Values=available" "Name=tag:kubernetes.io/created-for/pvc/namespace,Values=$NAMESPACE" \
  --query 'Volumes[*].VolumeId' \
  --output text | \
  xargs -n1 aws ec2 delete-volume --region us-east-1 --volume-id
```

### Option B: Delete Single Volume

```bash
aws ec2 delete-volume --region us-east-1 --volume-id vol-xxxxxxxxx
```

### Option C: Use the Cleanup Script

```bash
# Dry-run (preview)
./scripts/cleanup-orphaned-ebs.sh --dry-run

# Execute deletion
./scripts/cleanup-orphaned-ebs.sh --execute

# Delete specific namespace
./scripts/cleanup-orphaned-ebs.sh --namespace milvus-acme --execute
```

---

## Step 7: Clean Up Released PVs in Kubernetes

After deleting EBS volumes, clean up the orphaned PV objects:

```bash
# Delete all Released PVs
kubectl get pv | grep Released | awk '{print $1}' | xargs kubectl delete pv

# Or delete specific PV
kubectl delete pv pvc-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

---

## Understanding Volume States

| EBS Status | K8s PV Status | Meaning | Action |
|------------|---------------|---------|--------|
| `available` | `Released` | PVC deleted, volume retained | Safe to delete if namespace gone |
| `available` | Not found | PV also deleted | Safe to delete |
| `in-use` | `Bound` | Active volume | DO NOT DELETE |
| `available` | `Bound` | Inconsistent state | Investigate |

---

## Milvus Volume Types

Each Milvus cluster creates these PVCs:

| Component | Count | Size | Purpose |
|-----------|-------|------|---------|
| etcd | 3 | 10Gi each | Metadata storage |
| minio | 4 | 100Gi each | Object storage |
| pulsar-bookie-journal | 2-3 | 20Gi each | Message journal |
| pulsar-bookie-ledgers | 2-3 | 50Gi each | Message ledgers |
| pulsar-zookeeper | 3 | 10Gi each | Pulsar coordination |

**Total per cluster: ~670GB**

---

## FalkorDB Volume Types

Each FalkorDB cluster creates:

| Component | Count | Size | Purpose |
|-----------|-------|------|---------|
| data-falkordb | 2 | 50-100Gi each | Graph data |
| sentinel | 3 | 10Gi each | Sentinel data |

**Total per cluster: ~130-230GB**

---

## Prevention: Proper Cluster Deletion

To avoid orphaned volumes in the future:

### Milvus
```bash
# Delete Milvus CR (triggers cleanup)
kubectl delete milvus <cluster-name> -n milvus-<org_id>

# Wait for PVCs to be deleted
kubectl get pvc -n milvus-<org_id> -w

# Delete namespace
kubectl delete ns milvus-<org_id>
```

### FalkorDB
```bash
# Delete FalkorDB resources
helm uninstall <release-name> -n falkordb-<tenant>

# Delete PVCs manually if needed
kubectl delete pvc --all -n falkordb-<tenant>

# Delete namespace
kubectl delete ns falkordb-<tenant>
```

---

## Automation: Scheduled Cleanup

Consider adding a CronJob or scheduled Lambda to detect orphaned volumes:

```bash
# Add to crontab or CI/CD
0 9 * * 1 /path/to/scripts/cleanup-orphaned-ebs.sh --dry-run | mail -s "Orphaned EBS Report" cloud@usecortex.ai
```

---

## Troubleshooting

### Volume won't delete
```bash
# Check if volume is attached
aws ec2 describe-volumes --volume-ids vol-xxx --query 'Volumes[0].Attachments'

# Force detach if stuck
aws ec2 detach-volume --volume-id vol-xxx --force
```

### Can't find namespace tag
Some volumes may not have K8s tags. Check creation date and size to identify:
```bash
aws ec2 describe-volumes \
  --region us-east-1 \
  --filters "Name=status,Values=available" \
  --query 'Volumes[?!Tags[?Key==`kubernetes.io/created-for/pvc/namespace`]].{VolumeId:VolumeId,Size:Size,Created:CreateTime}' \
  --output table
```
