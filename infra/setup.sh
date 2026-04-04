#!/bin/bash
set -e

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
INFRA_DIR="$BASE_DIR/infra"

echo "============================================================"
echo " CCP Experiment — Infrastructure Setup"
echo "============================================================"

# --- 1. Crear cluster Kind ---
echo ""
echo ">>> [1/6] Creando cluster Kind 'ccp-experiment'..."
if kind get clusters 2>/dev/null | grep -q "ccp-experiment"; then
  echo "    Cluster ya existe, omitiendo."
else
  kind create cluster --name ccp-experiment --config "$INFRA_DIR/kind-config.yaml"
  echo "    Cluster creado."
fi

# --- 2. Namespaces ---
echo ""
echo ">>> [2/6] Creando namespaces..."
kubectl apply -f "$INFRA_DIR/namespaces.yaml"

# --- 3. Etiquetar nodos ---
echo ""
echo ">>> [3/6] Etiquetando nodos..."
kubectl label nodes ccp-experiment-worker  role=primary --overwrite 2>/dev/null || true
kubectl label nodes ccp-experiment-worker2 role=standby --overwrite 2>/dev/null || true
echo "    Nodos etiquetados."

# --- 4. Helm repos ---
echo ""
echo ">>> [4/6] Instalando NATS JetStream via Helm..."
helm repo add nats    https://nats-io.github.io/k8s/helm/charts/ 2>/dev/null || true
helm repo add bitnami https://charts.bitnami.com/bitnami          2>/dev/null || true
helm repo update

if helm status nats -n messaging &>/dev/null; then
  echo "    NATS ya instalado, omitiendo."
else
  helm install nats nats/nats -n messaging -f "$INFRA_DIR/nats-values.yaml" --wait --timeout 3m
  echo "    NATS instalado."
fi

# --- 5. MongoDB ---
echo ""
echo ">>> [5/6] Instalando MongoDB Replica Set via Helm..."
if helm status mongodb -n data &>/dev/null; then
  echo "    MongoDB ya instalado, omitiendo."
else
  helm install mongodb bitnami/mongodb -n data -f "$INFRA_DIR/mongodb-values.yaml" --wait --timeout 5m
  echo "    MongoDB instalado."
fi

# --- 6. Streams NATS ---
echo ""
echo ">>> [6/6] Creando streams NATS..."

# Port-forward en background
kubectl port-forward svc/nats -n messaging 4222:4222 &
PF_PID=$!
sleep 3

# Instalar nats CLI si no está disponible
if ! which nats &>/dev/null; then
  echo "    Instalando NATS CLI..."
  brew install nats-io/nats-tools/nats 2>/dev/null || \
    curl -sf https://binaries.nats.dev/nats-io/natscli/nats@latest | sh
fi

nats stream add HEARTBEAT_INVENTARIO \
  --subjects "heartbeat.inventario.*" \
  --storage memory --replicas 1 \
  --retention limits --max-msgs 10000 --max-age 1h \
  --server nats://localhost:4222 2>/dev/null || \
  echo "    (Stream HEARTBEAT_INVENTARIO ya existe)"

nats stream add CORRECCION \
  --subjects "correccion.*" \
  --storage memory --replicas 1 \
  --retention limits --max-msgs 10000 --max-age 1h \
  --server nats://localhost:4222 2>/dev/null || \
  echo "    (Stream CORRECCION ya existe)"

nats stream add FAILOVER \
  --subjects "failover.*" \
  --storage memory --replicas 1 \
  --retention limits --max-msgs 10000 --max-age 1h \
  --server nats://localhost:4222 2>/dev/null || \
  echo "    (Stream FAILOVER ya existe)"

kill $PF_PID 2>/dev/null || true

echo ""
echo "============================================================"
echo " Setup completado. Ejecuta 'infra/verify.sh' para verificar."
echo "============================================================"
