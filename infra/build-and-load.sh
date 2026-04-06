#!/bin/bash
set -e

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "============================================================"
echo " CCP — Build & Load: todos los servicios"
echo "============================================================"

# Pares: "carpeta_servicio:nombre_imagen"
SERVICES="modulo_inventarios:modulo-inventarios corrector:corrector monitor:monitor validacion_cep:validacion-cep modulo_seguridad:modulo-seguridad log_auditoria:log-auditoria"
TOTAL=6
i=1

for pair in $SERVICES; do
  dir="${pair%%:*}"
  name="${pair##*:}"
  echo ""
  echo ">>> [$i/$TOTAL] Building ccp/${name}:latest..."
  docker build -t "ccp/${name}:latest" "$BASE_DIR/services/${dir}/" -q
  echo "    Cargando en Kind..."
  kind load docker-image "ccp/${name}:latest" --name ccp-experiment
  echo "    Done: ${name}"
  i=$((i+1))
done

# Aplicar todos los manifiestos k8s
echo ""
echo ">>> Aplicando manifiestos k8s..."
kubectl apply -f "$BASE_DIR/k8s/modulo-inventarios.yaml"
kubectl apply -f "$BASE_DIR/k8s/inv-standby.yaml"
kubectl apply -f "$BASE_DIR/k8s/corrector.yaml"
kubectl apply -f "$BASE_DIR/k8s/monitor.yaml"
kubectl apply -f "$BASE_DIR/k8s/validacion-cep.yaml"
kubectl apply -f "$BASE_DIR/k8s/modulo-seguridad.yaml"
kubectl apply -f "$BASE_DIR/k8s/log-auditoria.yaml"

echo ""
echo ">>> Esperando rollout de todos los deployments..."
for dep in modulo-inventarios modulo-inventarios-standby corrector monitor validacion-cep modulo-seguridad log-auditoria; do
  kubectl rollout status deployment/$dep -n ccp --timeout=120s
done

echo ""
echo "============================================================"
echo " Done. Verifica con: kubectl get pods -n ccp"
echo "============================================================"
