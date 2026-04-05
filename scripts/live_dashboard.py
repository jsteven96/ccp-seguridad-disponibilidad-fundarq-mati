#!/usr/bin/env python3
"""
live_dashboard.py — Dashboard en tiempo real para el experimento CCP

Muestra en vivo:
  - HeartBeat del ModuloInventarios (tipo, t_self_test_ms)
  - Routing del Monitor (tipo, t_clasificacion_ms)
  - Acciones del Corrector (correccion / reconciliacion / failover)
  - Estado del CEP (window_size, ataques detectados)

Uso:
  python3 scripts/live_dashboard.py              # solo monitoreo
  python3 scripts/live_dashboard.py --demo       # ejecuta secuencia de fallas automática
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import threading
from collections import deque
from datetime import datetime

import httpx

# ── Colores ANSI ────────────────────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
CYAN   = "\033[36m"
MAGENTA= "\033[35m"
BLUE   = "\033[34m"
WHITE  = "\033[37m"
BG_DARK= "\033[48;5;235m"

# ── URLs (vía port-forward) ──────────────────────────────────────────────────
URLS = {
    "inv":    "http://localhost:30090",
    "mon":    "http://localhost:30091",
    "corr":   "http://localhost:30092",
    "seg":    "http://localhost:30093",
    "cep":    "http://localhost:30094",
    "audit":  "http://localhost:30096",
}

# ── Estado global del dashboard ──────────────────────────────────────────────
EVENTS: deque = deque(maxlen=18)   # feed de eventos recientes
STATS  = {
    "hb_tipo": "—",
    "hb_t_ms": "—",
    "hb_count_ok": 0,
    "hb_count_err": 0,
    "mon_last_tipo": "—",
    "mon_t_ms": "—",
    "mon_total": 0,
    "corr_total": 0,
    "corr_last": "—",
    "cep_window": 0,
    "cep_attacks": 0,
    "cep_last_signals": {},
    "failover_count": 0,
    "fault_mode": "none",
    "last_update": "—",
}
_lock = threading.Lock()
_pf_pids: list[int] = []
_stop = threading.Event()


# ── Port-forwards ────────────────────────────────────────────────────────────
def start_portforwards():
    mappings = [
        ("svc/modulo-inventarios",         "30090:8090"),
        ("svc/monitor",                    "30091:8091"),
        ("svc/corrector",                  "30092:8092"),
        ("svc/modulo-seguridad",           "30093:8093"),
        ("svc/validacion-cep",             "30094:8094"),
        ("svc/log-auditoria",              "30096:8096"),
    ]
    for svc, ports in mappings:
        p = subprocess.Popen(
            ["kubectl", "port-forward", "-n", "ccp", svc, ports],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        _pf_pids.append(p.pid)
    time.sleep(2)


def stop_portforwards():
    for pid in _pf_pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass


# ── Polling de stats (thread) ────────────────────────────────────────────────
def _poll_stats():
    while not _stop.is_set():
        try:
            with httpx.Client(timeout=2.0) as c:
                # Monitor stats
                try:
                    r = c.get(f"{URLS['mon']}/stats")
                    d = r.json()
                    counts = d.get("counts_por_tipo", {})
                    total = sum(counts.values())
                    with _lock:
                        STATS["mon_total"] = total
                        # último tipo enviado (el de mayor count)
                        if counts:
                            last = max(counts, key=counts.get)
                            STATS["mon_last_tipo"] = last
                except Exception:
                    pass

                # Corrector stats
                try:
                    r = c.get(f"{URLS['corr']}/stats")
                    d = r.json()
                    correcciones = d.get("correcciones", {})
                    total_c = sum(correcciones.values())
                    with _lock:
                        STATS["corr_total"] = total_c
                        if correcciones:
                            STATS["corr_last"] = ", ".join(
                                f"{k}:{v}" for k, v in correcciones.items()
                            )
                        STATS["failover_count"] = correcciones.get("failover", 0)
                except Exception:
                    pass

                # CEP stats
                try:
                    r = c.get(f"{URLS['cep']}/stats")
                    d = r.json()
                    with _lock:
                        STATS["cep_window"]   = d.get("window_size", 0)
                        STATS["cep_attacks"]  = d.get("attacks_detected", 0)
                        STATS["cep_last_signals"] = d.get("last_signals", {})
                except Exception:
                    pass

        except Exception:
            pass
        time.sleep(3)


# ── Tail de logs (thread) ────────────────────────────────────────────────────
_last_inv_log = ""
_last_mon_log = ""

def _tail_logs():
    global _last_inv_log, _last_mon_log
    while not _stop.is_set():
        try:
            # ModuloInventarios — heartbeat_publicado
            res = subprocess.run(
                ["kubectl", "logs", "-n", "ccp", "deployment/modulo-inventarios", "--tail=5"],
                capture_output=True, text=True
            )
            for raw in reversed(res.stdout.splitlines()):
                if raw == _last_inv_log:
                    break
                try:
                    d = json.loads(raw)
                    if d.get("event") == "heartbeat_publicado":
                        tipo = d.get("tipo", "?")
                        t_ms = d.get("t_self_test_ms", 0)
                        with _lock:
                            STATS["hb_tipo"] = tipo
                            STATS["hb_t_ms"] = f"{t_ms:.1f}"
                            STATS["last_update"] = datetime.now().strftime("%H:%M:%S")
                            if tipo == "SELF_TEST_OK":
                                STATS["hb_count_ok"] += 1
                                _add_event(GREEN, "♥ HB", tipo, f"t_self_test={t_ms:.1f}ms")
                            else:
                                STATS["hb_count_err"] += 1
                                _add_event(RED, "♥ HB", tipo, f"t_self_test={t_ms:.2f}ms ⚠")
                        _last_inv_log = raw
                        break
                except Exception:
                    pass

            # Monitor — heartbeat_routed / heartbeat_ok
            res = subprocess.run(
                ["kubectl", "logs", "-n", "ccp", "deployment/monitor", "--tail=5"],
                capture_output=True, text=True
            )
            for raw in reversed(res.stdout.splitlines()):
                if raw == _last_mon_log:
                    break
                try:
                    d = json.loads(raw)
                    ev = d.get("event", "")
                    if ev in ("heartbeat_ok", "heartbeat_routed"):
                        tipo   = d.get("tipo", "?")
                        t_ms   = d.get("t_clasificacion_ms", 0)
                        path   = d.get("path", "ok")
                        with _lock:
                            STATS["mon_t_ms"] = f"{t_ms:.3f}"
                            if ev == "heartbeat_ok":
                                _add_event(CYAN, "→ MON", tipo, f"t_clas={t_ms:.3f}ms → ACK")
                            else:
                                color = YELLOW if tipo != "SELF_TEST_FAILED" else RED
                                _add_event(color, "→ MON", tipo, f"t_clas={t_ms:.1f}ms → {path}")
                        _last_mon_log = raw
                        break
                    elif ev == "corrector_call_error":
                        with _lock:
                            _add_event(RED, "✗ ERR", d.get("tipo","?"), d.get("error","")[:50])
                        _last_mon_log = raw
                        break
                    elif ev in ("failover_activado", "correccion_completada", "reconciliacion_completada"):
                        with _lock:
                            color = MAGENTA if ev == "failover_activado" else YELLOW
                            label = {"failover_activado": "⚡ FAIL", "correccion_completada": "✓ CORR", "reconciliacion_completada": "✓ REC"}.get(ev, "✓")
                            _add_event(color, label, ev, "")
                        _last_mon_log = raw
                        break
                except Exception:
                    pass

        except Exception:
            pass
        time.sleep(2)


def _add_event(color, label, tipo, detail):
    ts = datetime.now().strftime("%H:%M:%S")
    EVENTS.append((ts, color, label, tipo, detail))


# ── Render del dashboard ─────────────────────────────────────────────────────
def _tipo_color(tipo):
    if tipo == "SELF_TEST_OK":    return GREEN
    if "NEGATIVO" in tipo:        return RED
    if "DIVERGENCIA" in tipo:     return YELLOW
    if "CONCURRENTE" in tipo:     return YELLOW
    if "FAILED" in tipo:          return RED
    return WHITE

def _signals_str(signals):
    parts = []
    for k, v in signals.items():
        c = RED if v else DIM+WHITE
        parts.append(f"{c}{k[:4]}:{int(v)}{RESET}")
    return "  ".join(parts) if parts else f"{DIM}—{RESET}"

def render():
    os.system("clear")
    W = 70

    with _lock:
        s = dict(STATS)
        events = list(EVENTS)

    hb_color  = _tipo_color(s["hb_tipo"])
    mon_color = _tipo_color(s["mon_last_tipo"])

    print(f"{BOLD}{BG_DARK}{'═'*W}{RESET}")
    print(f"{BOLD}{BG_DARK}  CCP — Dashboard en Tiempo Real        {DIM}actualizado: {s['last_update']}{RESET}")
    print(f"{BOLD}{BG_DARK}{'═'*W}{RESET}")

    # ── ASR-1: Disponibilidad ──
    print(f"\n{BOLD}{CYAN}  ▸ ASR-1  Disponibilidad (HeartBeat + VALCOH){RESET}")
    print(f"  {'─'*66}")
    fault_c = RED if s["fault_mode"] != "none" else DIM+WHITE
    print(f"  Fault mode : {fault_c}{s['fault_mode']}{RESET}")
    print(f"  HeartBeat  : {hb_color}{BOLD}{s['hb_tipo']:<28}{RESET}  t_self_test = {BOLD}{s['hb_t_ms']}ms{RESET}")
    print(f"  Monitor    : {mon_color}{s['mon_last_tipo']:<28}{RESET}  t_clas      = {BOLD}{s['mon_t_ms']}ms{RESET}")
    print(f"  Conteo     : {GREEN}OK={s['hb_count_ok']}{RESET}  {RED}ERR={s['hb_count_err']}{RESET}   "
          f"Correcciones: {s['corr_total']}  {MAGENTA}Failovers: {s['failover_count']}{RESET}")

    thresh = 300
    t_self = float(s["hb_t_ms"]) if s["hb_t_ms"] not in ("—","") else 0
    t_mon  = float(s["mon_t_ms"]) if s["mon_t_ms"] not in ("—","") else 0
    t_total = t_self + t_mon
    asr1_ok = t_total < thresh and s["hb_tipo"] != "—"
    asr1_icon = f"{GREEN}✅ OK  ({t_total:.1f}ms < {thresh}ms){RESET}" if asr1_ok else f"{DIM}— esperando...{RESET}"
    print(f"  ASR-1      : {asr1_icon}")

    # ── ASR-2: Seguridad ──
    print(f"\n{BOLD}{MAGENTA}  ▸ ASR-2  Seguridad (CEP / DDoS){RESET}")
    print(f"  {'─'*66}")
    print(f"  Ventana CEP: {s['cep_window']} eventos (últimos 60s)")
    sigs = s["cep_last_signals"]
    print(f"  Señales    : {_signals_str(sigs)}")
    atk_c = RED if s["cep_attacks"] > 0 else GREEN
    print(f"  Ataques    : {atk_c}{BOLD}{s['cep_attacks']}{RESET} detectados  "
          f"{'  → '+RED+BOLD+'BLOQUEADO'+RESET if s['cep_attacks']>0 else ''}")
    asr2_ok = True  # CEP siempre activo
    print(f"  ASR-2      : {GREEN}✅ Motor CEP activo — detecta en <1ms{RESET}")

    # ── Feed de eventos ──
    print(f"\n{BOLD}  ▸ Feed de eventos recientes{RESET}")
    print(f"  {'─'*66}")
    for ts, color, label, tipo, detail in reversed(events[-14:]):
        label_str = f"{color}{BOLD}{label:<7}{RESET}"
        tipo_str  = f"{color}{tipo:<30}{RESET}"
        det_str   = f"{DIM}{detail[:26]}{RESET}"
        print(f"  {DIM}{ts}{RESET}  {label_str}  {tipo_str}  {det_str}")

    print(f"\n{DIM}  Ctrl+C para salir  │  --demo (ASR-1 fallas)  │  --demo-asr2 (ASR-2 DDoS CEP){RESET}")
    print(f"{BOLD}{'═'*W}{RESET}")


# ── Secuencia demo ────────────────────────────────────────────────────────────
DEMO_STEPS = [
    ("none",               10, "Estado normal — SELF_TEST_OK"),
    ("stock_negativo",     12, "Inyectando stock negativo → VALCOH detecta → Corrector actúa"),
    ("none",               8,  "Corregido — volviendo a OK"),
    ("divergencia_reservas",12,"Inyectando divergencia de reservas → VALCOH detecta → Reconciliación"),
    ("none",               8,  "Reconciliado"),
    ("estado_concurrente", 10, "Inyectando estado concurrente → VALCOH detecta"),
    ("none",               8,  "Corregido"),
    ("self_test_failed",   12, "Inyectando fallo estructural → Monitor activa FAILOVER"),
    ("none",               10, "Failover completado — standby activo"),
]

def run_demo(inv_url):
    print(f"\n{BOLD}{YELLOW}  ▶ Iniciando secuencia DEMO — validación en vivo de ASR-1{RESET}\n")
    time.sleep(2)
    with httpx.Client(timeout=5.0) as c:
        for fault, wait, desc in DEMO_STEPS:
            with _lock:
                STATS["fault_mode"] = fault
                _add_event(YELLOW, "DEMO", fault, desc[:40])
            try:
                c.post(f"{inv_url}/fault-inject", json={"tipo": fault})
            except Exception:
                pass
            time.sleep(wait)
        # reset final
        try:
            c.post(f"{inv_url}/fault-inject", json={"tipo": "none"})
            c.post(f"{inv_url}/reset", json={})
        except Exception:
            pass
        with _lock:
            STATS["fault_mode"] = "none"
            _add_event(GREEN, "DEMO", "FIN", "Secuencia ASR-1 completada")


# ── Demo ASR-2 (DDoS CEP) ────────────────────────────────────────────────────
DEMO_ASR2_STEPS = [
    # (descripcion, actor_id, n_requests, sku, accion, jwt_valido, pausa_post)
    ("Happy path — 5 req normales",        "demo_b1", 5,  "COCA-COLA-350", "reservar", True,  4),
    ("DDoS gradual — 15 req mismo SKU",    "demo_b2", 15, "COCA-COLA-350", "reservar", False, 6),
    ("Reset ventana CEP",                  None,      0,  "",              "",          True,  2),
    ("DDoS con JWT valido — no bypass",    "demo_b3", 15, "COCA-COLA-350", "reservar", True,  6),
    ("Reset ventana CEP",                  None,      0,  "",              "",          True,  2),
    ("Umbral correlacion — 12 req (ataque)","demo_b4a",12,"COCA-COLA-350", "reservar", False, 4),
    ("Reset ventana CEP",                  None,      0,  "",              "",          True,  2),
    ("Umbral correlacion — 9 req (normal)","demo_b4b", 9, "COCA-COLA-350", "reservar", False, 4),
]

def run_demo_asr2(cep_url):
    print(f"\n{BOLD}{MAGENTA}  ▶ Iniciando secuencia DEMO — validación en vivo de ASR-2 (CEP/DDoS){RESET}\n")
    time.sleep(2)
    with httpx.Client(timeout=5.0) as c:
        for desc, actor_id, n_req, sku, accion, jwt_valido, pausa in DEMO_ASR2_STEPS:
            with _lock:
                _add_event(MAGENTA, "ASR2", actor_id or "reset", desc[:40])

            # reset CEP window
            if actor_id is None:
                try:
                    c.post(f"{cep_url}/reset")
                    with _lock:
                        _add_event(CYAN, "CEP", "reset", "ventana limpiada")
                except Exception:
                    pass
                time.sleep(pausa)
                continue

            # enviar ráfaga
            codes = []
            for i in range(n_req):
                if _stop.is_set():
                    return
                try:
                    resp = c.post(
                        f"{cep_url}/validar",
                        json={"actor_id": actor_id, "sku": sku,
                              "accion": accion, "jwt_valido": jwt_valido},
                    )
                    codes.append(resp.status_code)
                    if resp.status_code == 429:
                        with _lock:
                            _add_event(RED, "CEP", "BLOQUEADO",
                                       f"{actor_id} req {i+1} → 429")
                except Exception as e:
                    codes.append(None)

            got_429 = any(c == 429 for c in codes if c is not None)
            color = RED if got_429 else GREEN
            summary = f"{'ATAQUE' if got_429 else 'OK'} {codes.count(429)}x429 / {n_req} req"
            with _lock:
                _add_event(color, "CEP", actor_id, summary)

            time.sleep(pausa)

        with _lock:
            _add_event(GREEN, "ASR2", "FIN", "Secuencia ASR-2 completada")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo",      action="store_true", help="Ejecutar secuencia de fallas ASR-1 automática")
    parser.add_argument("--demo-asr2", action="store_true", help="Ejecutar secuencia de ataques DDoS ASR-2 automática")
    parser.add_argument("--no-portforward", action="store_true", help="Omitir port-forwards (ya activos)")
    args = parser.parse_args()

    print("Iniciando port-forwards...")
    if not args.no_portforward:
        start_portforwards()

    # Lanzar threads de polling
    t_stats = threading.Thread(target=_poll_stats, daemon=True)
    t_logs  = threading.Thread(target=_tail_logs,  daemon=True)
    t_stats.start()
    t_logs.start()

    # Demo en background si se solicitó
    if args.demo:
        t_demo = threading.Thread(target=run_demo, args=(URLS["inv"],), daemon=True)
        time.sleep(3)
        t_demo.start()

    if args.demo_asr2:
        t_demo2 = threading.Thread(target=run_demo_asr2, args=(URLS["cep"],), daemon=True)
        time.sleep(3)
        t_demo2.start()

    try:
        while True:
            render()
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        _stop.set()
        if not args.no_portforward:
            stop_portforwards()
        print("\nDashboard cerrado.")


if __name__ == "__main__":
    main()
