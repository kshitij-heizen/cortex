# FalkorDB ArgoCD Multi-Tenant Setup

This directory contains ArgoCD resources for automated multi-tenant FalkorDB deployments.

## Architecture

```
argocd-infra repo (GitHub)
├── infra/
│   ├── charts/
│   │   └── falkordb/          # Helm chart (ArgoCD-compatible)
│   └── tenants/
│       ├── org-0/
│       │   └── falkor.values.yaml
│       └── org-1/
│           └── falkor.values.yaml
```

**To add a new tenant:** Create a folder in `infra/tenants/<org-name>/` with `falkor.values.yaml`. ArgoCD automatically detects and deploys it.

## Files

| File | Purpose |
|------|---------|
| `project.yaml` | ArgoCD AppProject - defines permissions and allowed destinations |
| `appset.yaml` | ApplicationSet - auto-generates Applications per tenant folder |

## Prerequisites

1. **SSH Deploy Key**: Add to GitHub repo for ArgoCD access
   ```bash
   argocd repo add git@github.com:usecortex/argocd-infra.git \
     --ssh-private-key-path ./keys/argocd_key \
     --name argocd-infra
   ```

2. **Wildcard TLS Certificate**: Must exist in `falkordb-shared` namespace
   ```bash
   kubectl get secret falkordb-wildcard-tls -n falkordb-shared
   ```

## Deployment Order

Apply in this order:
```bash
kubectl apply -f project.yaml
kubectl apply -f appset.yaml
```

## Problems Solved & Lessons Learned

### 1. Helm Hooks Don't Work with ArgoCD

**Problem:** The cert-copy Job used Helm hooks (`helm.sh/hook: post-install`) which ArgoCD ignores.

**Solution:** Replace Helm hooks with ArgoCD sync-waves in the `argocd-infra` chart:
```yaml
# Instead of helm.sh/hook annotations, use:
annotations:
  argocd.argoproj.io/sync-wave: "1"
```

### 2. Circular Dependency with Sync-Waves

**Problem:** Sync waited for stunnel to be healthy, but stunnel needed the TLS cert, which needed the Job to run first. Deadlock.

**Solution:** Order resources with sync-waves:

| Wave | Resources |
|------|-----------|
| `"0"` | ServiceAccount, Roles, RoleBindings, other resources |
| `"1"` | cert-copy Job (copies wildcard cert to tenant namespace) |
| `"2"` | Stunnel Deployment (starts after cert exists) |

### 3. Go Template Syntax

**Problem:** ApplicationSet uses Go templates when `goTemplate: true`. Variable access differs from default.

**Solution:** Use `.path.path` for the full path string, `.path.basename` for folder name:
```yaml
valueFiles:
  - '/{{ .path.path }}/falkor.values.yaml'  # NOT {{ .path }}
```

### 4. Cross-Namespace Resources

**Problem:** The cert-copy Job needs RBAC in both `falkordb-shared` (read cert) and tenant namespace (write cert).

**Solution:** AppProject allows `falkordb-*` and `falkordb-shared` namespaces:
```yaml
destinations:
  - namespace: 'falkordb-*'
    server: https://kubernetes.default.svc
  - namespace: 'falkordb-shared'
    server: https://kubernetes.default.svc
```

## Sync-Wave Reference (for argocd-infra chart)

Files that need sync-wave annotations:

### `06_copy-wildcard-cert.yaml`
```yaml
# RBAC resources (wave 0)
annotations:
  argocd.argoproj.io/sync-wave: "0"

# Job (wave 1)
annotations:
  argocd.argoproj.io/sync-wave: "1"
```

### `05_stunnel.yaml`
```yaml
# Deployment (wave 2)
annotations:
  argocd.argoproj.io/sync-wave: "2"
```

## Monitoring

```bash
# List all tenant apps
argocd app list

# Check specific tenant
argocd app get falkordb-org-0

# Force refresh from git
argocd app get falkordb-org-0 --hard-refresh

# Check sync status
argocd app get falkordb-org-0 --show-operation
```

## Troubleshooting

### App stuck syncing
```bash
argocd app terminate-op <app-name>
argocd app sync <app-name>
```

### Job not created
Check if sync-waves are configured correctly. Job should be wave 1, stunnel wave 2.

### Cert not copied
```bash
kubectl get jobs -n falkordb-<org>
kubectl logs job/falkordb-<org>-copy-cert -n falkordb-<org>
```

### Pods pending
Usually node resources - check Karpenter:
```bash
kubectl get nodeclaims
kubectl describe pod <pod-name> -n falkordb-<org>
```
