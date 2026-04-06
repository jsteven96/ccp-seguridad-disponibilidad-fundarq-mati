#!/usr/bin/env python3
"""
Experimento A — Validación ASR-1 (Disponibilidad)
Hipótesis H1: El pipeline HeartBeat+VALCOH detecta cualquier inconsistencia en < 300ms.

Simulación estocástica: 1500 eventos/minuto (λ=25 req/s, proceso de Poisson).
El 10% de los eventos simula condiciones de error; el 90% son órdenes normales.
Distribución de errores: stock_negativo=4%, divergencia_reservas=3%,
estado_concurrente=2%, self_test_failed=1%.
"""

import json
import math
import random
import subprocess
import time
import threading
from datetime import datetime
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
BASE_URL_INV  = "http://localhost:30090"
BASE_URL_MON  = "http://localhost:30091"
BASE_URL_CORR = "http://localhost:30092"

RESULTS_PATH = Path(__file__).parent / "results_a.json"

SIMULATION_DURATION_S = 60        # ventana de simulación: 1 minuto
LAMBDA_PER_S          = 25.0      # 25 req/s = 1500 req/min
LATENCY_THRESHOLD_MS  = 300       # ASR-1: VALCOH debe completarse en < 300ms
HEARTBEAT_WAIT_S      = 8         # tiempo para que el HeartBeat se publique

# Distribución de errores (suma = 10% del total de eventos)
ERROR_TYPES = {
    "stock_negativo":       0.04,
    "divergencia_reservas": 0.03,
    "estado_concurrente":   0.02,
    "self_test_failed":     0.01,
}
NORMAL_RATE = 1.0 - sum(ERROR_TYPES.values())   # 90%

SKUS = ["COCA-COLA-350", "AGUA-500", "ARROZ-1KG"]


# ---------------------------------------------------------------------------
# Proceso de Poisson
# ---------------------------------------------------------------------------

def poisson_arrivals(rate_per_s: float, duration_s: float) -> list[float]:
    """Genera tiempos de llegada mediante muestreo inverso de la distribución exponencial."""
    times, t = [], 0.0
    mean_inter = 1.0 / rate_per_s
    while t < duration_s:
        t += -math.log(max(1e-10, random.random())) * mean_inter
        if t < duration_s:
            times.append(t)
    return times


