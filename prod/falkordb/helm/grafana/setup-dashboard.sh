#!/bin/bash

# Setup FalkorDB Grafana Dashboard
# This script creates a ConfigMap from dashboard.json for auto-provisioning
#
# Prerequisites:
#   - Grafana sidecar enabled (see monitoring-values.yaml)
#
# Usage:
#   ./setup-dashboard.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_JSON="$SCRIPT_DIR/falkor/dashboard.json"
CONFIGMAP_FILE="$SCRIPT_DIR/falkordb-dashboard-configmap.yaml"

# Check if dashboard.json exists
if [ ! -f "$DASHBOARD_JSON" ]; then
    echo "Error: dashboard.json not found at $DASHBOARD_JSON"
    echo "Please export your dashboard from Grafana first."
    exit 1
fi

echo "=========================================="
echo "Setting up FalkorDB Grafana Dashboard"
echo "=========================================="

# Generate ConfigMap YAML from dashboard.json
cat > "$CONFIGMAP_FILE" << 'HEADER'
# FalkorDB Grafana Dashboard ConfigMap
# Auto-generated from falkor/dashboard.json
# 
# This ConfigMap auto-provisions the dashboard into Grafana via sidecar.
#
# To update: modify falkor/dashboard.json and re-run setup-dashboard.sh
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: falkordb-grafana-dashboard
  namespace: monitoring
  labels:
    grafana_dashboard: "1"  # Required for sidecar to pick it up
data:
  falkordb-dashboard.json: |
HEADER

# Indent the JSON properly for YAML (4 spaces)
sed 's/^/    /' "$DASHBOARD_JSON" >> "$CONFIGMAP_FILE"

echo "✓ Generated ConfigMap: $CONFIGMAP_FILE"

# Apply the ConfigMap
echo "Applying ConfigMap to cluster..."
kubectl apply -f "$CONFIGMAP_FILE"

echo ""
echo "=========================================="
echo "Dashboard Setup Complete!"
echo "=========================================="
echo ""
echo "The dashboard will appear in Grafana within 1-2 minutes."
echo "Look for: FalkorDB dashboard in the Dashboards menu."
