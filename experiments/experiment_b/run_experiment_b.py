#!/usr/bin/env python3
"""
Experimento B — Validación ASR-2 (Seguridad)
Hipótesis H2: El motor CEP detecta ataque DDoS de capa de negocio en < 300ms
"""

import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
BASE_URL_CEP = "http://localhost:30094"
BASE_URL_SEG = "http://localhost:30093"

RESULTS_PATH = Path(__file__).parent / "results_b.json"

LATENCY_THRESHOLD_MS = 300   # ASR-2 requirement
DEFAULT_SKU = "COCA-COLA-350"


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
            lines.append({"_raw": raw})
    return lines


def get_metric(lines: list[dict], metric_key: str) -> float | None:
    """Extract the most recent numeric value for a metric key from log lines."""
    for line in reversed(lines):
        if metric_key in line:
            try:
                return float(line[metric_key])
            except (TypeError, ValueError):
                pass
    return None


def post_validar(actor_id: str, sku: str, accion: str, jwt_valido: bool,
                 timeout: float = 5.0) -> httpx.Response:
    """POST /validar to the CEP engine."""
    return httpx.post(
        f"{BASE_URL_CEP}/validar",
        json={"actor_id": actor_id, "sku": sku, "accion": accion, "jwt_valido": jwt_valido},
        timeout=timeout,
    )


def any_log_matches(lines: list[dict], key: str, value=None) -> bool:
    for line in lines:
        if key in line:
            if value is None:
                return True
            if line[key] == value:
                return True
        raw = line.get("_raw", "")
        if key in raw:
            if value is None:
                return True
    return False


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def reset_cep() -> None:
    """Reset CEP sliding window for test isolation."""
    try:
        httpx.post(f"{BASE_URL_CEP}/reset", timeout=5.0)
        print("  [CEP reset OK]")
    except Exception as exc:
        print(f"  [CEP reset failed: {exc}]")


def cp_b1_happy_path() -> dict:
    """CP-B1: Happy path — 5 requests spread over 30 s, no attack triggered."""
    print("\n[CP-B1] Happy path (sin ataque)")
    reset_cep()

    responses = []
    for i in range(5):
        try:
            resp = post_validar(
                actor_id=f"actor_b1_{i}",
                sku=DEFAULT_SKU,
                accion="reservar",
                jwt_valido=True,
            )
            responses.append(resp.status_code)
            print(f"  Request {i+1}/5 → {resp.status_code}")
        except Exception as exc:
            print(f"  Request {i+1}/5 failed: {exc}")
            responses.append(None)
        if i < 4:
            print(f"  Sleeping 6s...")
            time.sleep(6)

    false_positives = [c for c in responses if c == 429]
    passed = len(false_positives) == 0

    details = f"responses={responses}  false_positives={len(false_positives)}"
    print(f"  {details}")
    icon = "✅" if passed else "❌"
    print(f"  {icon} CP-B1: {'PASS' if passed else 'FAIL'}")

    return {
        "id": "CP-B1",
        "name": "Happy path (sin ataque)",
        "passed": passed,
        "response_codes": responses,
        "false_positives": len(false_positives),
    }


