# Milvus on EKS (CortexAI)

Self-hosted Milvus cluster using Milvus Operator on EKS.

- **Namespace**: `milvus-cortexai`
- **Cluster Name**: `cortexai`

## Endpoints

| Service                 | URL                                      | Protocol   |
| ----------------------- | ---------------------------------------- | ---------- |
| **Milvus API**    | `cortexai.milvusdb.usecortex.ai`       | gRPC (TLS) |
| **Milvus WebUI**  | `cortexai-webui.milvusdb.usecortex.ai` | HTTPS      |
| **Attu Admin UI** | `cortexai-attu.milvusdb.usecortex.ai`  | HTTPS      |

## Prerequisites

1. **Install Milvus Operator** (one-time setup):

   ```bash
   helm install milvus-operator \
     -n milvus-operator --create-namespace \
     --wait --wait-for-jobs \
     https://github.com/zilliztech/milvus-operator/releases/download/v1.1.2/milvus-operator-1.1.2.tgz
   ```
2. **Verify operator is running**:

   ```bash
   kubectl get pods -n milvus-operator
   ```

## Deployment

```bash
# Deploy everything
kubectl apply -f prod/milvus/

# Or apply in order:
kubectl apply -f 00_namespace.yaml
kubectl apply -f 01_storage-class.yaml
kubectl apply -f 02_milvus-cluster.yaml
kubectl apply -f 03_ingress-grpc.yaml
kubectl apply -f 04_ingress-webui.yaml
kubectl apply -f 05_ingress-attu.yaml

# Wait for cluster to be ready (takes 5-10 minutes)
kubectl get milvus -n milvus-cortexai -w
```

## Authentication & Credentials

Authentication is **enabled** by default in this deployment.

### Default Credentials

- **Username**: `root`
- **Password**: `Milvus` (default)

### Change Root Password (Recommended)

After deployment, change the default password immediately:

```python
from pymilvus import utility, connections

connections.connect(
    alias="default",
    uri="https://cortexai.milvusdb.usecortex.ai:443",
    token="root:Milvus"  # default credentials
)

# Change password
utility.reset_password("root", "Milvus", "YOUR_NEW_SECURE_PASSWORD")
```

### Create Additional Users

```python
from pymilvus import utility

# Create a new user
utility.create_user("cortex_app", "secure_password_here")

# Grant roles (optional)
# utility.grant_role("cortex_app", "admin")
```

## Python SDK Connection

### Install SDK

```bash
pip install pymilvus>=2.4.0
```

### Connect with TLS (via Ingress)

The Python SDK **fully supports TLS**. Since we're using nginx ingress with cert-manager TLS:

```python
from pymilvus import connections, Collection, utility

# Connect via HTTPS/TLS (recommended for production)
connections.connect(
    alias="default",
    uri="https://cortexai.milvusdb.usecortex.ai:443",
    token="root:Milvus"  # format: "username:password"
)

# Verify connection
print(f"Connected: {utility.get_server_version()}")

# List collections
print(utility.list_collections())
```

### Alternative: Connect with Explicit Parameters

```python
from pymilvus import connections

connections.connect(
    alias="default",
    host="cortexai.milvusdb.usecortex.ai",
    port="443",
    user="root",
    password="Milvus",
    secure=False  # Enable TLS
)
```

### Internal Cluster Connection (from within K8s)

```python
from pymilvus import connections

# No TLS needed for internal traffic
connections.connect(
    alias="default",
    host="cortexai-milvus.milvus-cortexai.svc.cluster.local",
    port="19530",
    user="root",
    password="YOUR_PASSWORD"
)
```

## Quick Test

### 1. Test Connection

```python
from pymilvus import connections, utility

connections.connect(
    uri="https://cortexai.milvusdb.usecortex.ai:443",
    token="root:Milvus"
)
print("Server version:", utility.get_server_version())
print("Collections:", utility.list_collections())
```

### 2. Create Test Collection

