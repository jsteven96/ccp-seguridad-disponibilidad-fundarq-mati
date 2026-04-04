# Spec: Monitor (Router de Inconsistencias) y Corrector

## Objetivo

Implementar dos servicios FastAPI: el `Monitor` que se suscribe a todos los topics de HeartBeat de inventario y actua como router de acciones segun el tipo de inconsistencia, y el `Corrector` que ejecuta las acciones de rollback, reconciliacion y resolucion de conflictos coordinados.

## Alcance

**En scope:**
- Servicio `Monitor` que suscribe a `heartbeat.inventario.*` via NATS JetStream
- Logica de routing por tipo de HeartBeat hacia acciones especificas
- Servicio `Corrector` con endpoints HTTP para rollback, reconciliacion y resolucion de conflictos
- Logica de failover: cuando Monitor recibe `SELF_TEST_FAILED` o detecta timeout de HeartBeat, senaliza promocion de INV-Standby
- Comunicacion Monitor -> Corrector via HTTP (sincrono para respuesta rapida)
- Comunicacion Monitor -> Gestor de Ordenes para marcar ordenes afectadas
- Manifiestos Kubernetes para ambos servicios
- Logs JSON estructurados con timestamps para metricas

**Fuera de scope:**
- La deteccion de inconsistencias (eso lo hace VALCOH en el Modulo de Inventarios)
- La validacion CEP/seguridad
- El colector de metricas (spec separada)

## Criterios de Aceptacion

- [ ] Monitor arranca y se suscribe exitosamente a `heartbeat.inventario.*`
- [ ] Cuando recibe HeartBeat tipo `STOCK_NEGATIVO`, invoca `POST /corrector/rollback`
- [ ] Cuando recibe HeartBeat tipo `DIVERGENCIA_RESERVAS`, invoca `POST /corrector/reconciliar`
- [ ] Cuando recibe HeartBeat tipo `ESTADO_CONCURRENTE`, invoca `POST /corrector/resolver-conflicto`
- [ ] Cuando recibe HeartBeat tipo `SELF_TEST_FAILED`, publica senal a `failover.inventario` y promueve INV-Standby
- [ ] Cuando no recibe HeartBeat en `HEARTBEAT_TIMEOUT_S` (default 10s), actua igual que `SELF_TEST_FAILED`
- [ ] `t_clasificacion_monitor` (tiempo de routing) < 10 ms
- [ ] Corrector ejecuta rollback: revierte stock y cancela pedido en paralelo
- [ ] Corrector ejecuta reconciliacion: recalcula stock real vs reservas
- [ ] Corrector ejecuta resolucion de conflicto: la reserva con timestamp menor gana
- [ ] Ambos servicios emiten logs JSON con timestamps para calculo de metricas
- [ ] Rollback + pedido coordinado completa en < 150 ms (t2 - t0)

## Inputs Requeridos

- Cluster Kind con NATS y MongoDB operativos (spec_infraestructura)
- Modulo de Inventarios desplegado y publicando HeartBeat (spec_modulo_inventarios)
- Topics NATS `heartbeat.inventario.*` con mensajes fluyendo

## Outputs Esperados

| Archivo | Descripcion |
|---|---|
| `services/monitor/main.py` | Servicio FastAPI del Monitor con suscripcion NATS |
| `services/monitor/router.py` | Logica de clasificacion y despacho por tipo |
| `services/monitor/failover.py` | Logica de failover (senal a INV-Standby) |
| `services/monitor/config.py` | Configuracion via env vars |
| `services/monitor/requirements.txt` | Dependencias |
| `services/monitor/Dockerfile` | Imagen Docker |
| `services/corrector/main.py` | Servicio FastAPI del Corrector |
| `services/corrector/rollback.py` | Logica de rollback coordinado |
| `services/corrector/reconciliacion.py` | Logica de reconciliacion de reservas |
| `services/corrector/conflicto.py` | Logica de resolucion de conflictos concurrentes |
| `services/corrector/config.py` | Configuracion |
| `services/corrector/requirements.txt` | Dependencias |
| `services/corrector/Dockerfile` | Imagen Docker |
| `k8s/monitor.yaml` | Deployment + Service del Monitor |
| `k8s/corrector.yaml` | Deployment + Service del Corrector |

## Agente Responsable

`monitor-corrector-implementor`

## Convenciones a Respetar

- El Monitor es pasivo: solo reacciona a HeartBeats, nunca consulta directamente a Inventarios
- El Corrector esta desacoplado del Monitor: recibe instrucciones via HTTP, no conoce la fuente del evento
- Respuestas enmascaradas al tendero: cuando el Monitor notifica al Gestor de Ordenes, el mensaje al usuario final debe ser generico ("Tu pedido esta siendo procesado, te notificaremos pronto")
- Logs JSON con campos: `timestamp_ms`, `service`, `event`, `heartbeat_tipo`, `accion`, `duracion_ms`
- Rollback en paralelo: inventario y pedido se revierten simultaneamente (asyncio.gather)
- HeartBeat timeout: si no se recibe HeartBeat en N segundos, asumir fallo del nodo primario

