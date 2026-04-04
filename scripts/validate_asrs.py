#!/usr/bin/env python3
"""
validate_asrs.py — Master validation script for CCP ASR experiments.

Runs experiment_a and experiment_b sequentially, reads their JSON result files,
and prints a final consolidated report.
"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent

EXP_A_SCRIPT = REPO_ROOT / "experiments" / "experiment_a" / "run_experiment_a.py"
EXP_B_SCRIPT = REPO_ROOT / "experiments" / "experiment_b" / "run_experiment_b.py"

RESULTS_A = REPO_ROOT / "experiments" / "experiment_a" / "results_a.json"
RESULTS_B = REPO_ROOT / "experiments" / "experiment_b" / "results_b.json"

FINAL_REPORT_PATH = REPO_ROOT / "scripts" / "final_report.json"

THRESHOLD_MS = 300


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_experiment(script: Path) -> bool:
    """Run a Python experiment script as a subprocess. Returns True on success."""
    print(f"\n>>> Ejecutando: {script.name}")
    print("-" * 60)
    result = subprocess.run([sys.executable, str(script)], text=True)
    return result.returncode == 0


def load_json(path: Path) -> dict | None:
    if not path.exists():
        print(f"  [WARNING] Archivo no encontrado: {path}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"  [ERROR] JSON inválido en {path}: {exc}")
        return None


def fmt_ms(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.1f}ms"


def status_icon(passed: bool) -> str:
    return "✅" if passed else "❌"


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def render_case_a(case: dict) -> str:
    """Format a single ASR-1 test case line."""
    cid = case.get("id", "?")
    name = case.get("name", "")
    passed = case.get("passed", False)
    icon = status_icon(passed)

    t_self = fmt_ms(case.get("t_self_test_ms"))
    t_clas = fmt_ms(case.get("t_clasificacion_ms"))
    t_total = fmt_ms(case.get("t_total_ms"))
    t_fail = fmt_ms(case.get("t_failover_ms"))

    label = f"{cid} {name}"

    if cid == "CP-A5":
        timing = f"t_failover={t_fail}"
    else:
        timing = f"t_self_test={t_self}  t_clasificacion={t_clas}  total={t_total}"

    return f"  {icon}  {label:<36} {timing}"


def render_case_b(case: dict) -> str:
    """Format a single ASR-2 test case line."""
    cid = case.get("id", "?")
    name = case.get("name", "")
    passed = case.get("passed", False)
    icon = status_icon(passed)

    label = f"{cid} {name}"

    if cid == "CP-B1":
        fp = case.get("false_positives", 0)
        timing = f"sin falsos positivos ({fp})"
    else:
        # Nested structure for CP-B4
        above = case.get("above_threshold", {})
        t_det_raw = (
            case.get("t_deteccion_ms")
            or above.get("t_deteccion_ms")
        )
        timing = f"t_deteccion={fmt_ms(t_det_raw)}"

    return f"  {icon}  {label:<36} {timing}"


def print_report(data_a: dict | None, data_b: dict | None) -> dict:
    """Print the consolidated report and return the final_report dict."""
    print()
    print("=" * 52)
    print(" CCP Experiment — Validación de ASRs")
    print("=" * 52)

    # --- ASR-1 ---
    print()
    print("--- ASR-1: Disponibilidad (HeartBeat + VALCOH) ---")
    cases_a = []
    h1_confirmed = False

    if data_a:
        cases_a = data_a.get("cases", [])
        for case in cases_a:
            print(render_case_a(case))
        h1_confirmed = data_a.get("h1_confirmed", False)
        passed_a = data_a.get("passed", 0)
        total_a = data_a.get("total", 0)
    else:
        print("  [ERROR] No se encontraron resultados del Experimento A")
        passed_a, total_a = 0, 0

    h1_icon = status_icon(h1_confirmed)
    h1_status = "CONFIRMADA" if h1_confirmed else "NO CONFIRMADA"
    print()
    print(f"Hipótesis H1: {h1_icon} {h1_status} — todos los CP-A < {THRESHOLD_MS}ms")

    # --- ASR-2 ---
    print()
    print("--- ASR-2: Seguridad (CEP) ---")
    cases_b = []
    h2_confirmed = False

    if data_b:
        cases_b = data_b.get("cases", [])
        for case in cases_b:
            print(render_case_b(case))
        h2_confirmed = data_b.get("h2_confirmed", False)
        passed_b = data_b.get("passed", 0)
        total_b = data_b.get("total", 0)
    else:
        print("  [ERROR] No se encontraron resultados del Experimento B")
        passed_b, total_b = 0, 0

    h2_icon = status_icon(h2_confirmed)
    h2_status = "CONFIRMADA" if h2_confirmed else "NO CONFIRMADA"
    print()
    print(f"Hipótesis H2: {h2_icon} {h2_status} — todos los CP-B < {THRESHOLD_MS}ms")

    # --- Final summary ---
    total_passed = passed_a + passed_b
    total_cases = total_a + total_b
    all_passed = total_passed == total_cases and total_cases > 0

    print()
    print("=" * 52)
    print(f" Resultado final: {total_passed}/{total_cases} casos exitosos")
    print("=" * 52)

    return {
        "timestamp": datetime.now().isoformat(),
        "threshold_ms": THRESHOLD_MS,
        "asr1": {
            "hypothesis": "H1",
            "confirmed": h1_confirmed,
            "passed": passed_a,
            "total": total_a,
            "cases": cases_a,
        },
        "asr2": {
            "hypothesis": "H2",
            "confirmed": h2_confirmed,
            "passed": passed_b,
            "total": total_b,
            "cases": cases_b,
        },
        "overall": {
            "passed": total_passed,
            "total": total_cases,
            "all_passed": all_passed,
        },
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 52)
    print(" CCP — Validación de ASRs (Master Script)")
    print(f" Inicio: {datetime.now().isoformat()}")
    print("=" * 52)
    print()
    print("Leyendo resultados de los experimentos ya ejecutados...")

    # Load results
    data_a = load_json(RESULTS_A)
    data_b = load_json(RESULTS_B)

    # Print consolidated report
    final = print_report(data_a, data_b)

    # Save final report
    FINAL_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    FINAL_REPORT_PATH.write_text(json.dumps(final, indent=2, ensure_ascii=False))
    print(f"\n Reporte final guardado en: {FINAL_REPORT_PATH}")

    # Exit with non-zero if any experiment failed
    if not final["overall"]["all_passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
