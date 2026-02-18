# FalkorDB Grafana Dashboard & Alerts

This folder contains Grafana dashboards and Prometheus alerts for FalkorDB.

## Files

```
grafana/
├── falkor/
│   └── dashboard.json           # Your custom dashboard (edit this)
├── setup-dashboard.sh           # Converts dashboard.json to ConfigMap
├── falkordb-dashboard-configmap.yaml  # Auto-generated ConfigMap
├── falkordb-alerts.yaml         # Prometheus alert rules for OOM prevention
└── README.md
```

## Setup (One-Time)

### 1. Enable Grafana Dashboard Sidecar

Update your `monitoring/monitoring-values.yaml`:

```yaml
grafana:
  sidecar:
    dashboards:
      enabled: true
      label: grafana_dashboard
      folder: /tmp/dashboards
      searchNamespace: ALL
```

Then upgrade the monitoring stack:
```bash
helm upgrade monitoring prometheus-community/kube-prometheus-stack \
  -n monitoring -f monitoring/monitoring-values.yaml
```

### 2. Deploy the Dashboard

```bash
./setup-dashboard.sh
```

This script:
1. Reads `falkor/dashboard.json`
2. Generates `falkordb-dashboard-configmap.yaml`
3. Applies the ConfigMap to the cluster

The Grafana sidecar will automatically pick up the dashboard.

## Updating the Dashboard

1. Edit in Grafana UI
2. Export: **Share → Export → Save to file**
3. Replace `falkor/dashboard.json` with exported file
4. Re-run: `./setup-dashboard.sh`

## Dashboard Features

- **Multi-tenant support**: Filter by namespace to view specific tenant metrics
- **Metrics included**:
  - Redis memory usage
  - Connected clients
  - Commands per second
  - FalkorDB graph statistics (nodes, edges, queries)
  - Cache hit ratio

## Prometheus Queries

```promql
# Filter by specific tenant
redis_memory_used_bytes{namespace="falkordb-cortex-soham"}

# All FalkorDB instances
sum by (namespace) (redis_connected_clients{namespace=~"falkordb-.*"})

# Graph metrics
falkordb_graph_nodes_total{namespace=~"falkordb-.*"}
falkordb_graph_edges_total{namespace=~"falkordb-.*"}
```

## Alerts Setup

Deploy memory alerts to prevent OOM kills:

```bash
kubectl apply -f falkordb-alerts.yaml
```

### Alert Rules

| Alert | Trigger | Severity | Action |
|-------|---------|----------|--------|
| `FalkorDBMemoryHigh` | Memory > 70% of request for 5m | warning | Plan to scale |
| `FalkorDBMemoryCritical` | Memory > 85% of request for 2m | critical | Scale immediately |
| `FalkorDBOOMKilled` | Container was OOM killed | critical | Scale and investigate |
| `FalkorDBRedisMemoryHigh` | Redis memory > 3GB for 5m | warning | Monitor trend |

### Response Playbook

When you receive an alert:

1. **Check current usage**:
   ```bash
   kubectl top pods -n falkordb-<tenant>
   ```

2. **Scale via values.yaml** (ArgoCD):
   ```yaml
   # Update values.yaml
   dataNode:
     resources:
       requests:
         memory: "8Gi"  # Increase from 4Gi
   ```
   Push to repo → ArgoCD syncs → KubeBlocks rolling update

3. **Or scale via OpsRequest** (immediate):
   ```bash
   kubectl apply -f - <<EOF
   apiVersion: apps.kubeblocks.io/v1alpha1
   kind: OpsRequest
   metadata:
     name: scale-memory-$(date +%s)
     namespace: falkordb-<tenant>
   spec:
     clusterName: falkordb-<tenant>
     type: VerticalScaling
     verticalScaling:
       - componentName: falkordb
         requests:
           memory: "8Gi"
         limits:
           memory: "12Gi"
   EOF
   ```
