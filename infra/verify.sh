#!/bin/bash
set -e

PASS=0
FAIL=0

check() {
  local desc="$1"
  local cmd="$2"
  if eval "$cmd" &>/dev/null; then
    echo "  ✅  $desc"
    PASS=$((PASS+1))
  else
    echo "  ❌  $desc"
    FAIL=$((FAIL+1))
  fi
}

echo "============================================================"
echo " CCP Experiment — Verificacion de Infraestructura"
echo "============================================================"

echo ""
echo "--- Cluster Kind ---"
check "Cluster 'ccp-experiment' existe" "kind get clusters | grep -q ccp-experiment"
check "3 nodos Ready" "[ \$(kubectl get nodes --no-headers 2>/dev/null | grep -c Ready) -eq 3 ]"
check "worker-node-2 tiene label role=standby" "kubectl get node ccp-experiment-worker2 -o jsonpath='{.metadata.labels.role}' 2>/dev/null | grep -q standby"

echo ""
echo "--- Namespaces ---"
check "Namespace 'ccp' existe"       "kubectl get ns ccp &>/dev/null"
check "Namespace 'data' existe"      "kubectl get ns data &>/dev/null"
check "Namespace 'messaging' existe" "kubectl get ns messaging &>/dev/null"

echo ""
echo "--- NATS JetStream ---"
check "Pod NATS Running en messaging" "kubectl get pods -n messaging --no-headers 2>/dev/null | grep nats | grep -q Running"

echo ""
echo "--- MongoDB Replica Set ---"
check "mongodb-0 Running en data" "kubectl get pods -n data --no-headers 2>/dev/null | grep mongodb-0 | grep -q Running"
check "mongodb-1 Running en data" "kubectl get pods -n data --no-headers 2>/dev/null | grep mongodb-1 | grep -q Running"

echo ""
echo "--- Streams NATS ---"
# Port-forward temporal
kubectl port-forward svc/nats -n messaging 4222:4222 &>/dev/null &
PF_PID=$!
sleep 2
check "Stream HEARTBEAT_INVENTARIO" "nats stream info HEARTBEAT_INVENTARIO --server nats://localhost:4222"
check "Stream CORRECCION"           "nats stream info CORRECCION --server nats://localhost:4222"
check "Stream FAILOVER"             "nats stream info FAILOVER --server nats://localhost:4222"
kill $PF_PID 2>/dev/null || true

echo ""
echo "============================================================"
echo " Resultado: $PASS pasaron, $FAIL fallaron"
echo "============================================================"

[ $FAIL -eq 0 ] && exit 0 || exit 1
