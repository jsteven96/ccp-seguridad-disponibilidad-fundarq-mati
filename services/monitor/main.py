import asyncio
import json
import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any

import httpx
import nats
from fastapi import FastAPI
from nats.js import JetStreamContext

import config

# ---------------------------------------------------------------------------
# Structured JSON logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)
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

# Counters per tipo
stats: dict[str, int] = defaultdict(int)


# ---------------------------------------------------------------------------
# NATS subscription handler
# ---------------------------------------------------------------------------

ROUTES: dict[str, str] = {
    "STOCK_NEGATIVO": "/corregir",
    "DIVERGENCIA_RESERVAS": "/reconciliar",
    "ESTADO_CONCURRENTE": "/reconciliar",
    "SELF_TEST_FAILED": "/failover",
}


async def handle_heartbeat(msg: nats.aio.client.Msg) -> None:
    t_received = time.monotonic()

    try:
        payload = json.loads(msg.data.decode())
    except Exception as exc:
        log_event("heartbeat_decode_error", error=str(exc))
        await msg.nak()
        return

    tipo: str = payload.get("tipo", "UNKNOWN")
    stats[tipo] += 1

    if tipo == "SELF_TEST_OK":
        t_clasificacion_ms = round((time.monotonic() - t_received) * 1000, 3)
        log_event(
            "heartbeat_ok",
            tipo=tipo,
            t_clasificacion_ms=t_clasificacion_ms,
            nodo=payload.get("nodo"),
        )
        await msg.ack()
        return

    path = ROUTES.get(tipo)
    if path is None:
        log_event("heartbeat_unknown_tipo", tipo=tipo, nodo=payload.get("nodo"))
        # Ack to avoid re-delivery of unrecognised messages
        await msg.ack()
        return

    # Route to corrector
    corrector_url = f"{config.CORRECTOR_URL}{path}"
    try:
        response = await http_client.post(corrector_url, json=payload, timeout=10.0)
        response.raise_for_status()
    except Exception as exc:
        log_event(
            "corrector_call_error",
            tipo=tipo,
            path=path,
            error=str(exc),
            nodo=payload.get("nodo"),
        )
        # ACK to prevent infinite redelivery on permanent errors (e.g. 422)
        await msg.ack()
        return

    t_clasificacion_ms = round((time.monotonic() - t_received) * 1000, 3)
    log_event(
        "heartbeat_routed",
        tipo=tipo,
        path=path,
        t_clasificacion_ms=t_clasificacion_ms,
        nodo=payload.get("nodo"),
        corrector_status=response.status_code,
    )
    await msg.ack()


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global nc, js, http_client

    log_event("startup", nats_url=config.NATS_URL, corrector_url=config.CORRECTOR_URL)

    # HTTP client (shared, keeps connection pool)
    http_client = httpx.AsyncClient()

    # NATS connection with retry
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

    # Ensure the stream exists (idempotent — server ignores if already present)
    try:
        await js.add_stream(
            name="HEARTBEAT_INVENTARIO",
            subjects=["heartbeat.inventario.>"],
        )
    except Exception as exc:
        # Stream may already exist; log and proceed
        log_event("stream_ensure_warning", warning=str(exc))

    # Durable push-based consumer with queue group for horizontal scaling
    await js.subscribe(
        "heartbeat.inventario.>",
        stream="HEARTBEAT_INVENTARIO",
        durable="monitor-consumer",
        cb=handle_heartbeat,
        manual_ack=True,
    )

    log_event("nats_subscribed", stream="HEARTBEAT_INVENTARIO", consumer="monitor-consumer")

    yield

    # Teardown
    log_event("shutdown")
    await http_client.aclose()
    await nc.drain()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="CCP Monitor", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": config.SERVICE_NAME, "nodo": config.NODE_ID}


@app.get("/stats")
async def get_stats() -> dict:
    return {
        "service": config.SERVICE_NAME,
        "nodo": config.NODE_ID,
        "counts_por_tipo": dict(stats),
        "total": sum(stats.values()),
    }
