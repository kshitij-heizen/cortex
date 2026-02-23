#!/usr/bin/env bash
# Lists all EKS nodes with their EC2 instance type and instance family.
# Requires: kubectl with access to the cluster.

printf "%-45s %-15s %-10s\n" "NODE" "INSTANCE_TYPE" "FAMILY"
printf "%-45s %-15s %-10s\n" "----" "-------------" "------"

kubectl get nodes -o json | \
  jq -r '.items[] |
    [
      .metadata.name,
      (.metadata.labels["node.kubernetes.io/instance-type"] // "unknown")
    ] | @tsv' | \
while IFS=$'\t' read -r node instance_type; do
  family=$(echo "$instance_type" | sed 's/\.[^.]*$//')
  printf "%-45s %-15s %-10s\n" "$node" "$instance_type" "$family"
done
