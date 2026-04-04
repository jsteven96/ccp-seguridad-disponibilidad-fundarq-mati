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

    Maintains a deque of request events within the last WINDOW_SECONDS seconds
    and evaluates three independent signals on each incoming request:

    1. Rate signal     — number of events in window exceeds RATE_THRESHOLD
    2. SKU concentration — dominant SKU proportion exceeds SKU_CONCENTRATION_THRESHOLD
    3. Cancellation rate — fraction of cancel events exceeds CANCEL_RATE_THRESHOLD

    An attack is declared when the count of triggered signals reaches
    SIGNAL_THRESHOLD or above.
    """

    def __init__(self) -> None:
        self._window: deque[dict[str, Any]] = deque()
        self.attacks_detected: int = 0
        self.last_signals: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evict_expired(self, now_s: float) -> None:
        """Remove events older than WINDOW_SECONDS from the left of the deque."""
        cutoff = now_s - WINDOW_SECONDS
        while self._window and self._window[0]["timestamp"] < cutoff:
            self._window.popleft()

    def _compute_signals(self) -> dict[str, Any]:
        """Evaluate the three CEP signals against the current window contents."""
        total = len(self._window)
        signals_detail: dict[str, Any] = {
            "rate": False,
            "sku_concentration": False,
            "cancel_rate": False,
        }

        if total == 0:
            return signals_detail

        # Signal 1 — rate
        if total > RATE_THRESHOLD:
            signals_detail["rate"] = True

        # Signal 2 — SKU concentration
        sku_counts: dict[str, int] = {}
        for ev in self._window:
            sku = ev.get("sku") or "__none__"
            sku_counts[sku] = sku_counts.get(sku, 0) + 1
        max_sku_count = max(sku_counts.values())
        if (max_sku_count / total) > SKU_CONCENTRATION_THRESHOLD:
            signals_detail["sku_concentration"] = True

        # Signal 3 — cancellation rate
        cancel_count = sum(
            1 for ev in self._window if ev.get("accion") == "cancelar"
        )
        if (cancel_count / total) > CANCEL_RATE_THRESHOLD:
            signals_detail["cancel_rate"] = True

        return signals_detail

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def window_size(self) -> int:
        return len(self._window)

    def reset(self) -> None:
        """Clear the sliding window (for test isolation)."""
        self._window.clear()
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
        """Add one event to the sliding window and run full CEP analysis.

        Returns a dict with:
            attack_detected   — bool
            signals_triggered — int (count of True signals)
            signals_detail    — dict with individual signal booleans
            t_deteccion_ms    — float, wall-clock processing time in ms
        """
        t_start = time.perf_counter()

        now_s = (timestamp_ms / 1000.0) if timestamp_ms is not None else time.time()

        # Evict expired events first
        self._evict_expired(now_s)

        # Append the new event
        self._window.append(
            {
                "timestamp": now_s,
                "actor_id": actor_id,
                "sku": sku,
                "accion": accion,
                "jwt_valido": jwt_valido,
            }
        )

        # Evaluate signals
        signals_detail = self._compute_signals()
        signals_triggered = sum(1 for v in signals_detail.values() if v)
        attack_detected = signals_triggered >= SIGNAL_THRESHOLD

        if attack_detected:
            self.attacks_detected += 1

        self.last_signals = signals_detail

        t_deteccion_ms = (time.perf_counter() - t_start) * 1000.0

        return {
            "attack_detected": attack_detected,
            "signals_triggered": signals_triggered,
            "signals_detail": signals_detail,
            "t_deteccion_ms": round(t_deteccion_ms, 3),
        }
