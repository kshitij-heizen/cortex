# Monitoring Stack Ingress Configuration

This directory contains Ingress resources for exposing Prometheus and Grafana services externally.

## Services

The following services are exposed via NGINX Ingress:

| Service | Ingress URL | Internal Service | Port |
|---------|-------------|------------------|------|
| **Prometheus** | http://prometheus.cortex.opengig.work | monitoring-kube-prometheus-prometheus | 9090 |
| **Grafana** | http://grafana.cortex.opengig.work | monitoring-grafana | 80 |

## Prerequisites

1. **NGINX Ingress Controller** must be installed:
   ```bash
   kubectl get pods -n ingress-nginx
   ```

2. **DNS Configuration** - Ensure DNS records point to your ingress controller:
   ```bash
   # Get ingress controller external IP
   kubectl get svc -n ingress-nginx

   # Add DNS A records:
   # prometheus.cortex.opengig.work -> <INGRESS_IP>
   # grafana.cortex.opengig.work -> <INGRESS_IP>
   ```

3. **Monitoring namespace** with Prometheus and Grafana deployed:
   ```bash
   kubectl get pods -n monitoring
   ```

## Deployment

### Quick Deploy (All Ingresses)

```bash
# Deploy both ingresses
kubectl apply -f prometheus-ingress.yaml
kubectl apply -f grafana-ingress.yaml

# Verify deployment
kubectl get ingress -n monitoring
```

### Deploy Individually

**Prometheus:**
```bash
kubectl apply -f prometheus-ingress.yaml
kubectl get ingress prometheus-ingress -n monitoring
```

**Grafana:**
```bash
kubectl apply -f grafana-ingress.yaml
kubectl get ingress grafana-ingress -n monitoring
```

## Verification

### Check Ingress Status

```bash
# List all ingresses in monitoring namespace
kubectl get ingress -n monitoring

# Detailed ingress information
kubectl describe ingress prometheus-ingress -n monitoring
kubectl describe ingress grafana-ingress -n monitoring
```

### Test Access

**Prometheus:**
```bash
# Test locally
curl http://prometheus.cortex.opengig.work

# Or open in browser
open http://prometheus.cortex.opengig.work
```

**Grafana:**
```bash
# Test locally
curl http://grafana.cortex.opengig.work

# Or open in browser
open http://grafana.cortex.opengig.work
```

### Check Ingress Logs

```bash
# Get ingress controller pod name
INGRESS_POD=$(kubectl get pods -n ingress-nginx -l app.kubernetes.io/component=controller -o jsonpath='{.items[0].metadata.name}')

# Follow logs
kubectl logs -f $INGRESS_POD -n ingress-nginx

# Filter for specific host
kubectl logs $INGRESS_POD -n ingress-nginx | grep "prometheus.cortex.opengig.work"
```

## Configuration Options

### Enable HTTPS/TLS

1. **Create TLS secret:**
   ```bash
   # Using cert-manager (recommended)
   kubectl apply -f - <<EOF
   apiVersion: cert-manager.io/v1
   kind: Certificate
   metadata:
     name: prometheus-tls
     namespace: monitoring
   spec:
     secretName: prometheus-tls-secret
     issuerRef:
       name: letsencrypt-prod
       kind: ClusterIssuer
     dnsNames:
     - prometheus.cortex.opengig.work
   EOF

   # Or manually with existing certificate
   kubectl create secret tls prometheus-tls-secret \
     --cert=path/to/tls.crt \
     --key=path/to/tls.key \
     -n monitoring
   ```

2. **Uncomment TLS section in ingress YAML:**
   ```yaml
   spec:
     tls:
     - hosts:
       - prometheus.cortex.opengig.work
       secretName: prometheus-tls-secret
   ```

3. **Enable HTTPS redirect annotation:**
   ```yaml
   annotations:
     nginx.ingress.kubernetes.io/ssl-redirect: "true"
     nginx.ingress.kubernetes.io/force-ssl-redirect: "true"
   ```

### Enable Basic Authentication

**For Prometheus:**

1. **Create htpasswd file:**
   ```bash
   # Install htpasswd if needed
   # Ubuntu/Debian: apt-get install apache2-utils
   # MacOS: brew install httpd

   # Create password file
   htpasswd -c auth prometheus-user
   # Enter password when prompted
   ```

2. **Create Kubernetes secret:**
   ```bash
   kubectl create secret generic prometheus-basic-auth \
     --from-file=auth \
     -n monitoring
   ```

3. **Uncomment auth annotations in prometheus-ingress.yaml:**
   ```yaml
   annotations:
     nginx.ingress.kubernetes.io/auth-type: basic
     nginx.ingress.kubernetes.io/auth-secret: prometheus-basic-auth
     nginx.ingress.kubernetes.io/auth-realm: 'Authentication Required - Prometheus'
   ```

**For Grafana:**

Grafana has built-in authentication, but you can add an additional layer:

```bash
# Create auth secret
htpasswd -c auth grafana-user
kubectl create secret generic grafana-basic-auth --from-file=auth -n monitoring

# Uncomment auth annotations in grafana-ingress.yaml
```

### Whitelist IP Addresses

Restrict access to specific IP ranges:

```yaml
annotations:
  nginx.ingress.kubernetes.io/whitelist-source-range: "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
  # Or specific IPs
  # nginx.ingress.kubernetes.io/whitelist-source-range: "1.2.3.4/32,5.6.7.8/32"
```

## Grafana Configuration

### Default Credentials

If using default Grafana deployment, the credentials are typically:
- **Username:** admin
- **Password:** Check Grafana secret
  ```bash
  kubectl get secret -n monitoring monitoring-grafana -o jsonpath="{.data.admin-password}" | base64 --decode ; echo
  ```

### Configure Grafana for External Access

If Grafana is behind ingress, you may need to update Grafana configuration:

```bash
kubectl edit configmap monitoring-grafana -n monitoring
```

Add/update:
```yaml
grafana.ini: |
  [server]
  domain = grafana.cortex.opengig.work
  root_url = http://grafana.cortex.opengig.work
  serve_from_sub_path = false
```

Restart Grafana:
```bash
kubectl rollout restart deployment monitoring-grafana -n monitoring
```

## Prometheus Configuration

### Configure External URL

Update Prometheus to use external URL:

```bash
kubectl edit prometheus -n monitoring monitoring-kube-prometheus-prometheus
```

Add:
```yaml
spec:
  externalUrl: http://prometheus.cortex.opengig.work
```

## Troubleshooting

### Ingress Not Working

**Check ingress controller:**
```bash
kubectl get pods -n ingress-nginx
kubectl logs -n ingress-nginx -l app.kubernetes.io/component=controller
```

**Check ingress resource:**
```bash
kubectl describe ingress prometheus-ingress -n monitoring
# Look for events and backend status
```

**Check service endpoints:**
```bash
kubectl get endpoints -n monitoring monitoring-kube-prometheus-prometheus
kubectl get endpoints -n monitoring monitoring-grafana
```

### 502 Bad Gateway

Usually indicates service or pod issues:

```bash
# Check if pods are running
kubectl get pods -n monitoring

# Check pod logs
kubectl logs -n monitoring -l app.kubernetes.io/name=prometheus
kubectl logs -n monitoring -l app.kubernetes.io/name=grafana

# Check service
kubectl describe svc monitoring-kube-prometheus-prometheus -n monitoring
```

### 404 Not Found

Check ingress configuration:

```bash
# Verify ingress exists and has correct backend
kubectl get ingress -n monitoring
kubectl describe ingress prometheus-ingress -n monitoring

# Check if service exists
kubectl get svc -n monitoring | grep -E "(prometheus|grafana)"
```

### DNS Not Resolving

```bash
# Test DNS resolution
nslookup prometheus.cortex.opengig.work
nslookup grafana.cortex.opengig.work

# Check if DNS points to ingress IP
kubectl get svc -n ingress-nginx
```

## Security Recommendations

### Production Checklist

- [ ] Enable HTTPS/TLS with valid certificates
- [ ] Enable forced HTTPS redirect
- [ ] Configure IP whitelisting (if applicable)
- [ ] Enable authentication (Basic Auth or OAuth)
- [ ] Configure network policies to restrict access
- [ ] Set up monitoring and alerting for ingress
- [ ] Regular security audits and updates
- [ ] Use strong passwords for Grafana
- [ ] Disable anonymous access in Grafana
- [ ] Enable audit logging

### Network Policies

Example network policy to restrict access:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: monitoring-ingress-policy
  namespace: monitoring
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: prometheus
  policyTypes:
  - Ingress
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          name: ingress-nginx
    ports:
    - protocol: TCP
      port: 9090
```

## Monitoring Ingress Metrics

NGINX Ingress Controller exposes Prometheus metrics:

```bash
# Check ingress metrics endpoint
INGRESS_POD=$(kubectl get pods -n ingress-nginx -l app.kubernetes.io/component=controller -o jsonpath='{.items[0].metadata.name}')
kubectl port-forward -n ingress-nginx $INGRESS_POD 10254:10254

# Access metrics
curl http://localhost:10254/metrics
```

Add to Prometheus scrape config to monitor ingress performance.

## Cleanup

Remove ingress resources:

```bash
# Remove both ingresses
kubectl delete -f prometheus-ingress.yaml
kubectl delete -f grafana-ingress.yaml

# Or individually
kubectl delete ingress prometheus-ingress -n monitoring
kubectl delete ingress grafana-ingress -n monitoring
```

## Additional Resources

- [NGINX Ingress Controller Documentation](https://kubernetes.github.io/ingress-nginx/)
- [Prometheus Documentation](https://prometheus.io/docs/)
- [Grafana Documentation](https://grafana.com/docs/)
- [Cert-Manager Documentation](https://cert-manager.io/docs/)