```python
from pymilvus import connections, Collection, FieldSchema, CollectionSchema, DataType, utility

connections.connect(
    uri="https://cortexai.milvusdb.usecortex.ai:443",
    token="root:Milvus"
)

# Define schema
fields = [
    FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
    FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=128)
]
schema = CollectionSchema(fields, description="Test collection")

# Create collection
collection = Collection(name="test_collection", schema=schema)
print(f"Created collection: {collection.name}")

# Insert test data
import random
vectors = [[random.random() for _ in range(128)] for _ in range(10)]
collection.insert([vectors])
print("Inserted 10 vectors")

# Create index
index_params = {"index_type": "IVF_FLAT", "metric_type": "L2", "params": {"nlist": 128}}
collection.create_index("embedding", index_params)
print("Created index")

# Load and search
collection.load()
results = collection.search(
    data=[vectors[0]],
    anns_field="embedding",
    param={"metric_type": "L2", "params": {"nprobe": 10}},
    limit=3
)
print(f"Search results: {results}")

# Cleanup
utility.drop_collection("test_collection")
print("Cleaned up test collection")
```

### 3. Test via CLI (curl)

```bash
# Check if gRPC ingress is responding (will fail with gRPC error, but confirms connectivity)
curl -v https://cortexai.milvusdb.usecortex.ai

# Check WebUI
curl -I https://cortexai-webui.milvusdb.usecortex.ai

# Check Attu
curl -I https://cortexai-attu.milvusdb.usecortex.ai
```

## Resource Summary

### Milvus Components

| Component | Replicas | CPU (req/limit) | Memory (req/limit) |
| --------- | -------- | --------------- | ------------------ |
| Proxy     | 2        | 500m / 2        | 1Gi / 4Gi          |
| QueryNode | 2        | 500m / 4        | 2Gi / 8Gi          |
| DataNode  | 2        | 500m / 2        | 1Gi / 4Gi          |
| IndexNode | 1        | 500m / 4        | 1Gi / 8Gi          |
| MixCoord  | 1        | 500m / 2        | 512Mi / 2Gi        |

### Dependencies

| Component   | Replicas | Storage    |
| ----------- | -------- | ---------- |
| etcd        | 3        | 10Gi each  |
| MinIO       | 4        | 100Gi each |
| Pulsar (ZK) | 1        | 10Gi       |
| Pulsar (BK) | 2        | 70Gi each  |

**Total Estimated Storage**: ~550-600 Gi

## UI Options

### 1. Built-in Milvus WebUI

URL: `https://cortexai-webui.milvusdb.usecortex.ai`

Basic observability interface for monitoring system metrics and status.

### 2. Attu Admin UI (Recommended)

URL: `https://cortexai-attu.milvusdb.usecortex.ai`

Full-featured GUI for:

- Collection management
- Data browsing & visualization
- Query execution
- Index management
- User management

**Login**: Use same credentials (root / YOUR_PASSWORD)

## Monitoring

```bash
# Check cluster status
kubectl get milvus -n milvus-cortexai

# Check all pods
kubectl get pods -n milvus-cortexai

# Check services
kubectl get svc -n milvus-cortexai

# View logs
kubectl logs -n milvus-cortexai -l app.kubernetes.io/instance=cortexai --tail=100

# Describe cluster
kubectl describe milvus cortexai -n milvus-cortexai
```

## Scaling

Update replicas in `02_milvus-cluster.yaml` and re-apply:

```bash
kubectl apply -f 02_milvus-cluster.yaml
```

## Troubleshooting

### Pods stuck in Pending

```bash
kubectl get pvc -n milvus-cortexai
kubectl describe pvc -n milvus-cortexai <pvc-name>
```

### gRPC connection issues via ingress

Ensure nginx ingress controller has HTTP/2 enabled. Alternative: use LoadBalancer:

```yaml
spec:
  components:
    proxy:
      serviceType: LoadBalancer
```

### Check TLS certificates

```bash
kubectl get certificate -n milvus-cortexai
kubectl describe certificate -n milvus-cortexai milvus-grpc-tls
```

### Authentication issues

```bash
# Check if auth is enabled
kubectl get milvus cortexai -n milvus-cortexai -o jsonpath='{.spec.config.milvus.common.security}'
```

## Cleanup

```bash
# Delete Milvus cluster (keeps PVCs)
kubectl delete milvus cortexai -n milvus-cortexai

# Delete everything including PVCs
kubectl delete -f prod/milvus/ --recursive
kubectl delete pvc -n milvus-cortexai --all
kubectl delete ns milvus-cortexai
```
