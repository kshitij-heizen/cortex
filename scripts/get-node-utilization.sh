#!/usr/bin/env bash
# Shows per-node CPU and memory utilization (requests, limits, allocatable, and actual usage).
# Requires: kubectl, jq. Metrics-server optional for actual usage.
# Compatible with Bash 3.x (macOS default).

set -euo pipefail

TMPDIR_WORK=$(mktemp -d)
trap "rm -rf $TMPDIR_WORK" EXIT

NODE_INFO="$TMPDIR_WORK/node_info.tsv"

# Check if metrics-server is available
HAS_METRICS=true
kubectl top nodes &>/dev/null || HAS_METRICS=false

# Get node metadata: name, allocatable_cpu (millicores), allocatable_mem (Ki), instance_type
kubectl get nodes -o json | jq -r '.items[] |
  [
    .metadata.name,
    (if (.status.allocatable.cpu | endswith("m")) then (.status.allocatable.cpu[:-1] | tonumber)
     else (.status.allocatable.cpu | tonumber * 1000) end),
    .status.allocatable.memory,
    (.metadata.labels["node.kubernetes.io/instance-type"] // "unknown")
  ] | @tsv' | sort > "$NODE_INFO"

# Get requests/limits per node by summing all running pods
POD_STATS="$TMPDIR_WORK/pod_stats.tsv"
kubectl get pods --all-namespaces -o json | jq -r '
  [.items[] | select(.status.phase != "Succeeded" and .status.phase != "Failed") |
    {
      node: .spec.nodeName,
      cpu_req: ([.spec.containers[].resources.requests.cpu // "0"] | map(
        if endswith("m") then (.[:-1] | tonumber)
        elif endswith("n") then (.[:-1] | tonumber / 1000000)
        else (tonumber * 1000) end) | add),
      cpu_lim: ([.spec.containers[].resources.limits.cpu // "0"] | map(
        if endswith("m") then (.[:-1] | tonumber)
        elif endswith("n") then (.[:-1] | tonumber / 1000000)
        else (tonumber * 1000) end) | add),
      mem_req: ([.spec.containers[].resources.requests.memory // "0"] | map(
        if endswith("Gi") then (.[:-2] | tonumber * 1073741824)
        elif endswith("G") then (.[:-1] | tonumber * 1000000000)
        elif endswith("Mi") then (.[:-2] | tonumber * 1048576)
        elif endswith("M") then (.[:-1] | tonumber * 1000000)
        elif endswith("Ki") then (.[:-2] | tonumber * 1024)
        elif endswith("K") then (.[:-1] | tonumber * 1000)
        elif endswith("m") then (.[:-1] | tonumber / 1000)
        elif endswith("Ti") then (.[:-2] | tonumber * 1099511627776)
        elif endswith("Pi") then (.[:-2] | tonumber * 1125899906842624)
        elif endswith("Ei") then (.[:-2] | tonumber * 1152921504606846976)
        else (tonumber // 0) end) | add),
      mem_lim: ([.spec.containers[].resources.limits.memory // "0"] | map(
        if endswith("Gi") then (.[:-2] | tonumber * 1073741824)
        elif endswith("G") then (.[:-1] | tonumber * 1000000000)
        elif endswith("Mi") then (.[:-2] | tonumber * 1048576)
        elif endswith("M") then (.[:-1] | tonumber * 1000000)
        elif endswith("Ki") then (.[:-2] | tonumber * 1024)
        elif endswith("K") then (.[:-1] | tonumber * 1000)
        elif endswith("m") then (.[:-1] | tonumber / 1000)
        elif endswith("Ti") then (.[:-2] | tonumber * 1099511627776)
        elif endswith("Pi") then (.[:-2] | tonumber * 1125899906842624)
        elif endswith("Ei") then (.[:-2] | tonumber * 1152921504606846976)
        else (tonumber // 0) end) | add)
    }
  ] | group_by(.node) | .[] |
  {
    node: .[0].node,
    cpu_req: ([.[].cpu_req] | add),
    cpu_lim: ([.[].cpu_lim] | add),
    mem_req: ([.[].mem_req] | add),
    mem_lim: ([.[].mem_lim] | add)
  } | [.node, .cpu_req, .cpu_lim, .mem_req, .mem_lim] | @tsv
' | sort > "$POD_STATS"

# Print header
echo "================================================================================================================================"
printf "%-45s %-15s %-14s %-12s %-12s %-12s\n" "NODE" "INSTANCE_TYPE" "CPU_REQ(%)" "CPU_LIM" "MEM_REQ" "MEM_LIM"
echo "================================================================================================================================"

# Join node info with pod stats and print
while IFS=$'\t' read -r node cpu_req cpu_lim mem_req mem_lim; do
  [[ -z "$node" || "$node" == "null" ]] && continue

  # Lookup node info from temp file
  node_line=$(grep "^${node}	" "$NODE_INFO" || echo "")
  if [[ -n "$node_line" ]]; then
    alloc_cpu=$(echo "$node_line" | cut -f2)
    itype=$(echo "$node_line" | cut -f4)
  else
    alloc_cpu=0
    itype="unknown"
  fi

  cpu_req_str="$(awk "BEGIN{printf \"%.1f\", ${cpu_req:-0}/1000}")c"
  cpu_lim_str="$(awk "BEGIN{printf \"%.1f\", ${cpu_lim:-0}/1000}")c"

  if [[ "${alloc_cpu:-0}" -gt 0 ]]; then
    cpu_pct="$(awk "BEGIN{printf \"%.0f\", ${cpu_req:-0}/${alloc_cpu}*100}")%"
  else
    cpu_pct="-"
  fi

  mem_req_str="$(awk "BEGIN{printf \"%.1f\", ${mem_req:-0}/1073741824}")Gi"
  mem_lim_str="$(awk "BEGIN{printf \"%.1f\", ${mem_lim:-0}/1073741824}")Gi"

  printf "%-45s %-15s %-14s %-12s %-12s %-12s\n" \
    "$node" "$itype" "${cpu_req_str}(${cpu_pct})" "$cpu_lim_str" "$mem_req_str" "$mem_lim_str"
done < "$POD_STATS"

# Show actual usage if metrics-server is available
if [[ "$HAS_METRICS" == "true" ]]; then
  echo ""
  echo "================================================================================================================================"
  echo "ACTUAL USAGE (from metrics-server)"
  echo "================================================================================================================================"
  printf "%-45s %-15s %-15s %-15s %-15s\n" "NODE" "CPU_USAGE" "CPU_%" "MEM_USAGE" "MEM_%"
  echo "--------------------------------------------------------------------------------------------------------------------------------"
  kubectl top nodes --no-headers | sort | while read -r node cpu cpu_pct mem mem_pct; do
    printf "%-45s %-15s %-15s %-15s %-15s\n" "$node" "$cpu" "$cpu_pct" "$mem" "$mem_pct"
  done
fi
