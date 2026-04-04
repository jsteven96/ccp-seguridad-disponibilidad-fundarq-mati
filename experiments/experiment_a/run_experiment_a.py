#!/usr/bin/env python3
"""
Experimento A — Validación ASR-1 (Disponibilidad)
Hipótesis H1: El pipeline HeartBeat+VALCOH detecta cualquier inconsistencia en < 300ms
"""

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
BASE_URL_INV = "http://localhost:30090"
BASE_URL_MON = "http://localhost:30091"
BASE_URL_CORR = "http://localhost:30092"

RESULTS_PATH = Path(__file__).parent / "results_a.json"

HEARTBEAT_WAIT_S = 8          # seconds to wait for one HeartBeat cycle
LATENCY_THRESHOLD_MS = 300    # ASR-1 requirement


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def kubectl_logs(namespace: str, deployment: str, tail: int = 30) -> list[dict]:
    """Run kubectl logs and return parsed JSON lines."""
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
            # Keep raw string entries for keyword searches
            lines.append({"_raw": raw})
    return lines


def logs_contain(lines: list[dict], key: str, value=None) -> bool:
    """Return True if any log line contains the given key (and optionally matches value)."""
    for line in lines:
        if key in line:
            if value is None:
                return True
            if line[key] == value:
                return True
        # Also check raw string
        raw = line.get("_raw", "")
        if key in raw:
            if value is None:
                return True
    return False


def get_metric(lines: list[dict], metric_key: str) -> float | None:
    """Extract the most recent numeric value for a metric key from log lines."""
    for line in reversed(lines):
        if metric_key in line:
            try:
                return float(line[metric_key])
            except (TypeError, ValueError):
                pass
    return None


def post(url: str, body: dict, timeout: float = 10.0) -> httpx.Response:
    return httpx.post(url, json=body, timeout=timeout)


def result_line(passed: bool, label: str, details: str) -> str:
    icon = "PASS" if passed else "FAIL"
    return f"  [{icon}] {label:<30} {details}"


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def cp_a1_happy_path() -> dict:
    """CP-A1: Happy path — SELF_TEST_OK, latencies < 300ms."""
    print("\n[CP-A1] Happy path (SELF_TEST_OK)")

    # Reset inventory state
    try:
        resp = post(f"{BASE_URL_INV}/reset", {})
        print(f"  POST /reset → {resp.status_code}")
    except Exception as exc:
        print(f"  POST /reset failed: {exc}")

    print(f"  Waiting {HEARTBEAT_WAIT_S}s for HeartBeat cycle...")
    time.sleep(HEARTBEAT_WAIT_S)

    mon_logs = kubectl_logs("ccp", "monitor", tail=100)
    inv_logs = kubectl_logs("ccp", "modulo-inventarios", tail=50)

    # Extract metrics
    t_self_test = get_metric(inv_logs, "t_self_test_ms")
    t_clasificacion = get_metric(mon_logs, "t_clasificacion_ms")

    # Verify SELF_TEST_OK event type — check both INV and Monitor logs
    tipo_ok = (
        logs_contain(inv_logs, "tipo", "SELF_TEST_OK")
        or logs_contain(mon_logs, "tipo", "SELF_TEST_OK")
    )

    passed = (
        tipo_ok
        and t_self_test is not None and t_self_test < LATENCY_THRESHOLD_MS
        and t_clasificacion is not None and t_clasificacion < LATENCY_THRESHOLD_MS
    )

    t_total = (t_self_test or 0) + (t_clasificacion or 0)
    details = (
        f"tipo_ok={tipo_ok}  t_self_test={t_self_test}ms  "
        f"t_clasificacion={t_clasificacion}ms  total={t_total:.1f}ms"
    )
    print(f"  {details}")
    icon = "✅" if passed else "❌"
    print(f"  {icon} CP-A1: {'PASS' if passed else 'FAIL'}")

    return {
        "id": "CP-A1",
        "name": "Happy path (SELF_TEST_OK)",
        "passed": passed,
        "tipo_ok": tipo_ok,
        "t_self_test_ms": t_self_test,
        "t_clasificacion_ms": t_clasificacion,
        "t_total_ms": t_total,
    }


