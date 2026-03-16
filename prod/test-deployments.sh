#!/bin/bash
# Test script for Cortex deployments

set -e

echo "========================================"
echo "Testing Cortex Deployments"
echo "========================================"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

test_cortex_app() {
    echo ""
    echo -e "${YELLOW}=== Testing cortex-app ===${NC}"
    
    echo -n "1. Checking pods... "
    READY=$(kubectl get pods -n cortex-app --no-headers 2>/dev/null | grep -c "1/1" || echo 0)
    TOTAL=$(kubectl get pods -n cortex-app --no-headers 2>/dev/null | wc -l | tr -d ' ')
    if [[ "$READY" -gt 0 ]]; then
        echo -e "${GREEN}✓ $READY/$TOTAL pods ready${NC}"
    else
        echo -e "${RED}✗ No pods ready${NC}"
        return 1
    fi
    
    echo -n "2. Testing internal service... "
    kubectl run test-curl --rm -i --restart=Never --image=curlimages/curl --timeout=30s -- \
        curl -s -o /dev/null -w "%{http_code}" http://cortex-app.cortex-app.svc.cluster.local/ 2>/dev/null | grep -q "200\|302" && \
        echo -e "${GREEN}✓ Service responding${NC}" || echo -e "${RED}✗ Service not responding${NC}"
    
    echo -n "3. Getting ingress info... "
    INGRESS_HOST=$(kubectl get ingress -n cortex-app cortex-app-ingress -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null)
    if [[ -n "$INGRESS_HOST" ]]; then
        echo -e "${GREEN}✓ $INGRESS_HOST${NC}"
    else
        echo -e "${YELLOW}⚠ No LoadBalancer assigned yet${NC}"
    fi
    
    echo -n "4. Checking HPA... "
    HPA_STATUS=$(kubectl get hpa -n cortex-app cortex-app-hpa -o jsonpath='{.status.currentReplicas}/{.spec.maxReplicas}' 2>/dev/null)
    echo -e "${GREEN}✓ Replicas: $HPA_STATUS${NC}"
    
    echo ""
    echo -e "${GREEN}cortex-app tests completed!${NC}"
}

test_cortex_ingestion() {
    echo ""
    echo -e "${YELLOW}=== Testing cortex-ingestion ===${NC}"
    
    # Check if namespace exists
    if ! kubectl get namespace cortex-ingestion &>/dev/null; then
        echo -e "${YELLOW}⚠ cortex-ingestion namespace not found. Not deployed yet.${NC}"
        return 0
    fi
    
    echo -n "1. Checking pods... "
    READY=$(kubectl get pods -n cortex-ingestion --no-headers 2>/dev/null | grep -c "1/1" || echo 0)
    TOTAL=$(kubectl get pods -n cortex-ingestion --no-headers 2>/dev/null | wc -l | tr -d ' ')
    if [[ "$READY" -gt 0 ]]; then
        echo -e "${GREEN}✓ $READY/$TOTAL pods ready${NC}"
    else
        echo -e "${RED}✗ No pods ready${NC}"
        return 1
    fi
    
    echo -n "2. Testing health endpoint... "
    kubectl run test-curl-ing --rm -i --restart=Never --image=curlimages/curl --timeout=30s -- \
        curl -s -o /dev/null -w "%{http_code}" http://cortex-ingestion.cortex-ingestion.svc.cluster.local/health 2>/dev/null | grep -q "200" && \
        echo -e "${GREEN}✓ Health check passed${NC}" || echo -e "${RED}✗ Health check failed${NC}"
    
    echo ""
    echo -e "${GREEN}cortex-ingestion tests completed!${NC}"
}

show_endpoints() {
    echo ""
    echo -e "${YELLOW}=== Endpoints ===${NC}"
    
    echo "cortex-app:"
    echo "  - Internal: http://cortex-app.cortex-app.svc.cluster.local/"
    INGRESS=$(kubectl get ingress -n cortex-app cortex-app-ingress -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null)
    [[ -n "$INGRESS" ]] && echo "  - External: http://$INGRESS (Host: api.usecortex.ai)"
    
    if kubectl get namespace cortex-ingestion &>/dev/null; then
        echo ""
        echo "cortex-ingestion:"
        echo "  - Internal: http://cortex-ingestion.cortex-ingestion.svc.cluster.local/"
        INGRESS_ING=$(kubectl get ingress -n cortex-ingestion cortex-ingestion-ingress -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null)
        [[ -n "$INGRESS_ING" ]] && echo "  - External: http://$INGRESS_ING (Host: ingestion.usecortex.ai)"
    fi
}

# Main
echo "Checking cluster connection..."
kubectl cluster-info &>/dev/null || { echo "Error: Cannot connect to cluster"; exit 1; }
echo -e "${GREEN}✓ Connected to cluster${NC}"

test_cortex_app
test_cortex_ingestion
show_endpoints

echo ""
echo "========================================"
echo -e "${GREEN}All tests completed!${NC}"
echo "========================================"

