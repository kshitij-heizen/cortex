# Wildcard Certificate Setup

Two options for TLS certificates:

## Option 1: Automatic (DNS-01 Challenge)

Use cert-manager with your DNS provider to auto-issue and renew certificates.

### Supported DNS Providers

| Provider | Config File |
|----------|-------------|
| Cloudflare | `providers/cloudflare.yaml` |
| AWS Route53 | `providers/route53.yaml` |
| Google Cloud DNS | `providers/google.yaml` |
| Azure DNS | `providers/azure.yaml` |
| DigitalOcean | `providers/digitalocean.yaml` |

### Setup Steps

1. **Create the provider secret** (see provider-specific file)

2. **Apply the ClusterIssuer and Certificate**:
   ```bash
   kubectl apply -f providers/<your-provider>.yaml
   kubectl apply -f certificate.yaml
   ```

3. **Verify**:
   ```bash
   kubectl get certificate -n falkordb-shared
   # Should show READY: True
   ```

---

## Option 2: Bring Your Own Certificate

If you have your own wildcard certificate for `*.falkordb.yourdomain.com`:

```bash
# Create the secret directly
kubectl create secret tls falkordb-wildcard-tls \
  --namespace falkordb-shared \
  --cert=/path/to/fullchain.pem \
  --key=/path/to/privkey.pem
```

No cert-manager setup needed. Just ensure:
- Certificate covers `*.falkordb.yourdomain.com`
- Secret is named `falkordb-wildcard-tls` in `falkordb-shared` namespace
- You handle renewal manually or via your own process
