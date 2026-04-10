# Spec: Modulo de Inventarios con VALCOH y HeartBeat Clasificado

## Objetivo

Implementar el servicio FastAPI `ModuloInventario` que gestiona el stock de productos, ejecuta un Validador de Coherencia (VALCOH) interno en cada ciclo de HeartBeat, y publica HeartBeats clasificados por tipo de inconsistencia a topics NATS especificos. Incluye un pod standby que replica estado via MongoDB para failover.

## Alcance

**En scope:**
- Servicio FastAPI `modulo_inventario` (pod primario en puerto :8090)
- VALCOH interno con 3 checks periodicos
- Publicacion de HeartBeat clasificado a 5 topics NATS
- Endpoint `POST /reservar` para reservas de stock
- Endpoint `GET /inventario/{sku}` para consultar stock
- Endpoint `POST /fault-inject` para inyeccion de fallas (solo para testing)
- Endpoint `GET /health` para health check
- Pod standby `ModuloInventario-standby` (:8095) que replica estado via MongoDB secondary
- Manifiestos Kubernetes (Deployment, Service) para ambos pods
- Dockerfile del servicio

**Fuera de scope:**
- Logica de Monitor/Corrector (spec separada)
- Validacion CEP/seguridad (spec separada)
- Gestor de Ordenes y Gestor de Pedidos (no se implementan como servicios separados en este harness; el harness inyecta directamente)

## Criterios de Aceptacion

- [ ] `POST /reservar {SKU: "COCA-COLA-350", cantidad: 5, actor_id: "tendero_001"}` retorna 200 y descuenta stock
- [ ] VALCOH ejecuta 3 checks en cada ciclo: stock >= 0, suma_reservas == delta_stock, reservas_huerfanas
- [ ] HeartBeat publicado a NATS cada 2 segundos (configurable via env var `HEARTBEAT_INTERVAL_S`)
- [ ] Cuando stock queda negativo, HeartBeat va a `heartbeat.inventario.stock_negativo`
- [ ] Cuando suma_reservas diverge, HeartBeat va a `heartbeat.inventario.divergencia_reservas`
- [ ] Cuando hay conflicto de concurrencia, HeartBeat va a `heartbeat.inventario.estado_concurrente`
- [ ] Cuando self-test falla estructuralmente, HeartBeat va a `heartbeat.inventario.self_test_failed`
- [ ] Cuando todo OK, HeartBeat va a `heartbeat.inventario.ok`
- [ ] `t_self_test` (duracion del VALCOH) < 50 ms
- [ ] Payload del HeartBeat sigue el esquema JSON definido en seccion 3.5 del diseno
- [ ] Pod standby en :8095 responde a `/health` y puede promover a primario
- [ ] Endpoint `POST /fault-inject` permite forzar un `SELF_TEST_FAILED`
- [ ] Endpoint `POST /reservar` con concurrencia (2 requests simultaneos) detecta conflicto

## Inputs Requeridos

- Cluster Kind operativo (spec_infraestructura)
- NATS JetStream con streams creados
- MongoDB Replica Set operativo
- Estado inicial de inventario: COCA-COLA-350=9, AGUA-500=100, ARROZ-1KG=50

## Outputs Esperados

| Archivo | Descripcion |
|---|---|
| `services/modulo_inventario/main.py` | Servicio FastAPI principal |
| `services/modulo_inventario/valcoh.py` | Validador de Coherencia (3 checks) |
| `services/modulo_inventario/heartbeat.py` | Publicador de HeartBeat a NATS |
| `services/modulo_inventario/models.py` | Modelos Pydantic (ReservaRequest, HeartBeatPayload, etc.) |
| `services/modulo_inventario/db.py` | Conexion a MongoDB y operaciones CRUD de inventario |
| `services/modulo_inventario/config.py` | Configuracion via variables de entorno |
| `services/modulo_inventario/requirements.txt` | Dependencias Python |
| `services/modulo_inventario/Dockerfile` | Imagen Docker del servicio |
| `k8s/modulo-inventario.yaml` | Deployment + Service del pod primario |
| `k8s/modulo-inventario-standby.yaml` | Deployment + Service del pod standby |
| `scripts/init_inventory.py` | Script para inicializar stock en MongoDB |

## Agente Responsable

`inventario-implementor`

## Convenciones a Respetar

- HeartBeat de baja latencia: VALCOH opera en memoria sin consultar MongoDB en el path critico del self-test; lee estado cacheado y solo consulta MongoDB para reconciliacion
- Payload HeartBeat sigue el esquema JSON de seccion 3.5 del diseno del experimento
- Logs JSON estructurados con campos: `timestamp`, `service`, `event`, `data`
- Respuestas enmascaradas: si hay error interno, el endpoint retorna un mensaje generico sin exponer detalles del VALCOH
- El HeartBeat debe incluir `timestamp_ms` con precision de milisegundos para calculo de latencia

## Pasos de Ejecucion

1. **Crear estructura de directorios:**
   ```
   services/modulo_inventario/
   k8s/
   scripts/
   ```

