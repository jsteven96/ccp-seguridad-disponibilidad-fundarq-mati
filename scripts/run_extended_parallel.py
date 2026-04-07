#!/usr/bin/env python3
"""
run_extended_parallel.py — Experimentos A y B en paralelo durante N minutos.

Lanza ambos experimentos en hilos independientes. Cada hilo repite su ciclo
de casos de prueba hasta agotar la ventana de tiempo configurada. Al final
agrega los resultados de todas las iteraciones y genera extended_report.json.

Uso desde run_extended_parallel.sh:
    python3.11 scripts/run_extended_parallel.py --duration 600

O directamente (asumiendo port-forwards activos):
    python3.11 scripts/run_extended_parallel.py --duration 600 --no-portforward
"""

import argparse
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Rutas — permite importar los módulos de experimento sin modificarlos
# ---------------------------------------------------------------------------
ROOT_DIR   = Path(__file__).parent.parent
EXP_A_DIR  = ROOT_DIR / "experiments" / "experiment_a"
EXP_B_DIR  = ROOT_DIR / "experiments" / "experiment_b"
REPORT_OUT = ROOT_DIR / "scripts" / "extended_report.json"

sys.path.insert(0, str(EXP_A_DIR))
sys.path.insert(0, str(EXP_B_DIR))

import run_experiment_a as exp_a  # noqa: E402
import run_experiment_b as exp_b  # noqa: E402


