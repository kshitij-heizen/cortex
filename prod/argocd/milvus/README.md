# Milvus Multi-Org ArgoCD Setup

This directory contains ArgoCD manifests for deploying Milvus per organization (one app per org).

**Key difference from FalkorDB:** FalkorDB deploys per tenant (`infra/orgs/<org>/<tenant>/`), while Milvus deploys per org only (`org/<org>/`).

---

## Folder Structure

```
prod/argocd/milvus/
├── project.yaml          # ArgoCD AppProject defining permissions and destinations
├── appset.yaml           # ApplicationSet that generates one ArgoCD App per org folder
└── README.md             # This file
```

### How it works

- **`project.yaml`**: Defines an ArgoCD `AppProject` named `tenant-milvus`.
  - Source repo: `git@github.com:usecortex/argocd-infra.git`
  - Allowed destination namespaces: `milvus-*` (one per org) and `milvus-shared`
  - Allows Namespace creation (cluster-scoped)
- **`appset.yaml`**: Uses a Git generator to scan top-level org folders `org/*`.
  - For each org folder, it creates an ArgoCD Application:
    - **App name**: `milvus-<org>` (e.g. `milvus-acme2`)
    - **Namespace**: `milvus-<org>` (one namespace per org)
    - **Helm release name**: `<org>-milvus`
    - **Values file**: `/org/<org>/milvus.values.yaml`

---

## Prerequisites

1. **SSH Deploy Key**: Add to GitHub repo for ArgoCD access
   ```bash
   argocd repo add git@github.com:usecortex/argocd-infra.git \
     --ssh-private-key-path ./keys/argocd_key \
     --name argocd-infra
   ```

2. **Milvus Operator** installed cluster-wide:
   ```bash
   helm install milvus-operator \
     -n milvus-operator --create-namespace \
     --wait --wait-for-jobs \
     https://github.com/zilliztech/milvus-operator/releases/download/v1.1.2/milvus-operator-1.1.2.tgz
   ```

3. **cert-manager** installed with a working `ClusterIssuer`

4. **Ingress controller** (NGINX Inc) with class `nginx-inc`

5. **Git repo** layout:

   ```
   org/
   └── acme2/
       └── milvus.values.yaml
   ```

   Each `milvus.values.yaml` must contain at least:

   ```yaml
   org_id: acme2
   ```

---

## Installation Steps

### 1. Apply the AppProject

```bash
kubectl apply -f prod/argocd/milvus/project.yaml
```

### 2. Apply the ApplicationSet

```bash
kubectl apply -f prod/argocd/milvus/appset.yaml
```

### 3. Verify in ArgoCD UI

- Open the ArgoCD UI.
- You should see an `AppProject` named `tenant-milvus`.
- You should see one `Application` per org folder named `milvus-<org>` (e.g. `milvus-acme2`).

---

## Values File Template

Create `org/<org>/milvus.values.yaml`:

```yaml
org_id: acme2

# Optional overrides (see Helm chart values.yaml for full reference)
# namespace:
#   create: false
# ingress:
#   className: nginx-inc
#   webui:
#     enabled: true
#   grpc:
#     enabled: true
# certificate:
#   create: true
# clusterIssuer:
#   create: false
```

---

## Common Operations

### Add a new org

1. Create a new folder `org/<org>/`
2. Add `milvus.values.yaml` with `org_id: <org>`
3. Commit and push. ArgoCD will automatically create the new Application.

### Delete an org

1. Delete the folder `org/<org>/`
2. Commit and push. ArgoCD will prune the Application and its resources (including PVCs, by default).

### Force sync an app

```bash
argocd app sync milvus-<org>
```

### Check app status

```bash
argocd app get milvus-<org>
```

---

## Monitoring

```bash
# List all Milvus apps
argocd app list -l app.kubernetes.io/part-of=milvus

# Check specific org app
argocd app get milvus-<org>

# Force refresh from git
argocd app get milvus-<org> --hard-refresh

# Check sync status with operation details
argocd app get milvus-<org> --show-operation
```

---

## Troubleshooting

### Missing `*-milvus` Service / 502 Bad Gateway

- Symptom: Ingress exists but backend service not found.
- Cause: Milvus Operator is stuck (usually `MsgStreamReady: ConnectionFailed`).
- Check:
  ```bash
  kubectl -n milvus-<org> get milvus <org>
  kubectl -n milvus-<org> describe milvus <org>
  kubectl -n milvus-<org> logs -l app.kubernetes.io/name=milvus-operator
  ```
- Common fix: Ensure Pulsar proxy is Ready:
  ```bash
  kubectl -n milvus-<org> get pods | grep pulsar-proxy
  kubectl -n milvus-<org> get svc | grep pulsar-proxy
  ```

### Certificate not issued

- Symptom: Ingress TLS secret missing or cert-manager `Ready=False`.
- Check:
  ```bash
  kubectl -n milvus-<org> get certificate
  kubectl -n milvus-<org> describe certificate <org>-milvus-tls
  kubectl -n cert-manager get clusterissuer
  ```
- Fix: Ensure `ClusterIssuer` exists or enable per-tenant `clusterIssuer.create=true`.

### ArgoCD Application not created

- Symptom: No app appears for a new org folder.
- Check:
  ```bash
  argocd appset get multi-org-milvus
  ```
- Ensure the folder path matches `org/*` and contains `milvus.values.yaml`.

### App stuck syncing

```bash
argocd app terminate-op milvus-<org>
argocd app sync milvus-<org>
```

### Pods pending

Usually node resources - check Karpenter:
```bash
kubectl get nodeclaims
kubectl describe pod <pod-name> -n milvus-<org>
```

### Pulsar not ready

Milvus depends on Pulsar for message streaming. If Pulsar is unhealthy, Milvus won't start:
```bash
kubectl -n milvus-<org> get pods | grep pulsar
kubectl -n milvus-<org> logs <org>-pulsar-broker-0 --tail=100
kubectl -n milvus-<org> logs <org>-pulsar-proxy-0 --tail=100
```

---

## Architecture

```
argocd-infra repo (GitHub)
├── org/
│   ├── acme2/
│   │   └── milvus.values.yaml      # Org-specific config
│   └── bigcorp/
│       └── milvus.values.yaml
└── infra/
    └── charts/
        └── milvus/                  # Helm chart (this repo's prod/milvus/helm/milvus-chart)
```

**Flow:**
1. Create `org/<org>/milvus.values.yaml` in Git
2. ArgoCD ApplicationSet detects new folder
3. ArgoCD creates Application `milvus-<org>`
4. Helm chart deploys Milvus CR + Attu + Ingresses to `milvus-<org>` namespace
5. Milvus Operator reconciles and creates actual Milvus pods/services

---

## FalkorDB vs Milvus Deployment Comparison

| Aspect | FalkorDB | Milvus |
|--------|----------|--------|
| Generator path | `infra/orgs/*/*` | `org/*` |
| Hierarchy | Org → Tenant | Org only |
| App name | `falkordb-<org>-<tenant>` | `milvus-<org>` |
| Release name | `<org>-<tenant>-falkordb` | `<org>-milvus` |
| Values file | `infra/orgs/<org>/<tenant>/falkor.values.yaml` | `org/<org>/milvus.values.yaml` |
| Namespace | `falkordb-<org>` (shared by tenants) | `milvus-<org>` (one per org) |

---
