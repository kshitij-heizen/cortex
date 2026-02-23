#!/usr/bin/env bash
# Shows pods running on each node, grouped by node, with namespace and status.
# Helps decide if a node can be safely drained/deleted.
# Output: prints to stdout and writes JSON to node-pod-map.json

set -euo pipefail

OUTFILE="${1:-node-pod-map.json}"

echo "Fetching node and pod data..."

# Build a JSON structure: { "node_name": { instance_type, cpu_usage, mem_usage, pods: [...] } }
# Step 1: Get node instance types
NODE_JSON=$(kubectl get nodes -o json | jq '[.items[] | {
  name: .metadata.name,
  instance_type: (.metadata.labels["node.kubernetes.io/instance-type"] // "unknown"),
  roles: ([.metadata.labels | to_entries[] | select(.key | startswith("node-role.kubernetes.io/")) | .key | split("/")[1]] | join(",")),
  taints: ([(.spec.taints // [])[] | "\(.key)=\(.value // ""):\(.effect)"] | join(", "))
}]')

# Step 2: Get all pods with node assignment
POD_JSON=$(kubectl get pods --all-namespaces -o json | jq '[.items[] | {
  name: .metadata.name,
  namespace: .metadata.namespace,
  node: (.spec.nodeName // "unassigned"),
  status: .status.phase,
  controlled_by: ((.metadata.ownerReferences // [{}])[0].kind // "none"),
  controller_name: ((.metadata.ownerReferences // [{}])[0].name // "none"),
  cpu_req: ([.spec.containers[].resources.requests.cpu // "0"] | join("+")),
  mem_req: ([.spec.containers[].resources.requests.memory // "0"] | join("+")),
  is_daemonset: (if ((.metadata.ownerReferences // [{}])[0].kind == "DaemonSet") then true else false end),
  has_local_storage: (if (.spec.volumes // [] | map(select(.emptyDir != null or .hostPath != null)) | length > 0) then true else false end),
  has_pvc: (if (.spec.volumes // [] | map(select(.persistentVolumeClaim != null)) | length > 0) then true else false end),
  pvc_names: ([(.spec.volumes // [])[] | select(.persistentVolumeClaim != null) | .persistentVolumeClaim.claimName] | join(", "))
}]')

# Step 3: Merge into per-node view
RESULT=$(jq -n --argjson nodes "$NODE_JSON" --argjson pods "$POD_JSON" '
  ($nodes | map({(.name): .}) | add) as $node_map |
  ($pods | group_by(.node)) |
  map({
    node: .[0].node,
    instance_type: ($node_map[.[0].node].instance_type // "unknown"),
    taints: ($node_map[.[0].node].taints // ""),
    pod_count: length,
    daemonset_pods: [.[] | select(.is_daemonset) | .name] | length,
    app_pods: [.[] | select(.is_daemonset | not)] | length,
    stateful_pods: [.[] | select(.has_pvc)] | length,
    pods: [.[] | select(.is_daemonset | not) | {
      namespace,
      name,
      status,
      controlled_by,
      controller_name,
      cpu_req,
      mem_req,
      has_local_storage,
      has_pvc,
      pvc_names
    }] | sort_by(.namespace, .name)
  }) | sort_by(.node) |
  map({(.node): del(.node)}) | add
')

# Write JSON
echo "$RESULT" > "$OUTFILE"
echo "JSON written to $OUTFILE"
echo ""

# Print summary to stdout
echo "$RESULT" | jq -r '
  to_entries[] |
  "═══════════════════════════════════════════════════════════════════════════════\n" +
  "NODE: \(.key)  [\(.value.instance_type)]  pods: \(.value.pod_count) (app: \(.value.app_pods), ds: \(.value.daemonset_pods), stateful: \(.value.stateful_pods))\n" +
  (if (.value.taints | length > 0) then "TAINTS: \(.value.taints)\n" else "" end) +
  "───────────────────────────────────────────────────────────────────────────────\n" +
  (.value.pods | if length == 0 then "  (only daemonset pods)\n"
   else (map(
    "  \(.namespace | . + " " * (25 - (. | length)))  \(.name | if (. | length) > 50 then .[0:47] + "..." else . end)" +
    (if .has_pvc then "  [PVC: \(.pvc_names)]" else "" end) +
    (if .has_local_storage then "  [LOCAL-STORAGE]" else "" end)
   ) | join("\n")) + "\n"
   end)
'
