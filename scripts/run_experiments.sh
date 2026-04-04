#!/bin/bash
# Configura port-forwards, ejecuta experimentos A y B, genera reporte final.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Configurando port-forwards ==="
PF_PIDS=()

kubectl port-forward -n ccp svc/modulo-inventarios 30090:8090 &>/dev/null &
PF_PIDS+=($!)
kubectl port-forward -n ccp svc/monitor 30091:8091 &>/dev/null &
PF_PIDS+=($!)
kubectl port-forward -n ccp svc/corrector 30092:8092 &>/dev/null &
PF_PIDS+=($!)
kubectl port-forward -n ccp svc/modulo-seguridad 30093:8093 &>/dev/null &
PF_PIDS+=($!)
kubectl port-forward -n ccp svc/validacion-cep 30094:8094 &>/dev/null &
PF_PIDS+=($!)
kubectl port-forward -n ccp svc/modulo-inventarios-standby 30095:8095 &>/dev/null &
PF_PIDS+=($!)
kubectl port-forward -n ccp svc/log-auditoria 30096:8096 &>/dev/null &
PF_PIDS+=($!)

cleanup() {
    echo ""
    echo "=== Cerrando port-forwards ==="
    for pid in "${PF_PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT

sleep 3
echo "Port-forwards listos (PIDs: ${PF_PIDS[*]})"

# Install dependencies if needed
pip install -q httpx 2>/dev/null || true

echo ""
echo "=== Ejecutando Experimento A (ASR-1 Disponibilidad) ==="
python3 "$ROOT_DIR/experiments/experiment_a/run_experiment_a.py"

echo ""
echo "=== Ejecutando Experimento B (ASR-2 Seguridad) ==="
python3 "$ROOT_DIR/experiments/experiment_b/run_experiment_b.py"

echo ""
echo "=== Generando Reporte Final ==="
python3 "$ROOT_DIR/scripts/validate_asrs.py"
