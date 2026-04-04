#!/bin/bash
set -e

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "============================================================"
echo " CCP — Build & Load: modulo-inventarios"
echo "============================================================"

# 1. Build Docker image
echo ""
echo ">>> [1/5] Building Docker image ccp/modulo-inventarios:latest..."
docker build -t ccp/modulo-inventarios:latest "$BASE_DIR/services/modulo_inventarios/"
echo "    Image built."

# 2. Load into Kind cluster
echo ""
echo ">>> [2/5] Loading image into Kind cluster 'ccp-experiment'..."
kind load docker-image ccp/modulo-inventarios:latest --name ccp-experiment
echo "    Image loaded."

# 3. Apply primary deployment
echo ""
echo ">>> [3/5] Applying k8s/modulo-inventarios.yaml..."
kubectl apply -f "$BASE_DIR/k8s/modulo-inventarios.yaml"
echo "    Applied."

# 4. Apply standby deployment
echo ""
echo ">>> [4/5] Applying k8s/inv-standby.yaml..."
kubectl apply -f "$BASE_DIR/k8s/inv-standby.yaml"
echo "    Applied."

# 5. Wait for primary rollout
echo ""
echo ">>> [5/5] Waiting for rollout of modulo-inventarios (primary)..."
kubectl rollout status deployment/modulo-inventarios -n ccp --timeout=120s
echo "    Rollout complete."

echo ""
echo "============================================================"
echo " Done. Check pod status with:"
echo "   kubectl get pods -n ccp"
echo " Test health with:"
echo "   kubectl port-forward svc/modulo-inventarios 8090:8090 -n ccp &"
echo "   curl http://localhost:8090/health"
echo "============================================================"
