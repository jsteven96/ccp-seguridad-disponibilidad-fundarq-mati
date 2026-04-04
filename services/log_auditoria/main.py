"""LogAuditoria — independent audit log for forensic persistence (MongoDB)."""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import motor.motor_asyncio
from fastapi import FastAPI, Query
from pydantic import BaseModel, Field

from config import MONGODB_URL, SERVICE_NAME

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
app = FastAPI(title="LogAuditoria", version="1.0.0")

# MongoDB client and collection are initialised at startup
_mongo_client: motor.motor_asyncio.AsyncIOMotorClient | None = None
_collection: motor.motor_asyncio.AsyncIOMotorCollection | None = None


@app.on_event("startup")
async def startup() -> None:
    global _mongo_client, _collection
    _mongo_client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URL)
    # The database name is embedded in MONGODB_URL; fall back to "ccp"
    db_name = MONGODB_URL.rsplit("/", 1)[-1] or "ccp"
    db = _mongo_client[db_name]
    _collection = db["auditoria"]
    # Descending index on received_at to support efficient sorted queries
    await _collection.create_index([("received_at", -1)])
    log_json(event="startup", mongodb_url=MONGODB_URL)


@app.on_event("shutdown")
async def shutdown() -> None:
    if _mongo_client:
        _mongo_client.close()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class RegistrarRequest(BaseModel):
    evento: str
    actor_id: str
    detalles: dict = Field(default_factory=dict)
    timestamp_ms: float | None = Field(default=None)


class RegistrarResponse(BaseModel):
    ok: bool
    id: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.post("/registrar", response_model=RegistrarResponse)
async def registrar(req: RegistrarRequest) -> RegistrarResponse:
    doc = {
        "evento": req.evento,
        "actor_id": req.actor_id,
        "detalles": req.detalles,
        "timestamp_ms": req.timestamp_ms if req.timestamp_ms is not None else time.time() * 1000,
        "received_at": _now_utc(),
    }

    result = await _collection.insert_one(doc)
    inserted_id = str(result.inserted_id)

    log_json(
        event="auditoria_registrada",
        evento=req.evento,
        actor_id=req.actor_id,
        id=inserted_id,
    )

    return RegistrarResponse(ok=True, id=inserted_id)


@app.get("/eventos")
async def eventos(
    actor_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict]:
    query: dict = {}
    if actor_id is not None:
        query["actor_id"] = actor_id

    cursor = _collection.find(query, {"_id": 0}).sort("received_at", -1).limit(limit)
    docs = await cursor.to_list(length=limit)

    # Serialize datetime objects to ISO strings for JSON compatibility
    for doc in docs:
        if isinstance(doc.get("received_at"), datetime):
            doc["received_at"] = doc["received_at"].isoformat()

    return docs


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
