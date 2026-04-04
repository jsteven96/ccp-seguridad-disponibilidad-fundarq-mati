from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Inconsistencia(BaseModel):
    """Represents a single inventory inconsistency reported in a HeartBeat.

    The HeartBeat payload uses uppercase 'SKU' as the field name.
    Both 'SKU' and 'sku' are accepted for compatibility.
    """

    SKU: str | None = None       # uppercase — matches HeartBeat payload
    sku: str | None = None       # lowercase fallback
    stock_real: int | None = None
    stock_esperado: int | None = None
    stock_inicial: int | None = None
    reservas_activas: list[int] = Field(default_factory=list)
    detalle: str | None = None

    # Allow extra fields coming from different HeartBeat variants
    model_config = {"extra": "allow"}

    @property
    def effective_sku(self) -> str:
        """Return the SKU value regardless of casing."""
        return self.SKU or self.sku or ""


class SelfTest(BaseModel):
    latencia_ms: float | None = None
    resultado: str | None = None

    model_config = {"extra": "allow"}


class HeartBeatPayload(BaseModel):
    """
    Canonical HeartBeat message published by Módulo de Inventarios.

    Fields map 1-to-1 with the NATS JetStream payload schema.
    """

    tipo: str
    timestamp_ms: int
    nodo: str
    inconsistencias: list[Inconsistencia] = Field(default_factory=list)
    self_test: SelfTest | None = None

    # Allow unknown top-level keys from future HeartBeat versions
    model_config = {"extra": "allow"}