2. **Implementar `models.py`** con modelos Pydantic:
   - `ReservaRequest`: SKU, cantidad, actor_id
   - `HeartBeatPayload`: tipo (enum), timestamp_ms, nodo, inconsistencias (lista), self_test (resultado, checks_ejecutados, check_fallido)
   - `InventarioItem`: SKU, stock, reservas_activas (lista)

3. **Implementar `db.py`:**
   - Conexion a MongoDB usando `motor` (async driver)
   - Coleccion `inventario` con documentos {SKU, stock, reservas_activas: [{id, cantidad, actor_id, timestamp}]}
   - Operaciones: `get_stock(sku)`, `update_stock(sku, delta)`, `add_reserva(sku, reserva)`, `remove_reserva(sku, reserva_id)`, `get_all_reservas(sku)`
   - Usar MongoDB transactions para atomicidad en reservas

4. **Implementar `valcoh.py`** (Validador de Coherencia):
   ```python
   async def ejecutar_self_test(sku: str, estado_cache: dict) -> SelfTestResult:
       t_start = time.monotonic_ns()
       checks = []

       # Check 1: stock >= 0
       check1 = estado_cache["stock"] >= 0
       checks.append(("stock_negativo", check1))

       # Check 2: suma_reservas == stock_inicial - stock_actual
       suma_reservas = sum(r["cantidad"] for r in estado_cache["reservas_activas"])
       stock_delta = estado_cache["stock_inicial"] - estado_cache["stock"]
       check2 = suma_reservas == stock_delta
       checks.append(("suma_reservas", check2))

       # Check 3: no hay reservas huerfanas (reservas sin orden activa)
       check3 = all(r["activa"] for r in estado_cache["reservas_activas"])
       checks.append(("reservas_huerfanas", check3))

       t_elapsed_ms = (time.monotonic_ns() - t_start) / 1_000_000
       # Clasificar tipo de inconsistencia
       ...
   ```

5. **Implementar `heartbeat.py`:**
   - Tarea asyncio que ejecuta cada `HEARTBEAT_INTERVAL_S` segundos
   - Llama a `valcoh.ejecutar_self_test()` con estado cacheado
   - Publica payload JSON al topic NATS correspondiente segun clasificacion
   - Emite log JSON con `t_self_test` para el colector de metricas

6. **Implementar `main.py`:**
   - FastAPI app con endpoints:
     - `POST /reservar`: recibe ReservaRequest, ejecuta reserva con transaction MongoDB, actualiza cache
     - `GET /inventario/{sku}`: retorna stock actual
     - `GET /health`: retorna 200 + info del nodo
     - `POST /fault-inject`: acepta `{tipo: "self_test_failed"}` para forzar fallo del VALCOH
     - `POST /reset`: reinicia stock a valores iniciales (para entre casos de prueba)
   - Al arrancar: inicializa cache de estado, inicia tarea de HeartBeat
   - Manejo de concurrencia: usar MongoDB `findOneAndUpdate` con version field para deteccion de conflictos optimista

7. **Crear `Dockerfile`:**
   ```dockerfile
   FROM python:3.11-slim
   WORKDIR /app
   COPY requirements.txt .
   RUN pip install --no-cache-dir -r requirements.txt
   COPY . .
   CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8090"]
   ```

8. **Crear manifiestos Kubernetes:**
   - `k8s/modulo-inventario.yaml`: Deployment con 1 replica, nodeSelector `node-role: primary`, Service ClusterIP en 8090
   - `k8s/modulo-inventario-standby.yaml`: Deployment con 1 replica, nodeSelector `node-role: standby`, Service ClusterIP en 8095, env var `ROLE=standby` (modo lectura, no publica HeartBeat hasta ser promovido)

9. **Crear `scripts/init_inventory.py`:**
   - Conecta a MongoDB y crea documentos iniciales:
     - COCA-COLA-350: stock=9
     - AGUA-500: stock=100
     - ARROZ-1KG: stock=50

10. **Verificar:**
    - Build de imagen Docker exitoso
    - Pod arranca y responde en `/health`
    - HeartBeat publicandose a NATS cada N segundos
    - Reserva exitosa descuenta stock
    - VALCOH detecta stock negativo cuando se fuerza

## Notas de Arquitectura

| Elemento | Decision | Razonamiento |
|---|---|---|
| VALCOH en memoria (cache) | No consulta MongoDB en cada ciclo de self-test | Cumplir con t_self_test < 50 ms; el cache se actualiza en cada operacion de escritura |
| Deteccion de concurrencia optimista | Version field en documento MongoDB | Evita locks pesimistas que aumentarian la latencia; si hay conflicto, se detecta en el self-test |
| 5 topics NATS separados | Un topic por tipo de inconsistencia | Permite al Monitor suscribirse selectivamente y rutear sin parsear el payload |
| Standby en modo pasivo | Lee de MongoDB secondary, no publica HeartBeat | Solo se activa tras senal de failover; evita split-brain |
| Endpoint de fault-inject | Solo para harness de testing | Permite simular SELF_TEST_FAILED sin corromper estado real |
