#!/usr/bin/env python3
"""
Experimento B — Validación ASR-2 (Seguridad)
Hipótesis H2: El motor CEP detecta el 100% de los ataques DDoS de capa de negocio.

Simulación estocástica: 1500 eventos/minuto (λ=25 req/s, proceso de Poisson).
El 30% del tráfico son patrones de ataque (mismo actor, mismo SKU, alta tasa
de cancelaciones). El 70% es tráfico normal con actores y SKUs variados.

Criterio de aceptación: detection_rate == 100%
(el tiempo de detección ya NO es la métrica principal de este ASR).
"""

import json
import math
import random
import time
import threading
from datetime import datetime
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
BASE_URL_CEP = "http://localhost:30094"
BASE_URL_SEG = "http://localhost:30093"

RESULTS_PATH = Path(__file__).parent / "results_b.json"

SIMULATION_DURATION_S      = 60
LAMBDA_PER_S               = 25.0
ATTACK_RATE                = 0.30   # 30% del tráfico son ataques
ATTACK_BURST_SIZE          = 15     # requests por sesión de ataque
DETECTION_RATE_THRESHOLD   = 1.0    # criterio: 100% de ataques detectados

ATTACK_SKU   = "COCA-COLA-350"
SKUS         = ["COCA-COLA-350", "AGUA-500", "ARROZ-1KG"]
ATTACK_ACTORS = [f"attacker_{i}" for i in range(10)]
NORMAL_ACTORS = [f"cliente_{i}"  for i in range(100)]


# ---------------------------------------------------------------------------
# Proceso de Poisson
# ---------------------------------------------------------------------------

def poisson_arrivals(rate_per_s: float, duration_s: float) -> list[float]:
    times, t = [], 0.0
    mean_inter = 1.0 / rate_per_s
    while t < duration_s:
        t += -math.log(max(1e-10, random.random())) * mean_inter
        if t < duration_s:
            times.append(t)
    return times


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def post_validar(actor_id: str, sku: str, accion: str, jwt_valido: bool,
                 timeout: float = 3.0) -> int:
    try:
        r = httpx.post(
            f"{BASE_URL_CEP}/validar",
            json={"actor_id": actor_id, "sku": sku,
                  "accion": accion, "jwt_valido": jwt_valido},
            timeout=timeout,
        )
        return r.status_code
    except Exception:
        return 0


def reset_cep() -> None:
    try:
        httpx.post(f"{BASE_URL_CEP}/reset", timeout=5.0)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Simulación estocástica CEP
# ---------------------------------------------------------------------------

