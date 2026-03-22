# ASR — Escenario 2: HeartBeat detecta inventario negativo → Corrección y rollback

**Contexto:** El tendero genera una orden que pasa la validación de seguridad, pero al reservar el inventario un SKU queda en negativo — se reservó más de lo que había. El módulo de inventarios lo detecta internamente y notifica al Monitor vía webhook HeartBeat. El Monitor recupera la orden problemática del Gestor de Órdenes, activa la cadena de corrección a través del Gestor de Pedidos y el Corrector ejecuta el rollback en paralelo. El tendero recibe un mensaje accionable.

**Tácticas activas:**
- Disponibilidad → **Detección**: HeartBeat — Inventario envía webhook de stock negativo al Monitor en < 300 ms
- Disponibilidad → **Enmascarar**: El tendero recibe una respuesta controlada, sin exponer el error interno
- Disponibilidad → **Corregir**: Monitor activa cadena Gestor de Pedidos → Corrector → rollback paralelo
- Seguridad → **Detección**: Validación de seguridad (DDoS / orden fantasma) — pasa en este escenario
- Seguridad → **Reaccionar**: Log del evento de corrección para análisis posterior

---

## Diagrama de secuencia

```mermaid
sequenceDiagram
    autonumber

    actor Tendero
    participant GO as Gestor de Órdenes
    participant VS as Validación de Seguridad
    participant INV as Módulo de Inventarios
    participant GP as Gestor de Pedidos
    participant MON as Monitor
    participant CORR as Corrector
    participant NOTIF as Notificaciones → Tendero

    Note over Tendero,NOTIF: ── RECEPCIÓN DE ORDEN ──────────────────────────────────────

    Tendero->>GO: Enviar orden {SKU: "COCA-COLA-350", cantidad: 10}
    GO->>VS: Validar orden (REST síncrono)

    Note over VS: Evalúa ventana de comportamiento del actor<br/>Señal 1 — rate de órdenes: dentro del umbral<br/>Señal 2 — concentración de SKU: normal<br/>Señal 3 — tasa de cancelación: 0 %

    VS-->>GO: ✓ Orden genuina · sin patrones de ataque

    Note over GO,GP: ── PROCESAMIENTO PARALELO ───────────────────────────────────

    GO->>NOTIF: Orden recibida · en procesamiento

    par Reserva de inventario
        GO->>INV: Reservar {SKU: COCA-COLA-350, cantidad: 10}
        INV->>INV: Intentar actualizar stock
        Note over INV: Stock disponible era 9.<br/>Se reservaron 10 → stock queda en -1.<br/>Inventario negativo detectado internamente.
        INV-->>GO: ✓ Reserva registrada
        INV->>MON: [webhook HeartBeat] {SKU: COCA-COLA-350, stock: -1, estado: NEGATIVO}

    and Confirmación de pedido
        GO->>GP: Crear pedido {detalle de orden}
        GP->>GP: Registrar pedido · asignar ID · programar despacho
        GP-->>GO: ✓ Pedido registrado · ID asignado
    end

    Note over MON,CORR: ── DETECCIÓN Y CORRECCIÓN ───────────────────────────────────

    MON->>MON: HeartBeat recibido · stock negativo en SKU: COCA-COLA-350
    Note over MON: Stock negativo detectado.<br/>Consulta al Gestor de Órdenes para identificar la orden problemática.

    MON->>GO: Consultar última orden con SKU: COCA-COLA-350
    GO-->>MON: Orden problemática {orden_id, tendero_id, SKU, cantidad: 10}

    MON->>GP: Marcar orden {orden_id} como problemática
    GP-->>MON: ✓ Orden marcada

    GP->>CORR: Ejecutar corrección {orden_id, SKU: COCA-COLA-350, cantidad: 10}

    Note over CORR: Inicia rollback coordinado.

    par Rollback en inventario
        CORR->>INV: Revertir reserva {SKU: COCA-COLA-350, cantidad: 10}
        INV->>INV: Aplicar rollback · stock vuelve a 9
        INV-->>CORR: ✓ Inventario restaurado · stock: 9
        INV->>MON: [webhook HeartBeat] {SKU: COCA-COLA-350, stock: 9, estado: OK}

    and Cancelación del pedido
        CORR->>GP: Cancelar pedido {orden_id}
        GP->>GP: Marcar pedido cancelado · liberar despacho
        GP-->>CORR: ✓ Pedido cancelado · despacho liberado
    end

    CORR-->>GO: Corrección completada · orden {orden_id} revertida
    GO->>GO: Registrar evento de corrección en log de auditoría

    Note over GO,NOTIF: ── NOTIFICACIÓN AL TENDERO ─────────────────────────────────

    GO->>NOTIF: Problema con la orden · requiere acción del tendero
    NOTIF-->>Tendero: ✗ No fue posible confirmar tu pedido.<br/>El SKU COCA-COLA-350 no tiene stock suficiente.<br/>Cantidad disponible: 9. Por favor ajusta tu orden.
```

---

## Notas de arquitectura

| Momento | Decisión | Razonamiento |
|---|---|---|
| INV envía HeartBeat vía webhook | Detectar fallas — HeartBeat | HTTP POST directo al Monitor; sin intermediario de mensajería; latencia mínima para cumplir < 300 ms |
| Monitor consulta GO para correlacionar | Correlación del evento | El SKU en el HeartBeat permite identificar qué orden generó el problema; GO es la fuente de verdad de órdenes |
| GP activa al Corrector (no el Monitor) | Corrector desacoplado | El Corrector es reutilizable para otros escenarios de rollback sin depender del flujo HeartBeat específico (ADR-03) |
| Rollback paralelo en inventario y pedidos | Corregir — Rollback coordinado | Ambas correcciones deben ejecutarse; hacerlas en paralelo reduce el tiempo que el stock permanece en negativo |
| Tendero notificado sin exponer el error | Enmascarar — respuesta controlada | El cliente recibe mensaje accionable (cantidad disponible = 9) sin ver trazas internas del sistema |
| Log de auditoría en Gestor de Órdenes | Recuperarse — Manejo de log de eventos | Permite análisis forense posterior y detección de patrones recurrentes |

> **Ventana de tiempo en negativo:** existe un intervalo entre que el inventario queda en negativo y el Corrector aplica el rollback. Este intervalo debe minimizarse con un HeartBeat de baja latencia (< 300 ms). Durante ese intervalo, el stock negativo es interno al sistema y no visible al tendero.

> **El Corrector como componente desacoplado:** recibe la orden del Gestor de Pedidos, no del Monitor directamente. Esto permite que el Corrector sea reutilizable para otros escenarios de rollback sin depender del flujo de detección específico.