def cp_b2_ddos_gradual() -> dict:
    """CP-B2: DDoS gradual — 15 rapid requests from same actor, same SKU."""
    print("\n[CP-B2] DDoS gradual detectado")
    reset_cep()

    actor_id = "attacker_b2"
    responses = []
    t_start = time.monotonic()

    for i in range(15):
        try:
            resp = post_validar(
                actor_id=actor_id,
                sku=DEFAULT_SKU,
                accion="reservar",
                jwt_valido=False,
            )
            responses.append(resp.status_code)
            print(f"  Request {i+1}/15 → {resp.status_code}")
        except Exception as exc:
            print(f"  Request {i+1}/15 failed: {exc}")
            responses.append(None)

    elapsed_s = time.monotonic() - t_start
    print(f"  All 15 requests sent in {elapsed_s:.2f}s")

    cep_logs = kubectl_logs("ccp", "validacion-cep", tail=50)
    seg_logs = kubectl_logs("ccp", "modulo-seguridad", tail=30)

    attack_in_logs = any_log_matches(cep_logs, "attack_detected", True) or any(
        "attack_detected" in str(l) and "true" in str(l).lower() for l in cep_logs
    )
    t_deteccion = get_metric(cep_logs, "t_deteccion_ms")
    actor_bloqueado = any(
        "actor_bloqueado" in str(l).lower() or actor_id in str(l) for l in seg_logs
    )
    any_429 = any(c == 429 for c in responses if c is not None)

    passed = (
        any_429
        and attack_in_logs
        and t_deteccion is not None
        and t_deteccion < LATENCY_THRESHOLD_MS
    )

    details = (
        f"any_429={any_429}  attack_in_logs={attack_in_logs}  "
        f"actor_bloqueado={actor_bloqueado}  t_deteccion={t_deteccion}ms"
    )
    print(f"  {details}")
    icon = "✅" if passed else "❌"
    print(f"  {icon} CP-B2: {'PASS' if passed else 'FAIL'}")

    return {
        "id": "CP-B2",
        "name": "DDoS gradual detectado",
        "passed": passed,
        "any_429": any_429,
        "attack_in_logs": attack_in_logs,
        "actor_bloqueado": actor_bloqueado,
        "t_deteccion_ms": t_deteccion,
        "response_codes": responses,
    }


def cp_b3_ddos_jwt_valido() -> dict:
    """CP-B3: DDoS con JWT válido — JWT does NOT bypass CEP detection."""
    print("\n[CP-B3] DDoS con JWT válido (no bypass)")
    reset_cep()

    actor_id = "attacker_jwt_b3"
    responses = []

    for i in range(15):
        try:
            resp = post_validar(
                actor_id=actor_id,
                sku=DEFAULT_SKU,
                accion="reservar",
                jwt_valido=True,   # valid JWT — must NOT bypass detection
            )
            responses.append(resp.status_code)
            print(f"  Request {i+1}/15 → {resp.status_code}")
        except Exception as exc:
            print(f"  Request {i+1}/15 failed: {exc}")
            responses.append(None)

    cep_logs = kubectl_logs("ccp", "validacion-cep", tail=50)

    attack_detected = any_log_matches(cep_logs, "attack_detected", True) or any(
        "attack_detected" in str(l) and "true" in str(l).lower() for l in cep_logs
    )
    t_deteccion = get_metric(cep_logs, "t_deteccion_ms")
    any_429 = any(c == 429 for c in responses if c is not None)

    # PASS: attack detected despite jwt_valido=True
    passed = (
        any_429
        and attack_detected
        and t_deteccion is not None
        and t_deteccion < LATENCY_THRESHOLD_MS
    )

    details = (
        f"any_429={any_429}  attack_detected={attack_detected}  "
        f"jwt_bypassed=False  t_deteccion={t_deteccion}ms"
    )
    print(f"  {details}")
    icon = "✅" if passed else "❌"
    print(f"  {icon} CP-B3: {'PASS' if passed else 'FAIL'}")

    return {
        "id": "CP-B3",
        "name": "DDoS con JWT válido (no bypass)",
        "passed": passed,
        "any_429": any_429,
        "attack_detected": attack_detected,
        "jwt_bypassed": False,
        "t_deteccion_ms": t_deteccion,
        "response_codes": responses,
    }