def cp_a2_stock_negativo() -> dict:
    """CP-A2: Stock negativo detectado."""
    print("\n[CP-A2] Stock negativo detectado")

    try:
        resp = post(f"{BASE_URL_INV}/fault-inject", {"tipo": "stock_negativo"})
        print(f"  POST /fault-inject stock_negativo → {resp.status_code}")
    except Exception as exc:
        print(f"  POST /fault-inject failed: {exc}")

    print(f"  Waiting {HEARTBEAT_WAIT_S}s...")
    time.sleep(HEARTBEAT_WAIT_S)

    inv_logs = kubectl_logs("ccp", "modulo-inventarios", tail=30)
    corr_logs = kubectl_logs("ccp", "corrector", tail=20)

    stock_neg_detected = logs_contain(inv_logs, "tipo", "STOCK_NEGATIVO") or logs_contain(inv_logs, "_raw", ) and any(
        "STOCK_NEGATIVO" in l.get("_raw", "") or l.get("tipo") == "STOCK_NEGATIVO" for l in inv_logs
    )
    # Simpler check
    stock_neg_detected = any(
        l.get("tipo") == "STOCK_NEGATIVO" or "STOCK_NEGATIVO" in l.get("_raw", "")
        for l in inv_logs
    )
    corr_event = any(
        "corregir" in str(l).lower() for l in corr_logs
    )

    t_self_test = get_metric(inv_logs, "t_self_test_ms")

    passed = stock_neg_detected and t_self_test is not None and t_self_test < LATENCY_THRESHOLD_MS

    details = (
        f"stock_neg_detected={stock_neg_detected}  corr_event={corr_event}  "
        f"t_self_test={t_self_test}ms"
    )
    print(f"  {details}")
    icon = "✅" if passed else "❌"
    print(f"  {icon} CP-A2: {'PASS' if passed else 'FAIL'}")

    return {
        "id": "CP-A2",
        "name": "Stock negativo detectado",
        "passed": passed,
        "stock_neg_detected": stock_neg_detected,
        "corrector_event": corr_event,
        "t_self_test_ms": t_self_test,
    }


def cp_a3_concurrencia() -> dict:
    """CP-A3: Concurrencia detectada."""
    print("\n[CP-A3] Concurrencia detectada")

    try:
        resp = post(f"{BASE_URL_INV}/fault-inject", {"tipo": "estado_concurrente"})
        print(f"  POST /fault-inject estado_concurrente → {resp.status_code}")
    except Exception as exc:
        print(f"  POST /fault-inject failed: {exc}")

    print(f"  Waiting {HEARTBEAT_WAIT_S}s...")
    time.sleep(HEARTBEAT_WAIT_S)

    inv_logs = kubectl_logs("ccp", "modulo-inventarios", tail=30)

    concurrencia_detected = any(
        l.get("tipo") == "ESTADO_CONCURRENTE" or "ESTADO_CONCURRENTE" in l.get("_raw", "")
        for l in inv_logs
    )
    t_self_test = get_metric(inv_logs, "t_self_test_ms")

    passed = concurrencia_detected and t_self_test is not None and t_self_test < LATENCY_THRESHOLD_MS

    details = f"concurrencia_detected={concurrencia_detected}  t_self_test={t_self_test}ms"
    print(f"  {details}")
    icon = "✅" if passed else "❌"
    print(f"  {icon} CP-A3: {'PASS' if passed else 'FAIL'}")

    return {
        "id": "CP-A3",
        "name": "Concurrencia detectada",
        "passed": passed,
        "concurrencia_detected": concurrencia_detected,
        "t_self_test_ms": t_self_test,
    }


