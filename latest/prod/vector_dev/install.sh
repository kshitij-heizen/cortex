#!/bin/bash

# Add Vector Helm repo
helm repo add vector https://helm.vector.dev
helm repo update

# Install Vector as Stateless Aggregator
helm upgrade --install vector vector/vector \
  --namespace vector \
  --create-namespace \
  -f prod/vector_dev/values.yaml