## Pasos de Ejecucion

1. **Crear estructura de directorios:**
   ```
   services/monitor/
   services/corrector/
   ```

2. **Implementar `services/monitor/router.py`:**
   ```python
   ROUTING_TABLE = {
       "STOCK_NEGATIVO": "rollback",
       "DIVERGENCIA_RESERVAS": "reconciliar",
       "ESTADO_CONCURRENTE": "resolver-conflicto",
       "SELF_TEST_FAILED": "failover",
       "SELF_TEST_OK": None,  # No action needed
   }

   async def clasificar_y_despachar(heartbeat: HeartBeatPayload) -> str:
       t_start = time.monotonic_ns()
       tipo = heartbeat.tipo
       accion = ROUTING_TABLE.get(tipo)
       t_clasificacion = (time.monotonic_ns() - t_start) / 1_000_000

       log_json(event="clasificacion", tipo=tipo, accion=accion, duracion_ms=t_clasificacion)

       if accion == "failover":
           await activar_failover(heartbeat)
       elif accion:
           await invocar_corrector(accion, heartbeat)
       return accion
   ```

3. **Implementar `services/monitor/main.py`:**
   - Suscripcion a NATS `heartbeat.inventario.>` (wildcard) usando JetStream consumer durable
   - Callback que parsea el HeartBeat y llama a `clasificar_y_despachar`
   - Watchdog: tarea asyncio que detecta timeout si no llega HeartBeat en `HEARTBEAT_TIMEOUT_S`
   - Endpoint `GET /health`
   - Endpoint `GET /metrics` que expone contadores por tipo de evento procesado

4. **Implementar `services/monitor/failover.py`:**
   - Publica mensaje a `failover.inventario` en NATS
   - Opcionalmente: usa Kubernetes API (via `kubernetes` Python client) para escalar el Deployment de INV-Standby y marcar su env var `ROLE=primary`
   - Log del evento de failover con timestamp

5. **Implementar `services/corrector/rollback.py`:**
   ```python
   async def ejecutar_rollback(heartbeat: HeartBeatPayload):
       """Rollback coordinado: inventario + pedido en paralelo."""
       sku = heartbeat.inconsistencias[0].SKU
       resultado_inv, resultado_ped = await asyncio.gather(
           revertir_inventario(sku, heartbeat),
           cancelar_pedido(heartbeat),
       )
       log_json(event="rollback_completado", sku=sku, ...)
   ```

6. **Implementar `services/corrector/reconciliacion.py`:**
   - Lee reservas activas de MongoDB
   - Calcula stock real = stock_inicial - sum(reservas_confirmadas)
   - Actualiza stock en MongoDB
   - Log de la reconciliacion

7. **Implementar `services/corrector/conflicto.py`:**
   - Lee las reservas en conflicto
   - La reserva con timestamp menor gana
   - Revierte la reserva perdedora
   - Actualiza stock

8. **Implementar `services/corrector/main.py`:**
   - Endpoints:
     - `POST /rollback`: recibe HeartBeatPayload, ejecuta rollback coordinado
     - `POST /reconciliar`: recibe HeartBeatPayload, ejecuta reconciliacion
     - `POST /resolver-conflicto`: recibe HeartBeatPayload, resuelve por timestamp
     - `GET /health`

9. **Crear Dockerfiles** para ambos servicios.

10. **Crear manifiestos Kubernetes:**
    - `k8s/monitor.yaml`: Deployment 1 replica, Service ClusterIP
    - `k8s/corrector.yaml`: Deployment 1 replica, Service ClusterIP

11. **Verificar:**
    - Monitor se conecta a NATS y recibe HeartBeats de tipo OK sin actuar
    - Forzar un STOCK_NEGATIVO y verificar que Monitor invoca Corrector rollback
    - Verificar logs JSON con timestamps correctos

## Notas de Arquitectura

| Elemento | Decision | Razonamiento |
|---|---|---|
| Monitor como router O(1) | Switch/dict lookup por tipo | t_clasificacion_monitor < 10 ms garantizado; sin logica de negocio pesada |
| Corrector desacoplado | HTTP sincrono Monitor -> Corrector | Permite reemplazar Corrector sin tocar Monitor; facilita testing independiente |
| Rollback paralelo | asyncio.gather para inventario + pedido | Reduce t_total; ambas operaciones son independientes |
| JetStream durable consumer | Consumer con nombre fijo para replay | Si el Monitor se reinicia, retoma desde donde quedo; no pierde HeartBeats |
| Watchdog timeout | Tarea asyncio con timer configurable | Detecta caida total del INV primario cuando no hay HeartBeat alguno |
| Failover via NATS + K8s API | Dual signal: NATS para servicios, K8s API para pods | NATS notifica a INV-Standby internamente; K8s API escala el deployment |
