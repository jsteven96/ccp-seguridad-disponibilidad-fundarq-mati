import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

import heartbeat as hb
from config import MONGODB_URL, NODE_ID, SERVICE_NAME, STANDBY_MODE
from models import FaultInjectRequest, ReservaRequest, ReservaResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)
logger = logging.getLogger(SERVICE_NAME)

# ---------------------------------------------------------------------------
# Inventario inicial (usado en /reset)
# ---------------------------------------------------------------------------
INITIAL_INVENTORY = [
    {"SKU": "COCA-COLA-350", "stock": 9, "stock_inicial": 9, "reservas_activas": [], "version": 0},
    {"SKU": "AGUA-500", "stock": 100, "stock_inicial": 100, "reservas_activas": [], "version": 0},
    {"SKU": "ARROZ-1KG", "stock": 50, "stock_inicial": 50, "reservas_activas": [], "version": 0},
]

# ---------------------------------------------------------------------------
# Application state
# ---------------------------------------------------------------------------
_db = None
_hb_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db, _hb_task

    # Startup
    client = AsyncIOMotorClient(MONGODB_URL)
    _db = client["ccp"]

    # Seed inventory if collection is empty
    count = await _db.inventario.count_documents({})
    if count == 0:
        await _db.inventario.insert_many(
            [doc.copy() for doc in INITIAL_INVENTORY]
        )
        logger.info(
            json.dumps({"event": "inventario_inicializado", "docs": len(INITIAL_INVENTORY)})
        )

    # Start HeartBeat background task
    _hb_task = asyncio.create_task(hb.heartbeat_loop(_db))
    logger.info(json.dumps({"event": "servicio_iniciado", "nodo": NODE_ID, "standby": STANDBY_MODE}))

    yield

    # Shutdown
    if _hb_task:
        _hb_task.cancel()
        try:
            await _hb_task
        except asyncio.CancelledError:
            pass
    client.close()
    logger.info(json.dumps({"event": "servicio_detenido", "nodo": NODE_ID}))


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "mode": "standby" if STANDBY_MODE else "primary",
        "nodo": NODE_ID,
    }


@app.get("/inventario/{sku}")
async def get_inventario(sku: str):
    doc = await _db.inventario.find_one({"SKU": sku}, {"_id": 0})
    if doc is None:
        raise HTTPException(status_code=404, detail="SKU no encontrado")
    reservas_activas_count = sum(
        1 for r in doc.get("reservas_activas", []) if r.get("activa", True)
    )
    return {
        "SKU": doc["SKU"],
        "stock": doc["stock"],
        "reservas_activas": reservas_activas_count,
    }


@app.post("/reservar", response_model=ReservaResponse)
async def reservar(req: ReservaRequest):
    doc = await _db.inventario.find_one({"SKU": req.SKU})
    if doc is None:
        return ReservaResponse(ok=False, mensaje="SKU no encontrado")

    # Intentamos la reserva incluso si el stock quedaría negativo,
    # para que el VALCOH detecte el negativo en el próximo ciclo.
    nueva_reserva = {
        "id": str(uuid4()),
        "cantidad": req.cantidad,
        "actor_id": req.actor_id,
        "activa": True,
        "timestamp": time.time(),
    }

    result = await _db.inventario.find_one_and_update(
        {"SKU": req.SKU, "version": doc["version"]},  # optimistic lock
        {
            "$inc": {"stock": -req.cantidad, "version": 1},
            "$push": {"reservas_activas": nueva_reserva},
        },
        return_document=True,
    )

    if result is None:
        # Version mismatch → conflicto de concurrencia
        logger.warning(
            json.dumps(
                {
                    "event": "concurrencia_detectada",
                    "SKU": req.SKU,
                    "actor_id": req.actor_id,
                }
            )
        )
        return ReservaResponse(
            ok=False,
            mensaje="Conflicto de concurrencia detectado",
        )

    logger.info(
        json.dumps(
            {
                "event": "reserva_creada",
                "SKU": req.SKU,
                "cantidad": req.cantidad,
                "actor_id": req.actor_id,
                "reserva_id": nueva_reserva["id"],
                "stock_restante": result["stock"],
            }
        )
    )

    return ReservaResponse(
        ok=True,
        reserva_id=nueva_reserva["id"],
        stock_restante=result["stock"],
        mensaje="Reserva creada exitosamente",
    )


@app.post("/reset")
async def reset():
    """Resetea el inventario a valores iniciales (para tests)."""
    await _db.inventario.drop()
    await _db.inventario.insert_many([doc.copy() for doc in INITIAL_INVENTORY])
    logger.info(json.dumps({"event": "inventario_reseteado"}))
    return {"ok": True, "mensaje": "Inventario reseteado a valores iniciales"}


@app.post("/fault-inject")
async def fault_inject(req: FaultInjectRequest):
    """Activa un fault mode que el VALCOH reportará en el próximo HeartBeat."""
    tipos_validos = {
        "self_test_failed",
        "stock_negativo",
        "divergencia_reservas",
        "estado_concurrente",
        "none",
    }
    if req.tipo not in tipos_validos:
        raise HTTPException(
            status_code=400,
            detail=f"Tipo inválido. Opciones: {tipos_validos}",
        )

    if req.tipo == "none":
        hb.set_fault_mode(None)
        logger.info(json.dumps({"event": "fault_inject_desactivado"}))
        return {"ok": True, "fault_mode": None}

    if req.tipo == "stock_negativo":
        # Corromper el stock del primer SKU directamente para que el VALCOH lo detecte
        await _db.inventario.update_one(
            {"SKU": "COCA-COLA-350"},
            {"$set": {"stock": -1}},
        )
        logger.info(json.dumps({"event": "fault_inject_stock_negativo"}))
        return {"ok": True, "fault_mode": req.tipo, "accion": "stock de COCA-COLA-350 seteado a -1"}

    hb.set_fault_mode(req.tipo)
    logger.info(json.dumps({"event": "fault_inject_activado", "tipo": req.tipo}))
    return {"ok": True, "fault_mode": req.tipo}
