# Milvus Helm Chart

Helm chart for deploying Milvus vector database clusters per organization on Kubernetes using the Milvus Operator.

> **Note:** This chart is primarily deployed via ArgoCD. See `prod/argocd/milvus/` for the ApplicationSet configuration.

## Prerequisites

1. **Milvus Operator** installed in the cluster:

   ```bash
   helm install milvus-operator \
     -n milvus-operator --create-namespace \
     --wait --wait-for-jobs \
     https://github.com/zilliztech/milvus-operator/releases/download/v1.1.2/milvus-operator-1.1.2.tgz
   ```
2. **nginx-inc** ingress controller deployed
3. **cert-manager** for TLS certificates (optional)
4. **Prometheus Operator** for monitoring (optional)

## Installation

### Deploy a new tenant

```bash
# Deploy with default values (org_id: cortexai)
helm install milvus-cortexai ./milvus-chart -n milvus-cortexai --create-namespace

# Deploy with custom org_id
helm install milvus-acme ./milvus-chart \
  --set org_id=acme2 \
  -n milvus-acme2 --create-namespace

# Deploy with custom values file
helm install milvus-acme ./milvus-chart \
  -f custom-values.yaml \
  -n milvus-acme --create-namespace
```

### Upgrade an existing deployment

```bash
helm upgrade milvus-cortexai ./milvus-chart -n milvus-cortexai
```

## Configuration

### Key Variable: `org_id`

The `org_id` is the **root variable** that drives all naming conventions:

| Resource       | Naming Pattern                           |
| -------------- | ---------------------------------------- |
| Namespace      | `milvus-{org_id}`                      |
| Milvus Cluster | `{org_id}`                             |
| Milvus Service | `{org_id}-milvus`                      |
| gRPC Host      | `{org_id}.milvusdb.usecortex.ai`       |
| WebUI Host     | `{org_id}-webui.milvusdb.usecortex.ai` |
| Attu Host      | `{org_id}-attu.milvusdb.usecortex.ai`  |
| TLS Secret     | `milvus-wildcard-tls-{org_id}`         |

### Example: Deploy for organization "acme"

```yaml
# values-acme.yaml
org_id: acme

milvus:
  components:
    proxy:
      replicas: 3
    queryNode:
      replicas: 4
```

This creates:

- Namespace: `milvus-acme`
- Endpoints:
  - `acme.milvusdb.usecortex.ai` (gRPC API)
  - `acme-webui.milvusdb.usecortex.ai` (WebUI)
  - `acme-attu.milvusdb.usecortex.ai` (Attu Admin)

## Values Reference

### Core Settings

| Parameter            | Description                    | Default      |
| -------------------- | ------------------------------ | ------------ |
| `org_id`           | Organization/tenant identifier | `cortexai` |
| `namespace.create` | Create namespace               | `true`     |

### Storage

| Parameter                        | Description          | Default             |
| -------------------------------- | -------------------- | ------------------- |
| `storageClass.create`          | Create storage class | `true`            |
| `storageClass.provisioner`     | Storage provisioner  | `ebs.csi.aws.com` |
| `storageClass.parameters.type` | EBS volume type      | `gp3`             |

### Milvus Components

| Parameter                                | Description           | Default     |
| ---------------------------------------- | --------------------- | ----------- |
| `milvus.mode`                          | Deployment mode       | `cluster` |
| `milvus.config.authEnabled`            | Enable authentication | `true`    |
| `milvus.components.proxy.replicas`     | Proxy replicas        | `2`       |
| `milvus.components.queryNode.replicas` | QueryNode replicas    | `2`       |
| `milvus.components.dataNode.replicas`  | DataNode replicas     | `2`       |

### Dependencies

| Parameter                                 | Description    | Default   |
| ----------------------------------------- | -------------- | --------- |
| `milvus.dependencies.etcd.replicaCount` | etcd replicas  | `3`     |
| `milvus.dependencies.minio.replicas`    | MinIO replicas | `4`     |
| `milvus.dependencies.minio.storageSize` | MinIO storage  | `100Gi` |