# ---------------------------------------------------------------------------
# Colores ANSI
# ---------------------------------------------------------------------------
RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[32m"
RED    = "\033[31m"
CYAN   = "\033[36m"
YELLOW = "\033[33m"
DIM    = "\033[2m"


# ---------------------------------------------------------------------------
# Worker ASR-1 (Experimento A)
# ---------------------------------------------------------------------------

def _run_asr1_worker(duration_s: float, results: list, stop_event: threading.Event) -> None:
    """
    Itera los casos de prueba de ASR-1 hasta que stop_event se active.
    Cada iteración ejecuta CP-A1 … CP-A5 en secuencia.
    Los resultados de cada iteración se añaden a la lista 'results'.
    """
    iteration = 0
    deadline  = time.monotonic() + duration_s

    while not stop_event.is_set() and time.monotonic() < deadline:
        iteration += 1
        iter_start = time.monotonic()
        print(f"\n{BOLD}{CYAN}[ASR-1] ── Iteración {iteration} "
              f"({_remaining(deadline):.0f}s restantes){RESET}")

        cases = []
        for fn in [exp_a.cp_a1_happy_path,
                   exp_a.cp_a2_stochastic_load,
                   exp_a.cp_a3_concurrencia,
                   exp_a.cp_a4_divergencia,
                   exp_a.cp_a5_selftest_failover]:
            if stop_event.is_set() or time.monotonic() >= deadline:
                break
            try:
                cases.append(fn())
            except Exception as exc:
                print(f"  {RED}[ASR-1] ERROR en {fn.__name__}: {exc}{RESET}")
                cases.append({"id": fn.__name__, "passed": False, "error": str(exc)})

        passed = sum(1 for c in cases if c.get("passed"))
        elapsed = time.monotonic() - iter_start
        icon = GREEN + "✅" if passed == len(cases) else RED + "❌"
        print(f"\n{icon} [ASR-1] Iteración {iteration}: "
              f"{passed}/{len(cases)} casos  ({elapsed:.1f}s){RESET}")

        results.append({
            "iteration":   iteration,
            "timestamp":   datetime.now().isoformat(),
            "elapsed_s":   round(elapsed, 2),
            "passed":      passed,
            "total":       len(cases),
            "all_passed":  passed == len(cases),
            "cases":       cases,
        })


# ---------------------------------------------------------------------------
# Worker ASR-2 (Experimento B)
# ---------------------------------------------------------------------------

def _run_asr2_worker(duration_s: float, results: list, stop_event: threading.Event) -> None:
    """
    Itera los casos de prueba de ASR-2 hasta que stop_event se active.
    Cada iteración ejecuta CP-B1 … CP-B4 en secuencia.
    """
    iteration = 0
    deadline  = time.monotonic() + duration_s

    while not stop_event.is_set() and time.monotonic() < deadline:
        iteration += 1
        iter_start = time.monotonic()
        print(f"\n{BOLD}{YELLOW}[ASR-2] ── Iteración {iteration} "
              f"({_remaining(deadline):.0f}s restantes){RESET}")

        cases = []
        for fn in [exp_b.cp_b1_happy_path,
                   exp_b.cp_b2_stochastic_detection,
                   exp_b.cp_b3_jwt_no_bypass,
                   exp_b.cp_b4_umbral_correlacion]:
            if stop_event.is_set() or time.monotonic() >= deadline:
                break
            try:
                cases.append(fn())
            except Exception as exc:
                print(f"  {RED}[ASR-2] ERROR en {fn.__name__}: {exc}{RESET}")
                cases.append({"id": fn.__name__, "passed": False, "error": str(exc)})

        passed = sum(1 for c in cases if c.get("passed"))
        elapsed = time.monotonic() - iter_start
        icon = GREEN + "✅" if passed == len(cases) else RED + "❌"
        print(f"\n{icon} [ASR-2] Iteración {iteration}: "
              f"{passed}/{len(cases)} casos  ({elapsed:.1f}s){RESET}")

        results.append({
            "iteration":   iteration,
            "timestamp":   datetime.now().isoformat(),
            "elapsed_s":   round(elapsed, 2),
            "passed":      passed,
            "total":       len(cases),
            "all_passed":  passed == len(cases),
            "cases":       cases,
        })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _aggregate(iterations: list[dict]) -> dict:
    """Calcula métricas agregadas sobre todas las iteraciones de un experimento."""
    if not iterations:
        return {"iterations": 0}

    total_iters    = len(iterations)
    passing_iters  = sum(1 for it in iterations if it["all_passed"])
    all_cases      = [c for it in iterations for c in it["cases"]]
    total_cases    = len(all_cases)
    passing_cases  = sum(1 for c in all_cases if c.get("passed"))
    iter_pass_rate = passing_iters / total_iters if total_iters else 0.0
    case_pass_rate = passing_cases / total_cases if total_cases else 0.0

    # Timing ASR-1: t_self_test_ms por caso individual
    t_vals = [
        c.get("t_self_test_ms") or c.get("simulation", {}).get("t_self_test_max_ms")
        for c in all_cases
        if (c.get("t_self_test_ms") or c.get("simulation", {}).get("t_self_test_max_ms"))
    ]
    timing = {}
    if t_vals:
        timing = {
            "t_min_ms":  round(min(t_vals), 3),
            "t_max_ms":  round(max(t_vals), 3),
            "t_avg_ms":  round(sum(t_vals) / len(t_vals), 3),
        }

    # Detection rate ASR-2 (CP-B2)
    b2_cases = [c for c in all_cases
                if c.get("id") == "CP-B2" and c.get("detection_rate") is not None]
    detection = {}
    if b2_cases:
        rates = [c["detection_rate"] for c in b2_cases]
        detection = {
            "cp_b2_runs":           len(rates),
            "detection_rate_min":   round(min(rates), 4),
            "detection_rate_max":   round(max(rates), 4),
            "detection_rate_avg":   round(sum(rates) / len(rates), 4),
            "detection_rate_100pct": sum(1 for r in rates if r >= 1.0),
        }

    return {
        "iterations":       total_iters,
        "passing_iterations": passing_iters,
        "iter_pass_rate":   round(iter_pass_rate, 4),
        "total_cases_run":  total_cases,
        "passing_cases":    passing_cases,
        "case_pass_rate":   round(case_pass_rate, 4),
        **timing,
        **detection,
    }


def _print_summary(duration_s: float,
                   results_a: list, results_b: list,
                   wall_elapsed: float) -> dict:
    W = 66
    print("\n" + "=" * W)
    print(f"{BOLD} CCP — Reporte Extendido Paralelo{RESET}")
    print(f" Duración configurada : {duration_s:.0f}s  "
          f"| Tiempo real: {wall_elapsed:.1f}s")
    print(f" Inicio : {datetime.now().isoformat()}")
    print("=" * W)

    agg_a = _aggregate(results_a)
    agg_b = _aggregate(results_b)

    # ASR-1
    print(f"\n{BOLD}{CYAN} ▸ ASR-1  Disponibilidad{RESET}")
    print(f"  Iteraciones totales  : {agg_a.get('iterations', 0)}")
    print(f"  Iteraciones pasadas  : {agg_a.get('passing_iterations', 0)}  "
          f"({agg_a.get('iter_pass_rate', 0)*100:.1f}%)")
    print(f"  Casos individuales   : {agg_a.get('passing_cases', 0)}"
          f"/{agg_a.get('total_cases_run', 0)}  "
          f"({agg_a.get('case_pass_rate', 0)*100:.1f}%)")
    if "t_max_ms" in agg_a:
        threshold_ok = agg_a["t_max_ms"] < 300
        t_icon = GREEN + "✅" if threshold_ok else RED + "❌"
        print(f"  t_self_test          : "
              f"min={agg_a['t_min_ms']}ms  "
              f"avg={agg_a['t_avg_ms']}ms  "
              f"max={agg_a['t_max_ms']}ms  "
              f"{t_icon} {'< 300ms' if threshold_ok else '≥ 300ms'}{RESET}")
    h1_ok = (agg_a.get("iter_pass_rate", 0) >= 1.0
             and agg_a.get("t_max_ms", 9999) < 300)
    print(f"  Hipótesis H1         : "
          f"{GREEN + '✅ CONFIRMADA' if h1_ok else RED + '❌ NO CONFIRMADA'}{RESET}")

    # ASR-2
    print(f"\n{BOLD}{YELLOW} ▸ ASR-2  Seguridad{RESET}")
    print(f"  Iteraciones totales  : {agg_b.get('iterations', 0)}")
    print(f"  Iteraciones pasadas  : {agg_b.get('passing_iterations', 0)}  "
          f"({agg_b.get('iter_pass_rate', 0)*100:.1f}%)")
    print(f"  Casos individuales   : {agg_b.get('passing_cases', 0)}"
          f"/{agg_b.get('total_cases_run', 0)}  "
          f"({agg_b.get('case_pass_rate', 0)*100:.1f}%)")
    if "detection_rate_avg" in agg_b:
        dr_avg = agg_b["detection_rate_avg"]
        dr_icon = GREEN + "✅" if dr_avg >= 1.0 else RED + "❌"
        print(f"  CP-B2 detection_rate : "
              f"min={agg_b['detection_rate_min']*100:.1f}%  "
              f"avg={dr_avg*100:.1f}%  "
              f"max={agg_b['detection_rate_max']*100:.1f}%  "
              f"{dr_icon}{RESET}")
        print(f"  CP-B2 100% runs      : "
              f"{agg_b['detection_rate_100pct']}/{agg_b['cp_b2_runs']}")
    h2_ok = (agg_b.get("iter_pass_rate", 0) >= 1.0)
    print(f"  Hipótesis H2         : "
          f"{GREEN + '✅ CONFIRMADA' if h2_ok else RED + '❌ NO CONFIRMADA'}{RESET}")

    # Global
    all_ok = h1_ok and h2_ok
    print(f"\n{'=' * W}")
    print(f"{BOLD} Resultado global: "
          f"{GREEN + '✅ AMBAS HIPÓTESIS CONFIRMADAS' if all_ok else RED + '❌ ALGUNA HIPÓTESIS NO CONFIRMADA'}"
          f"{RESET}")
    print("=" * W)

    return {
        "meta": {
            "timestamp":          datetime.now().isoformat(),
            "duration_configured_s": duration_s,
            "wall_elapsed_s":     round(wall_elapsed, 2),
        },
        "asr1": {
            "hypothesis": "H1",
            "confirmed":  h1_ok,
            "aggregate":  agg_a,
            "iterations": results_a,
        },
        "asr2": {
            "hypothesis": "H2",
            "confirmed":  h2_ok,
            "aggregate":  agg_b,
            "iterations": results_b,
        },
        "overall": {
            "h1_confirmed": h1_ok,
            "h2_confirmed": h2_ok,
            "all_confirmed": all_ok,
        },
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ejecuta ASR-1 y ASR-2 en paralelo durante N segundos."
    )
    parser.add_argument(
        "--duration", type=float, default=600.0,
        help="Duración total en segundos (default: 600 = 10 min)"
    )
    args = parser.parse_args()
    duration_s = args.duration

    print("=" * 66)
    print(f"{BOLD} CCP — Experimentos paralelos extendidos{RESET}")
    print(f" Inicio   : {datetime.now().isoformat()}")
    print(f" Duración : {duration_s:.0f}s (~{duration_s/60:.1f} min)")
    print(f" ASR-1 y ASR-2 corren simultáneamente en hilos independientes")
    print("=" * 66)

    results_a: list = []
    results_b: list = []
    stop_event = threading.Event()

    t_a = threading.Thread(
        target=_run_asr1_worker,
        args=(duration_s, results_a, stop_event),
        name="ASR-1",
        daemon=True,
    )
    t_b = threading.Thread(
        target=_run_asr2_worker,
        args=(duration_s, results_b, stop_event),
        name="ASR-2",
        daemon=True,
    )

    wall_start = time.monotonic()

    t_a.start()
    t_b.start()

    try:
        # Esperar a que ambos hilos terminen naturalmente (deadline interno)
        t_a.join(timeout=duration_s + 120)
        t_b.join(timeout=duration_s + 120)
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Interrupción recibida — cerrando hilos...{RESET}")
        stop_event.set()
        t_a.join(timeout=30)
        t_b.join(timeout=30)

    wall_elapsed = time.monotonic() - wall_start

    # Generar y guardar reporte
    report = _print_summary(duration_s, results_a, results_b, wall_elapsed)

    REPORT_OUT.parent.mkdir(parents=True, exist_ok=True)
    REPORT_OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\n Reporte guardado en: {REPORT_OUT}")

    sys.exit(0 if report["overall"]["all_confirmed"] else 1)


if __name__ == "__main__":
    main()
