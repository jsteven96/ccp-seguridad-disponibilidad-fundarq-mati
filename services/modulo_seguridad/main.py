"""ModuloSeguridad — JWT revocation, IP blocking, and audit alerts."""

import json
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from config import LOG_AUDITORIA_URL, BLOCK_DURATION_HOURS, SERVICE_NAME

# ---------------------------------------------------------------------------
# Logging — structured JSON to stdout
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(SERVICE_NAME)


def log_json(**kwargs: Any) -> None:
    logger.info(json.dumps({"service": SERVICE_NAME, **kwargs}))


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="ModuloSeguridad", version="1.0.0")

# In-memory stores
_revoked_tokens: set[str] = set()
_blocked_actors: dict[str, datetime] = {}  # actor_id -> unblock_time (UTC)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class BloquearRequest(BaseModel):
    actor_id: str
    signals: dict
    timestamp_ms: float | None = Field(default=None)


class BloquearResponse(BaseModel):
    ok: bool
    actor_bloqueado: str
    duracion_horas: int
    t_respuesta_ms: float


class VerificarResponse(BaseModel):
    bloqueado: bool
    unblock_time: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


async def _notify_log_auditoria(
    evento: str, actor_id: str, detalles: dict
) -> None:
    """POST event to LogAuditoria; errors are logged but do not block the caller."""
    payload = {
        "evento": evento,
        "actor_id": actor_id,
        "detalles": detalles,
        "timestamp_ms": time.time() * 1000,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{LOG_AUDITORIA_URL}/registrar", json=payload
            )
            resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        log_json(event="log_auditoria_notify_error", error=str(exc))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.post("/bloquear", response_model=BloquearResponse)
async def bloquear(req: BloquearRequest) -> BloquearResponse:
    t_start = time.perf_counter()

    # Revoke JWT — represented as token keyed by actor_id
    _revoked_tokens.add(req.actor_id)

    # Block actor for BLOCK_DURATION_HOURS hours
    unblock_time = _now_utc() + timedelta(hours=BLOCK_DURATION_HOURS)
    _blocked_actors[req.actor_id] = unblock_time

    t_respuesta_ms = round((time.perf_counter() - t_start) * 1000, 3)

    log_json(
        event="actor_bloqueado",
        actor_id=req.actor_id,
        signals=req.signals,
        t_respuesta_ms=t_respuesta_ms,
    )

    # Notify audit log (non-blocking on failure)
    await _notify_log_auditoria(
        evento="actor_bloqueado",
        actor_id=req.actor_id,
        detalles={
            "signals": req.signals,
            "unblock_time": unblock_time.isoformat(),
            "duracion_horas": BLOCK_DURATION_HOURS,
        },
    )

    return BloquearResponse(
        ok=True,
        actor_bloqueado=req.actor_id,
        duracion_horas=BLOCK_DURATION_HOURS,
        t_respuesta_ms=t_respuesta_ms,
    )


@app.get("/verificar/{actor_id}", response_model=VerificarResponse)
async def verificar(actor_id: str) -> VerificarResponse:
    unblock_time = _blocked_actors.get(actor_id)

    if unblock_time is None:
        return VerificarResponse(bloqueado=False)

    # Auto-expire: if the block window has passed, treat as unblocked
    if _now_utc() >= unblock_time:
        del _blocked_actors[actor_id]
        return VerificarResponse(bloqueado=False)

    return VerificarResponse(
        bloqueado=True,
        unblock_time=unblock_time.isoformat(),
    )


@app.post("/desbloquear/{actor_id}")
async def desbloquear(actor_id: str) -> dict:
    """Remove actor from blocked list (intended for testing only)."""
    removed = actor_id in _blocked_actors
    _blocked_actors.pop(actor_id, None)
    _revoked_tokens.discard(actor_id)
    return {"ok": True, "actor_id": actor_id, "was_blocked": removed}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/stats")
async def stats() -> dict:
    return {
        "total_bloqueados": len(_blocked_actors),
        "total_revocados": len(_revoked_tokens),
    }