### Ingress

| Parameter                 | Description          | Default                   |
| ------------------------- | -------------------- | ------------------------- |
| `ingress.className`     | Ingress class        | `nginx-inc`             |
| `ingress.domain`        | Base domain          | `milvusdb.usecortex.ai` |
| `ingress.grpc.enabled`  | Enable gRPC ingress  | `true`                  |
| `ingress.webui.enabled` | Enable WebUI ingress | `true`                  |
| `ingress.attu.enabled`  | Enable Attu ingress  | `true`                  |

### Attu Admin UI

| Parameter          | Description    | Default  |
| ------------------ | -------------- | -------- |
| `attu.enabled`   | Deploy Attu    | `true` |
| `attu.image.tag` | Attu image tag | `v2.4` |

### Monitoring

| Parameter               | Description           | Default  |
| ----------------------- | --------------------- | -------- |
| `monitoring.enabled`  | Create ServiceMonitor | `true` |
| `monitoring.interval` | Scrape interval       | `30s`  |

## Multi-Org Deployment

### Via ArgoCD (Recommended)

The preferred method is to use the ArgoCD ApplicationSet in `prod/argocd/milvus/`:

1. Create `org/<org>/milvus.values.yaml` in the `argocd-infra` repo
2. ArgoCD automatically detects and deploys

See `prod/argocd/milvus/README.md` for full details.

### Via Helm CLI (Manual)

To deploy multiple orgs manually:

```bash
# Org 1: cortexai
helm install milvus-cortexai ./milvus-chart --set org_id=cortexai -n milvus-cortexai --create-namespace

# Org 2: acme
helm install milvus-acme ./milvus-chart --set org_id=acme -n milvus-acme --create-namespace

# Org 3: bigcorp  
helm install milvus-bigcorp ./milvus-chart --set org_id=bigcorp -n milvus-bigcorp --create-namespace
```

Each org gets:

- Isolated namespace (`milvus-<org>`)
- Separate Milvus cluster
- Dedicated storage
- Unique DNS endpoints

## Connecting to Milvus

### Python SDK

```python
from pymilvus import connections

# Connect via TLS (external)
connections.connect(
    uri="https://cortexai.milvusdb.usecortex.ai:443",
    token="root:Milvus"  # Change password after first login!
)

# Connect internally (within K8s)
connections.connect(
    host="cortexai-milvus.milvus-cortexai.svc.cluster.local",
    port="19530",
    user="root",
    password="YOUR_PASSWORD"
)
```

### Default Credentials

- **Username**: `root`
- **Password**: `Milvus` (change immediately after deployment)

## Cleanup

```bash
# Delete an org deployment
helm uninstall milvus-cortexai -n milvus-cortexai

# Delete PVCs (data will be lost!)
kubectl delete pvc --all -n milvus-cortexai

# Delete namespace
kubectl delete ns milvus-cortexai
```

## Troubleshooting

### Milvus pods not created

The Milvus Operator creates pods only after all dependencies are ready. Check the Milvus CR status:

```bash
kubectl -n milvus-<org> get milvus
kubectl -n milvus-<org> describe milvus <org>
```

Common blockers:
- `MsgStreamReady: ConnectionFailed` — Pulsar not ready
- `StorageReady: False` — MinIO not ready
- `EtcdReady: False` — etcd not ready

### Ingress returns 502

The `<org>-milvus` service is created by the Milvus Operator, not Helm. If Milvus CR is stuck, the service won't exist:

```bash
kubectl -n milvus-<org> get svc | grep milvus
kubectl -n milvus-<org> get endpoints | grep milvus
```

### Check Milvus Operator logs

```bash
kubectl -n milvus-operator logs -l app.kubernetes.io/name=milvus-operator --tail=200
```