def classify_event() -> str:
    """Asigna tipo de evento según distribución estocástica."""
    r, cumulative = random.random(), 0.0
    for etype, prob in ERROR_TYPES.items():
        cumulative += prob
        if r < cumulative:
            return etype
    return "normal"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def kubectl_logs(namespace: str, deployment: str, tail: int = 60) -> list[dict]:
    cmd = ["kubectl", "logs", "-n", namespace, f"deployment/{deployment}", f"--tail={tail}"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    lines = []
    for raw in result.stdout.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            lines.append(json.loads(raw))
        except json.JSONDecodeError:
            lines.append({"_raw": raw})
    return lines


def logs_contain_tipo(lines: list[dict], tipo: str) -> bool:
    return any(l.get("tipo") == tipo or tipo in l.get("_raw", "") for l in lines)


def get_metric(lines: list[dict], key: str) -> float | None:
    for line in reversed(lines):
        if key in line:
            try:
                return float(line[key])
            except (TypeError, ValueError):
                pass
    return None


def post(url: str, body: dict, timeout: float = 5.0) -> int:
    try:
        return httpx.post(url, json=body, timeout=timeout).status_code
    except Exception:
        return 0


def get_req(url: str, timeout: float = 3.0) -> int:
    try:
        return httpx.get(url, timeout=timeout).status_code
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Núcleo de la simulación estocástica
# ---------------------------------------------------------------------------

def send_normal_order(sku: str) -> int:
    """Orden normal: consulta de stock o reserva de 1 unidad."""
    if random.random() < 0.5:
        return get_req(f"{BASE_URL_INV}/inventario/{sku}")
    return post(f"{BASE_URL_INV}/reservar", {"sku": sku, "cantidad": 1})


def inject_and_detect(fault_type: str) -> dict:
    """
    Inyecta un fallo, espera el siguiente ciclo de HeartBeat y verifica
    que VALCOH lo detectó en < LATENCY_THRESHOLD_MS ms.
    """
    tipo_map = {
        "stock_negativo":       "STOCK_NEGATIVO",
        "divergencia_reservas": "DIVERGENCIA_RESERVAS",
        "estado_concurrente":   "ESTADO_CONCURRENTE",
        "self_test_failed":     "SELF_TEST_FAILED",
    }
    expected = tipo_map[fault_type]

    post(f"{BASE_URL_INV}/fault-inject", {"tipo": fault_type})
    time.sleep(HEARTBEAT_WAIT_S)

    inv_logs = kubectl_logs("ccp", "modulo-inventarios", tail=60)
    detected    = logs_contain_tipo(inv_logs, expected)
    t_self_test = get_metric(inv_logs, "t_self_test_ms")

    # Limpiar fallo para no contaminar el siguiente ciclo
    post(f"{BASE_URL_INV}/fault-inject", {"tipo": "none"})

    return {
        "fault_type":       fault_type,
        "expected_tipo":    expected,
        "detected":         detected,
        "t_self_test_ms":   t_self_test,
        "within_threshold": t_self_test is not None and t_self_test < LATENCY_THRESHOLD_MS,
    }


def run_stochastic_simulation() -> dict:
    """
    Simula SIMULATION_DURATION_S segundos de carga con proceso de Poisson (λ=LAMBDA_PER_S).
    Genera ~1500 eventos totales: 90% normales + 10% errores según ERROR_TYPES.
    """
    print(f"\n[SIM] λ={LAMBDA_PER_S} req/s | duración={SIMULATION_DURATION_S}s "
          f"| objetivo≈{int(LAMBDA_PER_S*SIMULATION_DURATION_S)} eventos")
    print(f"[SIM] Distribución: {NORMAL_RATE*100:.0f}% normal  " +
          "  ".join(f"{k}={v*100:.0f}%" for k, v in ERROR_TYPES.items()))

    arrivals = poisson_arrivals(LAMBDA_PER_S, SIMULATION_DURATION_S)
    events   = [(t, classify_event()) for t in arrivals]

    normal_events = [(t, e) for t, e in events if e == "normal"]
    error_events  = [(t, e) for t, e in events if e != "normal"]

    print(f"[SIM] Generados: {len(events)} eventos  "
          f"(normales={len(normal_events)}  errores={len(error_events)})")
    for etype in ERROR_TYPES:
        c = sum(1 for _, e in error_events if e == etype)
        print(f"      · {etype}: {c}")

    # ── Hilo para órdenes normales (tráfico de fondo) ──
    normal_sent = [0]
    stop_flag   = {"stop": False}
    nq          = list(normal_events)
    nq_idx      = [0]
    sim_start   = time.monotonic()

    def _send_normals():
        while not stop_flag["stop"]:
            now = time.monotonic() - sim_start
            while nq_idx[0] < len(nq):
                t_evt, _ = nq[nq_idx[0]]
                if t_evt <= now:
                    send_normal_order(random.choice(SKUS))
                    normal_sent[0] += 1
                    nq_idx[0] += 1
                else:
                    break
            time.sleep(0.02)

    t_normal = threading.Thread(target=_send_normals, daemon=True)
    t_normal.start()

    # ── Hilo principal: inyectar errores en los momentos correctos ──
    detections = []
    for evt_time, etype in error_events:
        now  = time.monotonic() - sim_start
        wait = evt_time - now
        if wait > 0:
            time.sleep(min(wait, 2.0))
        if time.monotonic() - sim_start > SIMULATION_DURATION_S + HEARTBEAT_WAIT_S:
            break

        print(f"  [t={evt_time:.1f}s] → {etype}")
        r = inject_and_detect(etype)
        detections.append(r)
        t_ms = f"{r['t_self_test_ms']:.2f}ms" if r["t_self_test_ms"] else "N/A"
        icon = "✅" if r["detected"] else "❌"
        print(f"    {icon} detectado={r['detected']}  t_self_test={t_ms}  "
              f"dentro_umbral={r['within_threshold']}")

    stop_flag["stop"] = True
    t_normal.join(timeout=3)

    injected = len(detections)
    detected = sum(1 for r in detections if r["detected"])
    detection_rate = detected / injected if injected > 0 else 0.0
    t_vals = [r["t_self_test_ms"] for r in detections if r["t_self_test_ms"] is not None]
    t_max  = max(t_vals) if t_vals else None
    t_avg  = sum(t_vals) / len(t_vals) if t_vals else None

    print(f"\n[SIM] Normales enviadas: {normal_sent[0]}")
    print(f"[SIM] Errores inyectados: {injected}  detectados: {detected}  "
          f"tasa: {detection_rate*100:.1f}%")

    return {
        "total_events":       len(events),
        "lambda_per_s":       LAMBDA_PER_S,
        "normal_sent":        normal_sent[0],
        "errors_injected":    injected,
        "errors_detected":    detected,
        "detection_rate":     detection_rate,
        "t_self_test_max_ms": t_max,
        "t_self_test_avg_ms": t_avg,
        "detections":         detections,
    }


# ---------------------------------------------------------------------------
# Casos de prueba
# ---------------------------------------------------------------------------

def cp_a1_happy_path() -> dict:
    """CP-A1: Ciclo normal — HeartBeat SELF_TEST_OK con tiempos < 300ms."""
    print("\n[CP-A1] Happy path (SELF_TEST_OK)")
    post(f"{BASE_URL_INV}/reset", {})
    post(f"{BASE_URL_INV}/fault-inject", {"tipo": "none"})
    print(f"  Esperando {HEARTBEAT_WAIT_S}s...")
    time.sleep(HEARTBEAT_WAIT_S)

    inv_logs = kubectl_logs("ccp", "modulo-inventarios", tail=60)
    mon_logs = kubectl_logs("ccp", "monitor", tail=100)

    t_self_test     = get_metric(inv_logs, "t_self_test_ms")
    t_clasificacion = get_metric(mon_logs, "t_clasificacion_ms")
    tipo_ok = (logs_contain_tipo(inv_logs, "SELF_TEST_OK")
               or logs_contain_tipo(mon_logs, "SELF_TEST_OK"))

    passed = (
        tipo_ok
        and t_self_test is not None and t_self_test < LATENCY_THRESHOLD_MS
        and t_clasificacion is not None and t_clasificacion < LATENCY_THRESHOLD_MS
    )
    t_total = (t_self_test or 0) + (t_clasificacion or 0)
    print(f"  tipo_ok={tipo_ok}  t_self_test={t_self_test}ms  "
          f"t_clasificacion={t_clasificacion}ms  total={t_total:.1f}ms")
    print(f"  {'✅' if passed else '❌'} CP-A1: {'PASS' if passed else 'FAIL'}")
    return {
        "id": "CP-A1", "name": "Happy path (SELF_TEST_OK)",
        "passed": passed, "tipo_ok": tipo_ok,
        "t_self_test_ms": t_self_test,
        "t_clasificacion_ms": t_clasificacion,
        "t_total_ms": t_total,
    }


def cp_a2_stochastic_load() -> dict:
    """
    CP-A2: Simulación estocástica — 1500 eventos/min con carga mixta.
    Verifica que VALCOH detecta ≥95% de los errores en < 300ms bajo carga real.
    """
    print("\n[CP-A2] Simulación estocástica 1500 eventos/min")
    sim = run_stochastic_simulation()

    detection_rate = sim["detection_rate"]
    t_max          = sim["t_self_test_max_ms"]
    timing_ok      = t_max is not None and t_max < LATENCY_THRESHOLD_MS

    passed = (
        sim["errors_injected"] > 0
        and detection_rate >= 0.95        # ≥95% de errores detectados
        and timing_ok                      # todos los self-tests < 300ms
    )
    print(f"  total_eventos={sim['total_events']}  normales={sim['normal_sent']}")
    print(f"  inyectados={sim['errors_injected']}  detectados={sim['errors_detected']}")
    print(f"  detection_rate={detection_rate*100:.1f}%  t_max={t_max}ms  timing_ok={timing_ok}")
    print(f"  {'✅' if passed else '❌'} CP-A2: {'PASS' if passed else 'FAIL'}")
    return {
        "id": "CP-A2",
        "name": "Simulación estocástica 1500 eventos/min",
        "passed": passed,
        "simulation": sim,
        "detection_rate": detection_rate,
        "t_self_test_max_ms": t_max,
        "t_self_test_avg_ms": sim["t_self_test_avg_ms"],
        "timing_ok": timing_ok,
    }


def cp_a3_concurrencia() -> dict:
    """CP-A3: Estado concurrente detectado (locking optimista)."""
    print("\n[CP-A3] Estado concurrente detectado")
    post(f"{BASE_URL_INV}/fault-inject", {"tipo": "estado_concurrente"})
    time.sleep(HEARTBEAT_WAIT_S)
    inv_logs    = kubectl_logs("ccp", "modulo-inventarios", tail=40)
    detected    = logs_contain_tipo(inv_logs, "ESTADO_CONCURRENTE")
    t_self_test = get_metric(inv_logs, "t_self_test_ms")
    passed = detected and t_self_test is not None and t_self_test < LATENCY_THRESHOLD_MS
    print(f"  detected={detected}  t_self_test={t_self_test}ms")
    print(f"  {'✅' if passed else '❌'} CP-A3: {'PASS' if passed else 'FAIL'}")
    post(f"{BASE_URL_INV}/fault-inject", {"tipo": "none"})
    return {
        "id": "CP-A3", "name": "Estado concurrente detectado",
        "passed": passed, "detected": detected, "t_self_test_ms": t_self_test,
    }


def cp_a4_divergencia() -> dict:
    """CP-A4: Divergencia de reservas detectada."""
    print("\n[CP-A4] Divergencia de reservas")
    post(f"{BASE_URL_INV}/fault-inject", {"tipo": "divergencia_reservas"})
    time.sleep(HEARTBEAT_WAIT_S)
    inv_logs    = kubectl_logs("ccp", "modulo-inventarios", tail=40)
    detected    = logs_contain_tipo(inv_logs, "DIVERGENCIA_RESERVAS")
    t_self_test = get_metric(inv_logs, "t_self_test_ms")
    passed = detected and t_self_test is not None and t_self_test < LATENCY_THRESHOLD_MS
    print(f"  detected={detected}  t_self_test={t_self_test}ms")
    print(f"  {'✅' if passed else '❌'} CP-A4: {'PASS' if passed else 'FAIL'}")
    post(f"{BASE_URL_INV}/fault-inject", {"tipo": "none"})
    return {
        "id": "CP-A4", "name": "Divergencia de reservas",
        "passed": passed, "detected": detected, "t_self_test_ms": t_self_test,
    }


def cp_a5_selftest_failover() -> dict:
    """CP-A5: Self-test fallido → Monitor activa failover a INV-Standby."""
    print("\n[CP-A5] Self-test fallido → failover")
    post(f"{BASE_URL_INV}/fault-inject", {"tipo": "self_test_failed"})
    time.sleep(HEARTBEAT_WAIT_S)
    mon_logs  = kubectl_logs("ccp", "monitor",   tail=40)
    corr_logs = kubectl_logs("ccp", "corrector", tail=30)
    failover_monitor = any("failover" in str(l).lower() for l in mon_logs)
    failover_corr    = any("failover" in str(l).lower() for l in corr_logs)
    t_failover = get_metric(mon_logs, "t_failover_ms")
    post(f"{BASE_URL_INV}/fault-inject", {"tipo": "none"})
    passed = failover_monitor
    print(f"  failover_monitor={failover_monitor}  failover_corr={failover_corr}  "
          f"t_failover={t_failover}ms")
    print(f"  {'✅' if passed else '❌'} CP-A5: {'PASS' if passed else 'FAIL'}")
    return {
        "id": "CP-A5", "name": "Self-test fallido → failover",
        "passed": passed,
        "failover_monitor": failover_monitor,
        "failover_corrector": failover_corr,
        "t_failover_ms": t_failover,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print(" Experimento A — ASR-1 Disponibilidad (HeartBeat + VALCOH)")
    print(f" Inicio: {datetime.now().isoformat()}")
    print(f" Simulación: λ={LAMBDA_PER_S} req/s × {SIMULATION_DURATION_S}s "
          f"≈ {int(LAMBDA_PER_S*SIMULATION_DURATION_S)} eventos")
    print(f" Errores: {sum(ERROR_TYPES.values())*100:.0f}% del total  "
          "| Umbral VALCOH: <300ms")
    print("=" * 60)

    results = []
    for fn in [cp_a1_happy_path, cp_a2_stochastic_load, cp_a3_concurrencia,
               cp_a4_divergencia, cp_a5_selftest_failover]:
        try:
            results.append(fn())
        except Exception as exc:
            print(f"  ERROR en {fn.__name__}: {exc}")
            results.append({"id": fn.__name__, "passed": False, "error": str(exc)})

    passed_count = sum(1 for r in results if r.get("passed"))
    total        = len(results)

    print("\n" + "=" * 60)
    print(f" Resultado Experimento A: {passed_count}/{total} casos exitosos")
    h1_icon   = "✅" if passed_count == total else "❌"
    h1_status = "CONFIRMADA" if passed_count == total else "NO CONFIRMADA"
    print(f" Hipótesis H1: {h1_icon} {h1_status} — VALCOH detecta errores en <{LATENCY_THRESHOLD_MS}ms")
    print("=" * 60)

    output = {
        "experiment": "A",
        "asr": "ASR-1 Disponibilidad",
        "hypothesis": "H1",
        "timestamp": datetime.now().isoformat(),
        "threshold_ms": LATENCY_THRESHOLD_MS,
        "simulation_config": {
            "duration_s": SIMULATION_DURATION_S,
            "lambda_per_s": LAMBDA_PER_S,
            "target_events": int(LAMBDA_PER_S * SIMULATION_DURATION_S),
            "error_distribution": ERROR_TYPES,
        },
        "passed": passed_count,
        "total": total,
        "h1_confirmed": passed_count == total,
        "cases": results,
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"\n Resultados guardados en: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
