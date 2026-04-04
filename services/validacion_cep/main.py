"""ValidacionCEP — CEP engine with sliding window for DDoS detection."""

import json
import logging
import time
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from cep_engine import CEPEngine
from config import MODULO_SEGURIDAD_URL, SERVICE_NAME

# ---------------------------------------------------------------------------
# Logging — structured JSON to stdout
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)
logger = logging.getLogger(SERVICE_NAME)


def log_json(**kwargs: Any) -> None:
    logger.info(json.dumps({"service": SERVICE_NAME, **kwargs}))


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="ValidacionCEP", version="1.0.0")

_engine = CEPEngine()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class ValidarRequest(BaseModel):
    actor_id: str
    sku: str
    accion: str
    jwt_valido: bool
    timestamp_ms: float | None = Field(default=None)


class ValidarResponse(BaseModel):
    ok: bool
    mensaje: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _notify_modulo_seguridad(actor_id: str, signals: dict) -> None:
    """Fire-and-forget POST to ModuloSeguridad /bloquear."""
    payload = {
        "actor_id": actor_id,
        "signals": signals,
        "timestamp_ms": time.time() * 1000,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{MODULO_SEGURIDAD_URL}/bloquear", json=payload
            )
            resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        log_json(event="modulo_seguridad_notify_error", error=str(exc))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.post("/validar")
async def validar(req: ValidarRequest):
    result = _engine.add_event_and_analyze(
        actor_id=req.actor_id,
        sku=req.sku,
        accion=req.accion,
        jwt_valido=req.jwt_valido,
        timestamp_ms=req.timestamp_ms,
    )

    log_json(
        event="validacion_cep",
        actor_id=req.actor_id,
        attack_detected=result["attack_detected"],
        signals_triggered=result["signals_triggered"],
        signals_detail=result["signals_detail"],
        t_deteccion_ms=result["t_deteccion_ms"],
    )

    if result["attack_detected"]:
        await _notify_modulo_seguridad(
            actor_id=req.actor_id,
            signals=result["signals_detail"],
        )
        # Return masked 429 — never expose internal criteria
        return JSONResponse(
            status_code=429,
            content={"ok": False, "mensaje": "Servicio temporalmente no disponible"},
        )

    return JSONResponse(status_code=200, content={"ok": True, "mensaje": "Validacion exitosa"})


@app.post("/reset")
async def reset_engine() -> dict:
    """Reset the sliding window (for test isolation between experiment cases)."""
    _engine.reset()
    log_json(event="cep_engine_reset")
    return {"ok": True, "mensaje": "Ventana CEP limpiada"}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/stats")
async def stats() -> dict:
    return {
        "window_size": _engine.window_size,
        "attacks_detected": _engine.attacks_detected,
        "last_signals": _engine.last_signals,
    }
