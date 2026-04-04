# ASR 1 — Escenario 2 (Solo Detección): Inventarios detecta inconsistencia de stock

**Contexto:** El tendero genera una orden que pasa la validación de seguridad, pero al reservar el inventario un SKU queda en negativo. El Módulo de Inventarios ejecuta su Validador de Coherencia (VALCOH) en cada ciclo de HeartBeat y detecta la inconsistencia de tipo `STOCK_NEGATIVO`. Publica un HeartBeat clasificado al topic `heartbeat.inventario.stock_negativo` en NATS JetStream. El Monitor lo consume, identifica el tipo y determina la respuesta apropiada. Este diagrama muestra únicamente los mecanismos de detección activos: el self-test interno de Inventarios y el enrutamiento por tipo en el Monitor.

**Tácticas de detección activas (ASR 1 — Disponibilidad):**
- Disponibilidad → **Detección**: Self-test (VALCOH) — Inventarios verifica internamente la coherencia de stock en cada ciclo
- Disponibilidad → **Detección**: HeartBeat expandido — publica tipo de inconsistencia al topic clasificado en NATS JetStream
- Disponibilidad → **Detección**: Monitor como router — consume el HeartBeat, clasifica el tipo y determina la respuesta
- Disponibilidad → **Redundancia Pasiva**: INV-Standby en modo espera — se activa solo ante `SELF_TEST_FAILED` o timeout del HeartBeat

---

## Diagrama de secuencia — Solo detección

```mermaid
sequenceDiagram
    autonumber

    actor Tendero
    participant GW as API Gateway
    participant GO as Gestor de Órdenes
    participant VS as Validación CEP
    participant INV as Inventarios (nodo primario)
    participant INV_S as INV-Standby
    participant NATS as NATS JetStream
    participant MON as Monitor
    participant GP as Gestor de Pedidos

    Note over Tendero,GP: ── RECEPCIÓN Y VALIDACIÓN ──────────────────────────────────────

    Tendero->>GW: HTTPS — Enviar orden {SKU: "COCA-COLA-350", cantidad: 10}
    Note over GW: Valida JWT · verifica anti-spoofing<br/>Orden pasa la capa de acceso
    GW->>GO: HTTP — Reenviar orden validada
    GO->>VS: HTTP — Solicitar validación CEP

    Note over VS: Señal 1 — rate: ✓  ·  Señal 2 — concentración SKU: ✓  ·  Señal 3 — cancelaciones: ✓<br/>Motor de correlación: 0/3 señales activas → orden genuina · sin ataque

    VS-->>GO: ✓ Orden genuina · sin patrones de ataque

    Note over GO,GP: ── PROCESAMIENTO PARALELO ──────────────────────────────────────

    par Reserva de inventario
        GO->>INV: HTTP — Reservar {SKU: COCA-COLA-350, cantidad: 10}
        INV->>INV: Intentar actualizar stock
        Note over INV: Stock disponible: 9<br/>Cantidad solicitada: 10<br/>Stock resultante: 9 - 10 = -1 ❌<br/>Inconsistencia detectada internamente

        Note over INV: ── VALCOH — Self-test ejecutado ──────────────────────────<br/>Check 1 — stock >= 0 para todos los SKU: ❌ FALLO<br/>           COCA-COLA-350: stock = -1<br/>Check 2 — suma(reservas_activas) == stock_inicial - stock_actual: ❌ FALLO<br/>           Reservado: 10 · Disponible era: 9 · Delta excedido: 1<br/>Check 3 — reservas huérfanas (sin pedido activo): ✓<br/>Resultado del self-test: FAILED · Tipo de inconsistencia: STOCK_NEGATIVO

        INV->>NATS: [t0] Publicar HeartBeat clasificado<br/>Topic: heartbeat.inventario.stock_negativo<br/>{tipo: "STOCK_NEGATIVO",<br/> nodo: "inv-primary",<br/> inconsistencias: [{SKU: "COCA-COLA-350",<br/>   stock_real: -1,<br/>   stock_esperado: 9,<br/>   delta: -10}],<br/> self_test: {resultado: "FAILED",<br/>   checks_ejecutados: ["suma_reservas", "stock_negativo", "reservas_huerfanas"],<br/>   check_fallido: "stock_negativo"}}

    and Confirmación de pedido
        GO->>GP: HTTP — Crear pedido en firme {detalle de orden}
        GP->>GP: Registrar pedido · asignar ID
    end

    Note over INV_S: INV-Standby en modo espera.<br/>Réplica de estado activa vía MongoDB replica set.<br/>No procesa transacciones · no activa failover<br/>(el tipo STOCK_NEGATIVO no requiere failover).

    Note over MON: ── DETECCIÓN — Router de tipos ──────────────────────────────

    NATS-->>MON: [t1] HeartBeat recibido — Topic: heartbeat.inventario.stock_negativo

    Note over MON: Tipo recibido: STOCK_NEGATIVO<br/>SKU afectado: COCA-COLA-350 · stock_real: -1<br/><br/>Router de respuesta por tipo:<br/>  STOCK_NEGATIVO        → activar flujo de rollback vía Corrector<br/>  DIVERGENCIA_RESERVAS  → activar reconciliación de reservas<br/>  ESTADO_CONCURRENTE    → resolver conflicto (timestamp menor gana)<br/>  SELF_TEST_FAILED      → activar failover a INV-Standby<br/>  HEARTBEAT_AUSENTE     → activar failover a INV-Standby<br/><br/>Acción seleccionada: rollback<br/>⚠ Estado del sistema: ALERTA · inconsistencia detectada<br/>✅ Tiempo de detección: t1 - t0 < 300 ms  ← criterio ASR 1
```

