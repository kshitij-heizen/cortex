helm repo add altinity https://helm.altinity.com

helm repo update altinity

helm upgrade --install clickhouse-operator \
  altinity/altinity-clickhouse-operator \
  --version 0.25.5 \
  --namespace clickhouse \
  --create-namespace


helm list --output yaml -n clickhouse | grep app_version

kubectl get deployment.apps -n clickhouse