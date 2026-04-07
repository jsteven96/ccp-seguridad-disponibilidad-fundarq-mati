#!/bin/bash
# scripts/run_extended_parallel.sh
#
# Ejecuta los experimentos A (ASR-1) y B (ASR-2) en PARALELO durante
# EXTENDED_DURATION_S segundos (default: 600 = 10 min).
#
# Uso:
#   bash scripts/run_extended_parallel.sh              # 10 min
#   EXTENDED_DURATION_S=1200 bash scripts/run_extended_parallel.sh   # 20 min

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

DURATION="${EXTENDED_DURATION_S:-600}"

echo "============================================================"
echo " CCP — Experimentos paralelos extendidos"
echo " Duración total: ${DURATION}s  (~$((DURATION/60)) min)"
echo " ASR-1 (Disponibilidad) + ASR-2 (Seguridad) en paralelo"
echo "============================================================"

# ── Port-forwards ──────────────────────────────────────────────
echo ""
echo "=== Configurando port-forwards ==="
PF_PIDS=()

kubectl port-forward -n ccp svc/modulo-inventarios         30090:8090 &>/dev/null &
PF_PIDS+=($!)
kubectl port-forward -n ccp svc/monitor                    30091:8091 &>/dev/null &
PF_PIDS+=($!)
kubectl port-forward -n ccp svc/corrector                  30092:8092 &>/dev/null &
PF_PIDS+=($!)
kubectl port-forward -n ccp svc/modulo-seguridad           30093:8093 &>/dev/null &
PF_PIDS+=($!)
kubectl port-forward -n ccp svc/validacion-cep             30094:8094 &>/dev/null &
PF_PIDS+=($!)
kubectl port-forward -n ccp svc/modulo-inventarios-standby 30095:8095 &>/dev/null &
PF_PIDS+=($!)
kubectl port-forward -n ccp svc/log-auditoria              30096:8096 &>/dev/null &
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

# ── Dependencias ───────────────────────────────────────────────
python3.11 -m pip install -q httpx 2>/dev/null || true

# ── Lanzar experimento extendido ───────────────────────────────
echo ""
echo "=== Iniciando experimentos paralelos ==="
python3.11 "$SCRIPT_DIR/run_extended_parallel.py" --duration "$DURATION"

echo ""
echo "=== Experimentos paralelos finalizados ==="
echo "    Reporte: $SCRIPT_DIR/extended_report.json"
