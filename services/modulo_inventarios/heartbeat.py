import asyncio
import json
import logging

import nats
from config import HEARTBEAT_INTERVAL_S, NATS_URL, STANDBY_MODE
from valcoh import run_self_test

logger = logging.getLogger(__name__)

_fault_mode: str | None = None  # se puede setear via /fault-inject

TOPIC_MAP = {
    "SELF_TEST_OK": "heartbeat.inventario.ok",
    "STOCK_NEGATIVO": "heartbeat.inventario.stock_negativo",
    "DIVERGENCIA_RESERVAS": "heartbeat.inventario.divergencia_reservas",
    "ESTADO_CONCURRENTE": "heartbeat.inventario.estado_concurrente",
    "SELF_TEST_FAILED": "heartbeat.inventario.self_test_failed",
}


def set_fault_mode(mode: str | None) -> None:
    global _fault_mode
    _fault_mode = mode


def get_fault_mode() -> str | None:
    return _fault_mode


async def heartbeat_loop(db) -> None:
    """Loop que publica HeartBeat cada HEARTBEAT_INTERVAL_S segundos."""
    if STANDBY_MODE:
        logger.info("STANDBY_MODE activo — HeartBeat deshabilitado")
        return

    nc = await nats.connect(NATS_URL)
    js = nc.jetstream()
    logger.info(
        json.dumps(
            {
                "event": "heartbeat_loop_iniciado",
                "intervalo_s": HEARTBEAT_INTERVAL_S,
            }
        )
    )

    while True:
        try:
            payload = await run_self_test(db, fault_mode=_fault_mode)
            topic = TOPIC_MAP.get(payload.tipo, "heartbeat.inventario.ok")
            data = json.dumps(payload.model_dump()).encode()
            await js.publish(topic, data)
            logger.info(
                json.dumps(
                    {
                        "event": "heartbeat_publicado",
                        "tipo": payload.tipo,
                        "topic": topic,
                        "t_self_test_ms": payload.self_test.get("t_self_test_ms"),
                        "inconsistencias": len(payload.inconsistencias),
                    }
                )
            )
        except Exception as e:
            logger.error(
                json.dumps({"event": "heartbeat_error", "error": str(e)})
            )

        await asyncio.sleep(HEARTBEAT_INTERVAL_S)
