from pydantic import BaseModel
from typing import Optional


class Reserva(BaseModel):
    id: str
    cantidad: int
    actor_id: str
    activa: bool
    timestamp: float


class ReservaRequest(BaseModel):
    SKU: str
    cantidad: int
    actor_id: str


class ReservaResponse(BaseModel):
    ok: bool
    reserva_id: Optional[str] = None
    stock_restante: Optional[int] = None
    mensaje: str


class FaultInjectRequest(BaseModel):
    tipo: str  # "self_test_failed" | "stock_negativo" | "divergencia_reservas" | "estado_concurrente"


class HeartBeatPayload(BaseModel):
    tipo: str
    timestamp_ms: int
    nodo: str
    inconsistencias: list
    self_test: dict
