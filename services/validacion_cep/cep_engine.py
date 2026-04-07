import time
from collections import deque
from typing import Any

from config import (
    WINDOW_SECONDS,
    SIGNAL_THRESHOLD,
    RATE_THRESHOLD,
    SKU_CONCENTRATION_THRESHOLD,
    CANCEL_RATE_THRESHOLD,
)


class CEPEngine:
    """Sliding window CEP engine for DDoS detection.

    Evaluates signals at two independent levels:

    1. Per-actor — detects concentrated single-actor attacks even when legitimate
       traffic is present. Each actor maintains its own sliding window; signals
       are computed only against that actor's events.

    2. Global — detects distributed volumetric attacks where many actors each
       contribute a small fraction of requests. Signals are computed against the
       aggregated window of all actors.

    An attack is declared for the requesting actor when EITHER:
      - The actor triggers >= SIGNAL_THRESHOLD per-actor signals, OR
      - The global window triggers >= SIGNAL_THRESHOLD global signals.

    Per-actor windows are evicted lazily once all their events expire, keeping
    memory proportional to active actors within the last WINDOW_SECONDS.
    """

    def __init__(self) -> None:
        self._window: deque[dict[str, Any]] = deque()
        self._actor_windows: dict[str, deque[dict[str, Any]]] = {}
        self.attacks_detected: int = 0
        self.last_signals: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evict_expired(self, now_s: float) -> None:
        """Remove expired events from the global window and all actor windows."""
        cutoff = now_s - WINDOW_SECONDS

        while self._window and self._window[0]["timestamp"] < cutoff:
            self._window.popleft()

        for actor_id in list(self._actor_windows):
            aw = self._actor_windows[actor_id]
            while aw and aw[0]["timestamp"] < cutoff:
                aw.popleft()
            if not aw:
                del self._actor_windows[actor_id]

    def _compute_signals_for_window(
        self, window: deque[dict[str, Any]]
    ) -> dict[str, Any]:
        """Evaluate the three CEP signals against an arbitrary event window.

        Shared logic used by both per-actor and global analysis.
        """
        total = len(window)
        signals: dict[str, Any] = {
            "rate": False,
            "sku_concentration": False,
            "cancel_rate": False,
        }

        if total == 0:
            return signals

        # Signal 1 — rate
        if total > RATE_THRESHOLD:
            signals["rate"] = True

        # Signal 2 — SKU concentration
        sku_counts: dict[str, int] = {}
        for ev in window:
            sku = ev.get("sku") or "__none__"
            sku_counts[sku] = sku_counts.get(sku, 0) + 1
        max_sku_count = max(sku_counts.values())
        if (max_sku_count / total) > SKU_CONCENTRATION_THRESHOLD:
            signals["sku_concentration"] = True

        # Signal 3 — cancellation rate
        cancel_count = sum(1 for ev in window if ev.get("accion") == "cancelar")
        if (cancel_count / total) > CANCEL_RATE_THRESHOLD:
            signals["cancel_rate"] = True

        return signals

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def window_size(self) -> int:
        return len(self._window)

    def reset(self) -> None:
        """Clear all sliding windows (for test isolation)."""
        self._window.clear()
        self._actor_windows.clear()
        self.attacks_detected = 0
        self.last_signals = {}

    def add_event_and_analyze(
        self,
        actor_id: str,
        sku: str,
        accion: str,
        jwt_valido: bool,
        timestamp_ms: float | None = None,
    ) -> dict[str, Any]:
        """Add one event and run dual-level CEP analysis.

        Returns a dict with:
            attack_detected   — bool
            signals_triggered — int (max of per-actor and global triggered counts)
            signals_detail    — dict with per-actor signal booleans (API compat)
            actor_signals     — dict with per-actor signal booleans
            global_signals    — dict with global signal booleans
            t_deteccion_ms    — float, wall-clock processing time in ms
        """
        t_start = time.perf_counter()

        now_s = (timestamp_ms / 1000.0) if timestamp_ms is not None else time.time()
        event = {
            "timestamp": now_s,
            "actor_id": actor_id,
            "sku": sku,
            "accion": accion,
            "jwt_valido": jwt_valido,
        }

        self._evict_expired(now_s)

        # Append to global window
        self._window.append(event)

        # Append to per-actor window
        if actor_id not in self._actor_windows:
            self._actor_windows[actor_id] = deque()
        self._actor_windows[actor_id].append(event)

        # --- Per-actor analysis (catches concentrated attacks under mixed traffic) ---
        actor_signals = self._compute_signals_for_window(self._actor_windows[actor_id])
        actor_triggered = sum(1 for v in actor_signals.values() if v)
        actor_attack = actor_triggered >= SIGNAL_THRESHOLD

        # --- Global analysis (catches distributed volumetric attacks) ---
        global_signals = self._compute_signals_for_window(self._window)
        global_triggered = sum(1 for v in global_signals.values() if v)
        global_attack = global_triggered >= SIGNAL_THRESHOLD

        attack_detected = actor_attack or global_attack

        if attack_detected:
            self.attacks_detected += 1

        self.last_signals = {
            "actor": actor_signals,
            "global": global_signals,
        }

        t_deteccion_ms = (time.perf_counter() - t_start) * 1000.0

        return {
            "attack_detected": attack_detected,
            "signals_triggered": max(actor_triggered, global_triggered),
            "signals_detail": actor_signals,   # kept for API backward compatibility
            "actor_signals": actor_signals,
            "global_signals": global_signals,
            "t_deteccion_ms": round(t_deteccion_ms, 3),
        }
