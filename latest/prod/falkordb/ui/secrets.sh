# Copy wildcard TLS secret from shared ns into dashboard ns
kubectl get secret falkordb-wildcard-tls -n falkordb-shared -o yaml \
  | sed 's/namespace: falkordb-shared/namespace: falkordb-dashboard/' \
  | kubectl apply -f -

kubectl -n falkordb-dashboard get secret falkordb-wildcard-tls
kubectl -n falkordb-dashboard describe ingress falkordb-dashboard