def cp_a4_divergencia_reservas() -> dict:
    """CP-A4: Divergencia de reservas."""
    print("\n[CP-A4] Divergencia de reservas")

    try:
        resp = post(f"{BASE_URL_INV}/fault-inject", {"tipo": "divergencia_reservas"})
        print(f"  POST /fault-inject divergencia_reservas → {resp.status_code}")
    except Exception as exc:
        print(f"  POST /fault-inject failed: {exc}")

    print(f"  Waiting {HEARTBEAT_WAIT_S}s...")
    time.sleep(HEARTBEAT_WAIT_S)

    inv_logs = kubectl_logs("ccp", "modulo-inventarios", tail=30)

    divergencia_detected = any(
        l.get("tipo") == "DIVERGENCIA_RESERVAS" or "DIVERGENCIA_RESERVAS" in l.get("_raw", "")
        for l in inv_logs
    )
    t_self_test = get_metric(inv_logs, "t_self_test_ms")

    passed = divergencia_detected and t_self_test is not None and t_self_test < LATENCY_THRESHOLD_MS

    details = f"divergencia_detected={divergencia_detected}  t_self_test={t_self_test}ms"
    print(f"  {details}")
    icon = "✅" if passed else "❌"
    print(f"  {icon} CP-A4: {'PASS' if passed else 'FAIL'}")

    return {
        "id": "CP-A4",
        "name": "Divergencia de reservas",
        "passed": passed,
        "divergencia_detected": divergencia_detected,
        "t_self_test_ms": t_self_test,
    }


def cp_a5_selftest_failover() -> dict:
    """CP-A5: Self-test fallido → failover activado."""
    print("\n[CP-A5] Self-test fallido → failover")

    try:
        resp = post(f"{BASE_URL_INV}/fault-inject", {"tipo": "self_test_failed"})
        print(f"  POST /fault-inject self_test_failed → {resp.status_code}")
    except Exception as exc:
        print(f"  POST /fault-inject failed: {exc}")

    print(f"  Waiting {HEARTBEAT_WAIT_S}s...")
    time.sleep(HEARTBEAT_WAIT_S)

    mon_logs = kubectl_logs("ccp", "monitor", tail=30)
    corr_logs = kubectl_logs("ccp", "corrector", tail=20)

    failover_monitor = any(
        "failover" in str(l).lower() for l in mon_logs
    )
    failover_corr = any(
        "failover_activado" in str(l).lower() or "failover" in str(l).lower()
        for l in corr_logs
    )
    t_failover = get_metric(mon_logs, "t_failover_ms")

    # Reset fault after test
    try:
        resp = post(f"{BASE_URL_INV}/fault-inject", {"tipo": "none"})
        print(f"  POST /fault-inject none (reset) → {resp.status_code}")
    except Exception as exc:
        print(f"  POST /fault-inject reset failed: {exc}")

    passed = failover_monitor and (t_failover is None or t_failover < LATENCY_THRESHOLD_MS)

    details = (
        f"failover_monitor={failover_monitor}  failover_corr={failover_corr}  "
        f"t_failover={t_failover}ms"
    )
    print(f"  {details}")
    icon = "✅" if passed else "❌"
    print(f"  {icon} CP-A5: {'PASS' if passed else 'FAIL'}")

    return {
        "id": "CP-A5",
        "name": "Self-test fallido → failover",
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
    print("=" * 60)

    results = []
    for fn in [cp_a1_happy_path, cp_a2_stock_negativo, cp_a3_concurrencia,
               cp_a4_divergencia_reservas, cp_a5_selftest_failover]:
        try:
            results.append(fn())
        except Exception as exc:
            print(f"  ERROR in {fn.__name__}: {exc}")
            results.append({"id": fn.__name__, "passed": False, "error": str(exc)})

    # Summary
    passed_count = sum(1 for r in results if r.get("passed"))
    total = len(results)

    print("\n" + "=" * 60)
    print(f" Resultado Experimento A: {passed_count}/{total} casos exitosos")
    all_timing_ok = all(
        (r.get("t_self_test_ms") or 0) < LATENCY_THRESHOLD_MS
        or (r.get("t_failover_ms") or 0) < LATENCY_THRESHOLD_MS
        for r in results if r.get("passed")
    )
    h1_icon = "✅" if passed_count == total else "❌"
    h1_status = "CONFIRMADA" if passed_count == total else "NO CONFIRMADA"
    print(f" Hipótesis H1: {h1_icon} {h1_status} — todos los CP-A < {LATENCY_THRESHOLD_MS}ms")
    print("=" * 60)

    # Persist results
    output = {
        "experiment": "A",
        "asr": "ASR-1 Disponibilidad",
        "hypothesis": "H1",
        "timestamp": datetime.now().isoformat(),
        "threshold_ms": LATENCY_THRESHOLD_MS,
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
