import asyncio
import json
import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any

import httpx
import nats
from fastapi import FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from nats.js import JetStreamContext

import config
from models import HeartBeatPayload, Inconsistencia

# ---------------------------------------------------------------------------
# Structured JSON logging
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(config.SERVICE_NAME)


def log_event(event: str, **kwargs: Any) -> None:
    record = {"service": config.SERVICE_NAME, "nodo": config.NODE_ID, "event": event, **kwargs}
    logger.info(json.dumps(record, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

nc: nats.NATS | None = None
js: JetStreamContext | None = None
http_client: httpx.AsyncClient | None = None
mongo_client: AsyncIOMotorClient | None = None
db = None  # motor database handle

# Operation counters
stats: dict[str, int] = defaultdict(int)


# ---------------------------------------------------------------------------
# NATS stream helpers
# ---------------------------------------------------------------------------

async def _ensure_stream(name: str, subjects: list[str]) -> None:
    try:
        await js.add_stream(name=name, subjects=subjects)
    except Exception as exc:
        log_event("stream_ensure_warning", stream=name, warning=str(exc))


async def _publish(stream_subject: str, payload: dict) -> None:
    try:
        await js.publish(stream_subject, json.dumps(payload).encode())
    except Exception as exc:
        log_event("nats_publish_error", subject=stream_subject, error=str(exc))


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global nc, js, http_client, mongo_client, db

    log_event(
        "startup",
        nats_url=config.NATS_URL,
        mongodb_url=config.MONGODB_URL,
    )

    # HTTP client
    http_client = httpx.AsyncClient()

    # MongoDB
    mongo_client = AsyncIOMotorClient(config.MONGODB_URL)
    db = mongo_client.get_default_database()
    log_event("mongodb_connected")

    # NATS with retry
    for attempt in range(1, 6):
        try:
            nc = await nats.connect(config.NATS_URL)
            break
        except Exception as exc:
            log_event("nats_connect_retry", attempt=attempt, error=str(exc))
            await asyncio.sleep(2 ** attempt)
    else:
        raise RuntimeError(f"Cannot connect to NATS at {config.NATS_URL}")

    js = nc.jetstream()

    # Ensure output streams
    await _ensure_stream("CORRECCION", ["correccion.>"])
    await _ensure_stream("FAILOVER", ["failover.>"])

    log_event("nats_connected", streams=["CORRECCION", "FAILOVER"])

    yield

    log_event("shutdown")
    await http_client.aclose()
    mongo_client.close()
    await nc.drain()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="CCP Corrector", lifespan=lifespan)


# ---------------------------------------------------------------------------
# POST /corregir  — rollback stock negativo
# ---------------------------------------------------------------------------

@app.post("/corregir")
async def corregir(payload: HeartBeatPayload) -> dict:
    t_start = time.monotonic()

    acciones_tomadas: list[dict] = []

    for inc in payload.inconsistencias:
        stock_real = inc.stock_real if inc.stock_real is not None else 0
        if stock_real < 0:
            sku = inc.effective_sku
            try:
                result = await db.inventario.update_one(
                    {"SKU": sku},
                    {"$set": {"stock": 0, "reservas_activas": []}},
                    upsert=False,
                )
                acciones_tomadas.append(
                    {
                        "sku": sku,
                        "stock_anterior": stock_real,
                        "stock_nuevo": 0,
                        "reservas_limpiadas": True,
                        "matched": result.matched_count,
                        "modified": result.modified_count,
                    }
                )
            except Exception as exc:
                log_event("corregir_db_error", sku=sku, error=str(exc))
                raise HTTPException(status_code=500, detail=f"DB error for sku {sku}: {exc}")

    t_correccion_ms = round((time.monotonic() - t_start) * 1000, 3)
    stats["correccion"] += 1

    result_payload = {
        "ok": True,
        "acciones_tomadas": acciones_tomadas,
        "t_correccion_ms": t_correccion_ms,
        "nodo_origen": payload.nodo,
        "timestamp_ms": payload.timestamp_ms,
    }

    await _publish(
        "correccion.stock",
        {**result_payload, "tipo_origen": payload.tipo},
    )

    log_event(
        "correccion_completada",
        acciones=len(acciones_tomadas),
        t_correccion_ms=t_correccion_ms,
        nodo=payload.nodo,
    )

    return result_payload


# ---------------------------------------------------------------------------
# POST /reconciliar  — fix reservas divergence
# ---------------------------------------------------------------------------

@app.post("/reconciliar")
async def reconciliar(payload: HeartBeatPayload) -> dict:
    t_start = time.monotonic()

    acciones_tomadas: list[dict] = []

    for inc in payload.inconsistencias:
        sku = inc.effective_sku
        stock_inicial = inc.stock_inicial if inc.stock_inicial is not None else 0
        reservas = inc.reservas_activas or []
        stock_correcto = stock_inicial - sum(reservas)

        try:
            result = await db.inventario.update_one(
                {"SKU": sku},
                {"$set": {"stock": stock_correcto}},
                upsert=False,
            )
            acciones_tomadas.append(
                {
                    "sku": sku,
                    "stock_inicial": stock_inicial,
                    "reservas_activas": reservas,
                    "stock_recalculado": stock_correcto,
                    "matched": result.matched_count,
                    "modified": result.modified_count,
                }
            )
        except Exception as exc:
            log_event("reconciliar_db_error", sku=sku, error=str(exc))
            raise HTTPException(status_code=500, detail=f"DB error for sku {sku}: {exc}")

    t_reconciliacion_ms = round((time.monotonic() - t_start) * 1000, 3)
    stats["reconciliacion"] += 1

    result_payload = {
        "ok": True,
        "acciones_tomadas": acciones_tomadas,
        "t_reconciliacion_ms": t_reconciliacion_ms,
        "nodo_origen": payload.nodo,
        "timestamp_ms": payload.timestamp_ms,
    }

    await _publish(
        "correccion.reservas",
        {**result_payload, "tipo_origen": payload.tipo},
    )

    log_event(
        "reconciliacion_completada",
        acciones=len(acciones_tomadas),
        t_reconciliacion_ms=t_reconciliacion_ms,
        nodo=payload.nodo,
    )

    return result_payload


# ---------------------------------------------------------------------------
# POST /failover  — activate INV-Standby
# ---------------------------------------------------------------------------

@app.post("/failover")
async def failover(payload: HeartBeatPayload) -> dict:
    t_start = time.monotonic()

    # Wake up standby
    standby_ok = False
    standby_error: str | None = None
    try:
        resp = await http_client.post(
            f"{config.INV_STANDBY_URL}/activar",
            json={"nodo_fallido": payload.nodo, "timestamp_ms": payload.timestamp_ms},
            timeout=10.0,
        )
        resp.raise_for_status()
        standby_ok = True
    except Exception as exc:
        standby_error = str(exc)
        log_event("failover_standby_error", error=standby_error, nodo=payload.nodo)
        # Do not abort — still publish NATS event and return info

    t_failover_ms = round((time.monotonic() - t_start) * 1000, 3)
    stats["failover"] += 1

    nats_payload = {
        "timestamp_ms": payload.timestamp_ms,
        "nodo_fallido": payload.nodo,
        "motivo": payload.tipo,
        "t_failover_ms": t_failover_ms,
        "standby_activado": standby_ok,
    }

    await _publish("failover.activado", nats_payload)

    log_event(
        "failover_activado",
        t_failover_ms=t_failover_ms,
        nodo_fallido=payload.nodo,
        standby_ok=standby_ok,
        standby_error=standby_error,
    )

    result = {
        "ok": True,
        "t_failover_ms": t_failover_ms,
        "standby_url": config.INV_STANDBY_URL,
        "standby_activado": standby_ok,
    }
    if standby_error:
        result["standby_error"] = standby_error

    return result


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": config.SERVICE_NAME, "nodo": config.NODE_ID}


# ---------------------------------------------------------------------------
# GET /stats
# ---------------------------------------------------------------------------

@app.get("/stats")
async def get_stats() -> dict:
    return {
        "service": config.SERVICE_NAME,
        "nodo": config.NODE_ID,
        "correcciones": dict(stats),
        "total": sum(stats.values()),
    }