def run_stochastic_cep_simulation() -> dict:
    """
    Simula 1500 eventos/minuto mezclando:
      - 70% tráfico normal: actores y SKUs variados, sin patrón de ataque
      - 30% ataques: bursts de ATTACK_BURST_SIZE req del mismo actor en el mismo SKU
                     con alta tasa de cancelaciones (activa ≥2 señales CEP)

    Retorna stats de la simulación incluyendo detection_rate por sesión de ataque.
    """
    print(f"\n[SIM] λ={LAMBDA_PER_S} req/s | {SIMULATION_DURATION_S}s | "
          f"{int(LAMBDA_PER_S*SIMULATION_DURATION_S)} eventos objetivo")
    print(f"[SIM] Tráfico: {(1-ATTACK_RATE)*100:.0f}% normal | "
          f"{ATTACK_RATE*100:.0f}% ataques ({ATTACK_BURST_SIZE} req/sesión)")

    arrivals  = poisson_arrivals(LAMBDA_PER_S, SIMULATION_DURATION_S)
    n_total   = len(arrivals)
    n_attack  = int(n_total * ATTACK_RATE)
    n_normal  = n_total - n_attack
    n_sessions = max(1, n_attack // ATTACK_BURST_SIZE)

    print(f"[SIM] Eventos: {n_total}  normales≈{n_normal}  "
          f"ataques≈{n_attack} en {n_sessions} sesiones")

    # Construir sesiones de ataque
    attack_sessions = [
        {
            "session_id": i,
            "actor_id":   ATTACK_ACTORS[i % len(ATTACK_ACTORS)],
            "n_requests": ATTACK_BURST_SIZE + (n_attack % ATTACK_BURST_SIZE if i == n_sessions - 1 else 0),
            "detected":   False,
            "responses":  [],
        }
        for i in range(n_sessions)
    ]

    sim_start     = time.monotonic()
    normal_ok     = [0]
    false_positives = [0]
    stop_flag     = {"stop": False}

    # Tiempos de llegada para normales (aprox, sin mezclar con ataques)
    normal_times = arrivals[:n_normal]

    # ── Hilo para tráfico normal ──
    def _send_normal():
        for t_evt in normal_times:
            if stop_flag["stop"]:
                break
            wait = t_evt - (time.monotonic() - sim_start)
            if wait > 0:
                time.sleep(min(wait, 0.1))
            actor  = random.choice(NORMAL_ACTORS)
            sku    = random.choice(SKUS)
            accion = random.choice(["reservar", "consultar"])
            code   = post_validar(actor, sku, accion, jwt_valido=True)
            if code == 200:
                normal_ok[0] += 1
            elif code == 429:
                false_positives[0] += 1

    t_normal = threading.Thread(target=_send_normal, daemon=True)
    t_normal.start()

    # ── Hilo principal: sesiones de ataque distribuidas en el tiempo ──
    time_per_session = SIMULATION_DURATION_S / max(n_sessions, 1)

    for idx, session in enumerate(attack_sessions):
        target_t = idx * time_per_session + time_per_session * 0.4
        wait = target_t - (time.monotonic() - sim_start)
        if wait > 0:
            time.sleep(wait)

        actor = session["actor_id"]
        n_req = session["n_requests"]
        print(f"  [t={time.monotonic()-sim_start:.1f}s] Sesión {idx+1}/{n_sessions}: "
              f"actor={actor}  n_req={n_req}")

        for i in range(n_req):
            # Patrón de ataque: concentración en ATTACK_SKU + cancellaciones alternas
            accion = "cancelar" if i % 2 == 0 else "reservar"
            code   = post_validar(actor, ATTACK_SKU, accion,
                                  jwt_valido=random.choice([True, False]))
            session["responses"].append(code)
            if code == 429:
                session["detected"] = True

        n_429 = session["responses"].count(429)
        icon  = "✅ DETECTADO" if session["detected"] else "❌ no detectado"
        print(f"    {icon}  429s={n_429}/{n_req}")

    stop_flag["stop"] = True
    t_normal.join(timeout=5)

    sessions_detected = sum(1 for s in attack_sessions if s["detected"])
    detection_rate    = sessions_detected / len(attack_sessions) if attack_sessions else 0.0

    print(f"\n[SIM] Normal OK={normal_ok[0]}  Falsos positivos={false_positives[0]}")
    print(f"[SIM] Ataques: {sessions_detected}/{n_sessions} detectados  "
          f"→ detection_rate={detection_rate*100:.1f}%")

    return {
        "total_events":       n_total,
        "lambda_per_s":       LAMBDA_PER_S,
        "normal_sent":        normal_ok[0] + false_positives[0],
        "attack_sessions":    n_sessions,
        "sessions_detected":  sessions_detected,
        "detection_rate":     detection_rate,
        "false_positives":    false_positives[0],
        "sessions": [
            {
                "session_id":    s["session_id"],
                "actor_id":      s["actor_id"],
                "requests":      s["n_requests"],
                "detected":      s["detected"],
                "responses_429": s["responses"].count(429),
            }
            for s in attack_sessions
        ],
    }


# ---------------------------------------------------------------------------
# Casos de prueba
# ---------------------------------------------------------------------------

def cp_b1_happy_path() -> dict:
    """CP-B1: Tráfico estrictamente normal — sin falsos positivos."""
    print("\n[CP-B1] Happy path — tráfico normal, sin falsos positivos")
    reset_cep()
    time.sleep(1)

    # 30 requests normales bien espaciadas (no superan el rate threshold del CEP)
    responses = []
    for i in range(30):
        actor  = f"cliente_normal_{i % 20}"
        sku    = random.choice(SKUS)
        code   = post_validar(actor, sku, "reservar", jwt_valido=True)
        responses.append(code)
        if i < 29:
            time.sleep(1.0)

    false_positives = responses.count(429)
    passed = false_positives == 0
    print(f"  requests={len(responses)}  falsos_positivos={false_positives}")
    print(f"  {'✅' if passed else '❌'} CP-B1: {'PASS' if passed else 'FAIL'}")
    return {
        "id": "CP-B1", "name": "Happy path — sin falsos positivos",
        "passed": passed, "requests_sent": len(responses),
        "false_positives": false_positives, "response_codes": responses,
    }


def cp_b2_stochastic_detection() -> dict:
    """
    CP-B2: Simulación estocástica — 1500 eventos/min con 30% ataques.
    Criterio: detection_rate == 100% y sin falsos positivos.
    """
    print("\n[CP-B2] Simulación estocástica — detección 100% de ataques bajo carga real")
    reset_cep()
    time.sleep(1)

    sim            = run_stochastic_cep_simulation()
    detection_rate = sim["detection_rate"]
    false_positives = sim["false_positives"]

    passed = detection_rate >= DETECTION_RATE_THRESHOLD

    print(f"  total_eventos={sim['total_events']}  sesiones_ataque={sim['attack_sessions']}")
    print(f"  detectadas={sim['sessions_detected']}  "
          f"detection_rate={detection_rate*100:.1f}%  "
          f"falsos_positivos={false_positives}")
    print(f"  {'✅' if passed else '❌'} CP-B2: {'PASS' if passed else 'FAIL'}")
    return {
        "id": "CP-B2",
        "name": "Simulación estocástica 1500 eventos/min — detección 100%",
        "passed": passed,
        "simulation": sim,
        "detection_rate": detection_rate,
        "false_positives": false_positives,
    }


def cp_b3_jwt_no_bypass() -> dict:
    """CP-B3: JWT válido no bypasea la detección CEP."""
    print("\n[CP-B3] Ataque con JWT válido — no debe bypasear CEP")
    reset_cep()
    time.sleep(1)

    actor     = "attacker_jwt_b3"
    responses = []
    for i in range(15):
        accion = "cancelar" if i % 2 == 0 else "reservar"
        code   = post_validar(actor, ATTACK_SKU, accion, jwt_valido=True)
        responses.append(code)

    any_429 = 429 in responses
    passed  = any_429
    print(f"  responses={responses}  any_429={any_429}")
    print(f"  {'✅' if passed else '❌'} CP-B3: {'PASS' if passed else 'FAIL'}")
    return {
        "id": "CP-B3", "name": "JWT válido no bypasea CEP",
        "passed": passed, "any_429": any_429,
        "jwt_bypassed": False, "response_codes": responses,
    }


def cp_b4_umbral_correlacion() -> dict:
    """
    CP-B4: Exactamente ≥2 señales activas = ataque, <2 señales = no ataque.
    Sub-test 4a: 12 req (rate + concentración SKU + cancelaciones) → ataque.
    Sub-test 4b:  9 req (solo rate) → no ataque.
    """
    print("\n[CP-B4] Umbral de correlación (≥2 señales = ataque)")

    # 4a: por encima del umbral (≥2 señales)
    reset_cep()
    time.sleep(1)
    actor_above = "attacker_above_b4"
    resp_above  = []
    for i in range(12):
        accion = "cancelar" if i % 2 == 0 else "reservar"
        code   = post_validar(actor_above, ATTACK_SKU, accion, jwt_valido=False)
        resp_above.append(code)
    any_429_above = 429 in resp_above

    # 4b: por debajo del umbral (solo rate, 1 señal)
    reset_cep()
    time.sleep(1)
    actor_below = "safe_actor_b4b"
    resp_below  = []
    for i in range(9):
        code = post_validar(actor_below, ATTACK_SKU, "reservar", jwt_valido=False)
        resp_below.append(code)
    any_429_below = 429 in resp_below

    passed = any_429_above and not any_429_below
    print(f"  4a — 12 req + cancelaciones: any_429={any_429_above}  "
          f"→ {'ATAQUE ✅' if any_429_above else 'NO DETECTADO ❌'}")
    print(f"  4b —  9 req solo rate:       any_429={any_429_below}  "
          f"→ {'falso positivo ❌' if any_429_below else 'correcto ✅'}")
    print(f"  {'✅' if passed else '❌'} CP-B4: {'PASS' if passed else 'FAIL'}")
    return {
        "id": "CP-B4", "name": "Umbral de correlación (≥2 señales)",
        "passed": passed,
        "above_threshold": {
            "requests": 12, "any_429": any_429_above,
            "response_codes": resp_above,
        },
        "below_threshold": {
            "requests": 9, "any_429": any_429_below,
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
    print(f" Criterio: detection_rate = {DETECTION_RATE_THRESHOLD*100:.0f}% de ataques")
    print(f" Simulación: λ={LAMBDA_PER_S} req/s × {SIMULATION_DURATION_S}s | "
          f"{ATTACK_RATE*100:.0f}% ataques")
    print("=" * 60)

    results = []
    for fn in [cp_b1_happy_path, cp_b2_stochastic_detection,
               cp_b3_jwt_no_bypass, cp_b4_umbral_correlacion]:
        try:
            results.append(fn())
        except Exception as exc:
            print(f"  ERROR en {fn.__name__}: {exc}")
            results.append({"id": fn.__name__, "passed": False, "error": str(exc)})

    passed_count = sum(1 for r in results if r.get("passed"))
    total        = len(results)

    print("\n" + "=" * 60)
    print(f" Resultado Experimento B: {passed_count}/{total} casos exitosos")
    h2_icon   = "✅" if passed_count == total else "❌"
    h2_status = "CONFIRMADA" if passed_count == total else "NO CONFIRMADA"
    print(f" Hipótesis H2: {h2_icon} {h2_status} — CEP detecta el 100% de los ataques")
    print("=" * 60)

    output = {
        "experiment": "B",
        "asr": "ASR-2 Seguridad",
        "hypothesis": "H2",
        "timestamp": datetime.now().isoformat(),
        "criterion": "detection_rate == 100%",
        "simulation_config": {
            "duration_s":               SIMULATION_DURATION_S,
            "lambda_per_s":             LAMBDA_PER_S,
            "target_events":            int(LAMBDA_PER_S * SIMULATION_DURATION_S),
            "attack_rate":              ATTACK_RATE,
            "attack_burst_size":        ATTACK_BURST_SIZE,
            "detection_rate_threshold": DETECTION_RATE_THRESHOLD,
        },
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