def cp_b4_umbral_correlacion() -> dict:
    """CP-B4: Umbral de correlación — exactamente 2 señales deben disparar detección.

    Sub-test 1: 12 requests → rate signal + SKU concentration → ATAQUE
    Sub-test 2:  9 requests → below threshold → NO ataque
    """
    print("\n[CP-B4] Umbral de correlación (exactamente 2 señales)")

    reset_cep()
    # --- Sub-test 4a: above threshold (12 requests → rate + SKU concentration) ---
    print("  Sub-test 4a: 12 requests (umbral ≥ 2 señales esperado)")
    actor_above = "attacker_threshold_b4a"
    responses_above = []

    for i in range(12):
        try:
            resp = post_validar(
                actor_id=actor_above,
                sku=DEFAULT_SKU,
                accion="reservar",
                jwt_valido=False,
            )
            responses_above.append(resp.status_code)
        except Exception as exc:
            responses_above.append(None)
    print(f"  Responses above-threshold: {responses_above}")

    cep_logs_above = kubectl_logs("ccp", "validacion-cep", tail=50)
    attack_above = any_log_matches(cep_logs_above, "attack_detected", True) or any(
        "attack_detected" in str(l) and "true" in str(l).lower() for l in cep_logs_above
    )
    t_deteccion = get_metric(cep_logs_above, "t_deteccion_ms")
    any_429_above = any(c == 429 for c in responses_above if c is not None)

    reset_cep()
    # --- Sub-test 4b: below threshold (9 requests → should NOT trigger) ---
    print("  Sub-test 4b: 9 requests (por debajo del umbral — no debe detectar)")
    actor_below = "safe_actor_b4b"
    responses_below = []

    for i in range(9):
        try:
            resp = post_validar(
                actor_id=actor_below,
                sku=DEFAULT_SKU,
                accion="reservar",
                jwt_valido=False,
            )
            responses_below.append(resp.status_code)
        except Exception as exc:
            responses_below.append(None)
    print(f"  Responses below-threshold: {responses_below}")

    # For below-threshold, no 429 expected
    any_429_below = any(c == 429 for c in responses_below if c is not None)

    passed = (
        any_429_above
        and attack_above
        and t_deteccion is not None
        and t_deteccion < LATENCY_THRESHOLD_MS
        and not any_429_below   # below threshold should not trigger
    )

    details = (
        f"above_attack={attack_above}  any_429_above={any_429_above}  "
        f"t_deteccion={t_deteccion}ms  below_no_attack={not any_429_below}"
    )
    print(f"  {details}")
    icon = "✅" if passed else "❌"
    print(f"  {icon} CP-B4: {'PASS' if passed else 'FAIL'}")

    return {
        "id": "CP-B4",
        "name": "Umbral de correlación (exactamente 2 señales)",
        "passed": passed,
        "above_threshold": {
            "actor": actor_above,
            "requests": 12,
            "attack_detected": attack_above,
            "any_429": any_429_above,
            "t_deteccion_ms": t_deteccion,
        },
        "below_threshold": {
            "actor": actor_below,
            "requests": 9,
            "any_429": any_429_below,
            "no_false_positive": not any_429_below,
        },
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print(" Experimento B — ASR-2 Seguridad (CEP / DDoS)")
    print(f" Inicio: {datetime.now().isoformat()}")
    print("=" * 60)

    results = []
    for fn in [cp_b1_happy_path, cp_b2_ddos_gradual, cp_b3_ddos_jwt_valido,
               cp_b4_umbral_correlacion]:
        try:
            results.append(fn())
        except Exception as exc:
            print(f"  ERROR in {fn.__name__}: {exc}")
            results.append({"id": fn.__name__, "passed": False, "error": str(exc)})

    # Summary
    passed_count = sum(1 for r in results if r.get("passed"))
    total = len(results)

    print("\n" + "=" * 60)
    print(f" Resultado Experimento B: {passed_count}/{total} casos exitosos")
    h2_icon = "✅" if passed_count == total else "❌"
    h2_status = "CONFIRMADA" if passed_count == total else "NO CONFIRMADA"
    print(f" Hipótesis H2: {h2_icon} {h2_status} — todos los CP-B < {LATENCY_THRESHOLD_MS}ms")
    print("=" * 60)

    # Persist results
    output = {
        "experiment": "B",
        "asr": "ASR-2 Seguridad",
        "hypothesis": "H2",
        "timestamp": datetime.now().isoformat(),
        "threshold_ms": LATENCY_THRESHOLD_MS,
        "passed": passed_count,
        "total": total,
        "h2_confirmed": passed_count == total,
        "cases": results,
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"\n Resultados guardados en: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