---

## Notas de arquitectura — Detección (ASR 1)

| Momento | Táctica | Detalle |
|---|---|---|
| VALCOH ejecuta self-test en cada ciclo de HeartBeat | Detectar fallas — Self-test | El Validador de Coherencia verifica tres checks: stock >= 0, suma de reservas == diferencia de stock, y ausencia de reservas huérfanas. Es el único mecanismo que detecta inconsistencias que no emergen de una sola transacción aislada (ej. divergencias acumuladas o corrupción de contadores). |
| HeartBeat publicado a topic clasificado en NATS | Detectar fallas — HeartBeat expandido | El topic `heartbeat.inventario.stock_negativo` permite al Monitor suscribirse selectivamente al tipo sin parsear el payload. Elimina un paso de clasificación del path crítico y reduce la latencia de detección. |
| Monitor actúa como router por tipo | Detectar fallas — Router de inconsistencias | El Monitor implementa una tabla de despacho por tipo: rollback para `STOCK_NEGATIVO`, reconciliación para `DIVERGENCIA_RESERVAS`, resolución de conflicto para `ESTADO_CONCURRENTE`, failover para `SELF_TEST_FAILED` o timeout. |
| INV-Standby en modo pasivo | Disponibilidad — Redundancia Pasiva | El nodo standby mantiene réplica del estado vía MongoDB replica set. No se activa ante `STOCK_NEGATIVO` (el primario sigue operativo); se activa únicamente ante `SELF_TEST_FAILED` o ausencia de HeartBeat. |
| Trade-off: self-test añade latencia local | Impacto negativo — ASR 1 | El VALCOH consume < 50 ms del presupuesto de 300 ms para ejecutar los tres checks. Opera sobre datos en memoria (no consulta MongoDB en el path crítico), manteniendo el impacto acotado. |

> **Ventana de detección — ASR 1:** el intervalo `t1 - t0` (publicación del HeartBeat → consumo por el Monitor) es el tiempo de detección que el ASR 1 exige sea inferior a 300 ms. El self-test interno (VALCOH) consume < 50 ms de ese presupuesto. Los ~ 250 ms restantes los absorbe la latencia de NATS JetStream y el procesamiento del Monitor.

> **Alcance de este diagrama:** se muestra únicamente la detección de la inconsistencia y la clasificación del tipo. La corrección (rollback coordinado por el Corrector), el failover y la notificación al tendero se documentan en el escenario completo `ASR_escenario2_heartbeat_negativo.md`.
