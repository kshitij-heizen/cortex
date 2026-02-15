# Milvus Operator Cluster YAML: what it is and why it matters

The most important file in this setup is `02_milvus-cluster.yaml`.

It is a **Milvus Custom Resource (CR)**:

- `apiVersion: milvus.io/v1beta1`
- `kind: Milvus`

When you apply it, the **Milvus Operator** continuously reconciles the cluster to match what you declared.

In other words:

- This YAML is the **desired state**.
- The operator is the **controller** that creates/updates Kubernetes resources (Deployments/StatefulSets/Services/PVCs) to match that state.

---

## 1) Identity

```yaml
metadata:
  name: cortexai
  namespace: milvus-cortexai
```

- The cluster is named **`cortexai`**.
- Everything is deployed into **`milvus-cortexai`**.

The name also becomes a prefix for many generated resources (Services, Deployments, etc.).

---

## 2) `spec.mode: cluster`

```yaml
spec:
  mode: cluster
```

Milvus runs in a distributed architecture:

- **Proxy** (client entrypoint)
- **QueryNode** (search/query execution)
- **DataNode** (ingest / flush)
- **IndexNode** (index build)
- **Coordinators** (metadata + scheduling)

`cluster` mode means these components run as separate workloads and can be scaled independently.

---

## 3) Global Milvus configuration (`spec.config`)

```yaml
spec:
  config:
    milvus:
      log:
        level: info
      common:
        security:
          authorizationEnabled: true
```

This section maps to Milvus configuration.

### Authentication

`authorizationEnabled: true` turns on user/password auth.

- Default user: `root`
- Default password: `Milvus`

Operational implication:

- Your clients must pass credentials.
- For `pymilvus`, you typically use `token="username:password"`.

---

## 4) Milvus core components (`spec.components`)

This section controls the **Milvus workloads** themselves.

### Proxy

```yaml
components:
  proxy:
    replicas: 2
    resources: ...
    serviceType: ClusterIP
```

- **Proxy** is what the SDK connects to.
- `serviceType: ClusterIP` means it is only reachable *inside* the cluster unless you expose it (Ingress / LB).

In this repo we expose it using nginx ingress at:

- `cortexai.milvusdb.usecortex.ai`

### QueryNode

```yaml
queryNode:
  replicas: 2
  resources:
    requests:
      memory: "2Gi"
    limits:
      memory: "8Gi"
```

- This is usually your **main scaling lever** for query throughput.
- Memory can be significant depending on dataset + index type.

### DataNode

Handles ingestion / compaction-related work.

### IndexNode

Index build can be CPU/memory heavy (especially for larger datasets). If you do lots of index building, scale this.

### MixCoord

```yaml
mixCoord:
  replicas: 1
```

Milvus can run multiple coordinators; `mixCoord` consolidates them.

If you need HA for coordinators, consider increasing this (depending on Milvus/operator support and your HA goals).

---

## 5) Dependencies (`spec.dependencies`)

Milvus depends on:

- **etcd**: metadata store
- **object storage** (MinIO or S3): persistent segment/index files
- **message queue** (Pulsar / Kafka / Woodpecker): internal streaming + coordination

In our cluster YAML we run these **in-cluster**.

### etcd

```yaml
dependencies:
  etcd:
    inCluster:
      values:
        replicaCount: 3
        persistence:
          storageClass: milvus-storage
          size: 10Gi
```

- 3 replicas for HA.
- Each replica gets a PVC.

### storage (MinIO)

```yaml
storage:
  inCluster:
    values:
      mode: distributed
      replicas: 4
      persistence:
        size: 100Gi
```

- In-cluster MinIO, distributed mode.
- 4 replicas, each with its own PVC.

**This is where most of your storage allocation goes.**

If you already use AWS S3, you may want to switch from in-cluster MinIO to external S3 (less operational overhead).

### msgStreamType + Pulsar

```yaml
msgStreamType: pulsar
pulsar:
  inCluster:
    values:
      bookkeeper:
        replicaCount: 2
        volumes:
          journal: 20Gi
          ledgers: 50Gi
```

- Pulsar is heavier operationally.
- Milvus newer recommendations often prefer **Woodpecker** (less infrastructure).

If you want to reduce moving parts, consider migrating message queue choice (based on your Milvus version and feature requirements).

---

## 6) How this ties to Ingress / DNS

### What you’re exposing

- Milvus SDK traffic (gRPC): **Proxy** on port `19530`
- Built-in WebUI (HTTP): port `9091`
- Attu UI (HTTP): separate Deployment on port `3000`

Ingresses:

- `03_ingress-grpc.yaml` routes `cortexai.milvusdb.usecortex.ai` → service `cortexai-milvus:19530`
- `04_ingress-webui.yaml` routes `cortexai-webui.milvusdb.usecortex.ai` → service `cortexai-milvus:9091`
- `05_ingress-attu.yaml` routes `cortexai-attu.milvusdb.usecortex.ai` → service `attu:3000`

### TLS

We used cert-manager annotations to provision certs.

- The Python SDK supports TLS.
- In our README we connect using:
  - `uri="https://cortexai.milvusdb.usecortex.ai:443"`

Important constraint:

- gRPC over nginx ingress requires HTTP/2. If you hit odd gRPC issues, switching Proxy’s service to `LoadBalancer` (NLB) is the usual production approach.

---

## 7) What you will tune first

- **Query throughput / latency**: scale `queryNode` replicas + resources
- **Ingestion throughput**: scale `dataNode`
- **Index build speed**: scale `indexNode`
- **Cost / footprint**: reduce MinIO + Pulsar resources or move to managed equivalents (S3 / MSK / etc.)
- **Reliability**:
  - Keep etcd at 3 replicas
  - Ensure PVC reclaim policy matches your expectations (we used `Retain` in the StorageClass)

---

## 8) How to quickly reason about “is the cluster up?”

The operator creates many Kubernetes resources, but a few checks quickly tell you the state:

- CR health:
  - `kubectl get milvus cortexai -n milvus-cortexai -o yaml`
- Pods:
  - `kubectl get pods -n milvus-cortexai`
- Proxy service endpoints:
  - `kubectl get svc -n milvus-cortexai | grep cortexai`

---

## 9) Auth: where does the password come from?

In this setup we rely on Milvus’ standard default credentials:

- `root` / `Milvus`

This password is **not coming from a Kubernetes Secret we manage** in these manifests.

Recommended operational flow:

1. Connect once using the default password.
2. Immediately rotate it via the SDK (`utility.reset_password`).
3. Store your rotated password in your own secret manager (AWS Secrets Manager / Vault / External Secrets) and inject it into applications.
