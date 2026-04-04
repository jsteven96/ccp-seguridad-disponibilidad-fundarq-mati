import time
from models import HeartBeatPayload
from config import NODE_ID


async def run_self_test(db, fault_mode: str | None = None) -> HeartBeatPayload:
    """
    Ejecuta los 3 checks del VALCOH sobre el estado actual del inventario.
    Retorna un HeartBeatPayload clasificado.

    Si fault_mode está seteado, simula ese tipo de falla.
    """
    t_start = time.monotonic()

    t_self_test_ms = (time.monotonic() - t_start) * 1000

    # Si hay fault injection activo, retornar el tipo simulado directamente
    if fault_mode == "self_test_failed":
        return HeartBeatPayload(
            tipo="SELF_TEST_FAILED",
            timestamp_ms=int(time.time() * 1000),
            nodo=NODE_ID,
            inconsistencias=[{"error": "fallo_estructural_simulado"}],
            self_test={"resultado": "FAILED", "check_fallido": "fault_injection", "t_self_test_ms": t_self_test_ms},
        )

    if fault_mode == "estado_concurrente":
        return HeartBeatPayload(
            tipo="ESTADO_CONCURRENTE",
            timestamp_ms=int(time.time() * 1000),
            nodo=NODE_ID,
            inconsistencias=[{"error": "conflicto_version_simulado", "SKU": "COCA-COLA-350"}],
            self_test={"resultado": "FAILED", "check_fallido": "optimistic_lock", "t_self_test_ms": t_self_test_ms},
        )

    if fault_mode == "divergencia_reservas":
        return HeartBeatPayload(
            tipo="DIVERGENCIA_RESERVAS",
            timestamp_ms=int(time.time() * 1000),
            nodo=NODE_ID,
            inconsistencias=[{"error": "divergencia_simulada", "SKU": "COCA-COLA-350", "suma_reservas": 5, "delta_esperado": 0}],
            self_test={"resultado": "FAILED", "check_fallido": "suma_reservas", "t_self_test_ms": t_self_test_ms},
        )

    inconsistencias = []
    check_fallido = None

    # Leer todos los documentos de inventario (en memoria una vez)
    docs = await db.inventario.find({}).to_list(length=1000)

    # Check 1: stock >= 0
    for doc in docs:
        if doc["stock"] < 0:
            inconsistencias.append(
                {
                    "SKU": doc["SKU"],
                    "stock_real": doc["stock"],
                    "delta": doc["stock"],
                }
            )
            if check_fallido is None:
                check_fallido = "stock_negativo"

    if inconsistencias and check_fallido == "stock_negativo":
        t_self_test_ms = (time.monotonic() - t_start) * 1000
        return HeartBeatPayload(
            tipo="STOCK_NEGATIVO",
            timestamp_ms=int(time.time() * 1000),
            nodo=NODE_ID,
            inconsistencias=inconsistencias,
            self_test={
                "resultado": "FAILED",
                "check_fallido": "stock_negativo",
                "t_self_test_ms": t_self_test_ms,
            },
        )

    # Check 2: suma(reservas_activas) == stock_inicial - stock_actual
    for doc in docs:
        reservas_activas = [
            r for r in doc.get("reservas_activas", []) if r.get("activa", True)
        ]
        suma_reservas = sum(r["cantidad"] for r in reservas_activas)
        delta_esperado = doc["stock_inicial"] - doc["stock"]
        if suma_reservas != delta_esperado:
            inconsistencias.append(
                {
                    "SKU": doc["SKU"],
                    "suma_reservas": suma_reservas,
                    "delta_esperado": delta_esperado,
                    "diferencia": suma_reservas - delta_esperado,
                }
            )
            if check_fallido is None:
                check_fallido = "divergencia_reservas"

    if inconsistencias and check_fallido == "divergencia_reservas":
        t_self_test_ms = (time.monotonic() - t_start) * 1000
        return HeartBeatPayload(
            tipo="DIVERGENCIA_RESERVAS",
            timestamp_ms=int(time.time() * 1000),
            nodo=NODE_ID,
            inconsistencias=inconsistencias,
            self_test={
                "resultado": "FAILED",
                "check_fallido": "suma_reservas",
                "t_self_test_ms": t_self_test_ms,
            },
        )

    # Check 3: reservas huérfanas (reservas activas con timestamp > 10 min sin resolución)
    ahora = time.time()
    for doc in docs:
        huerfanas = [
            r
            for r in doc.get("reservas_activas", [])
            if r.get("activa", True) and (ahora - r.get("timestamp", ahora)) > 600
        ]
        if huerfanas:
            inconsistencias.append(
                {"SKU": doc["SKU"], "reservas_huerfanas": len(huerfanas)}
            )
            if check_fallido is None:
                check_fallido = "reservas_huerfanas"

    t_self_test_ms = (time.monotonic() - t_start) * 1000

    if inconsistencias:
        return HeartBeatPayload(
            tipo="SELF_TEST_FAILED",
            timestamp_ms=int(time.time() * 1000),
            nodo=NODE_ID,
            inconsistencias=inconsistencias,
            self_test={
                "resultado": "FAILED",
                "check_fallido": check_fallido,
                "t_self_test_ms": t_self_test_ms,
            },
        )

    return HeartBeatPayload(
        tipo="SELF_TEST_OK",
        timestamp_ms=int(time.time() * 1000),
        nodo=NODE_ID,
        inconsistencias=[],
        self_test={
            "resultado": "OK",
            "checks_ejecutados": [
                "stock_negativo",
                "suma_reservas",
                "reservas_huerfanas",
            ],
            "t_self_test_ms": t_self_test_ms,
        },
    )
