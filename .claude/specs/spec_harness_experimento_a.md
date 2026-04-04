# Spec: Harness de Experimento A -- Validacion de H1 (ASR-1, Inconsistencias de Inventario)

## Objetivo

Implementar 5 scripts Python que ejecutan los casos de prueba del Experimento A, simulando cada tipo de inconsistencia de inventario y verificando que el sistema detecta, clasifica y corrige cada una en menos de 300 ms. Cada script es autocontenido: prepara el estado, inyecta la falla, observa la respuesta y emite un veredicto.

## Alcance

**En scope:**
- CP-A1: Happy path (control negativo)
- CP-A2: Stock negativo -> rollback
- CP-A3: Concurrencia (2 reservas simultaneas) -> resolucion de conflicto
- CP-A4: Divergencia de reservas -> reconciliacion
- CP-A5: Self-test fallido -> failover a INV-Standby
- Medicion de metricas: t_self_test, t_clasificacion_monitor, t_total
- Script orquestador que ejecuta los 5 en secuencia con reset entre ellos

**Fuera de scope:**
- Implementacion de los servicios (cubiertas por otras specs)
- Experimento B (spec separada)
- Analisis estadistico (spec de metricas)

## Criterios de Aceptacion

- [ ] Cada script es ejecutable con `python cp_a{N}.py` sin argumentos (configuracion via env vars)
- [ ] CP-A1: Verifica HeartBeat tipo SELF_TEST_OK en topic `heartbeat.inventario.ok`, ausencia de eventos de correccion
- [ ] CP-A2: Verifica HeartBeat tipo STOCK_NEGATIVO, rollback ejecutado, stock final == 9
- [ ] CP-A3: Verifica HeartBeat tipo ESTADO_CONCURRENTE, solo una reserva confirmada, stock final == 3
- [ ] CP-A4: Verifica HeartBeat tipo DIVERGENCIA_RESERVAS, reconciliacion ejecutada, stock consistente
- [ ] CP-A5: Verifica HeartBeat tipo SELF_TEST_FAILED, failover completado, INV-Standby responde en :8095
- [ ] Cada caso mide y reporta: t_self_test, t_clasificacion_monitor, t_total
- [ ] t_total (t1 - t0) < 300 ms para CP-A2, CP-A3, CP-A4
- [ ] t_failover < 500 ms para CP-A5
- [ ] Cada script imprime resultado en formato JSON: `{caso, veredicto, metricas, evidencias}`
- [ ] Script orquestador `run_experiment_a.py` ejecuta los 5 en orden con reset de stock entre ellos

## Inputs Requeridos

- Todos los servicios del CCP desplegados y operativos (specs de infraestructura, inventarios, monitor/corrector)
- NATS JetStream con streams creados
- MongoDB con stock inicial: COCA-COLA-350=9, AGUA-500=100, ARROZ-1KG=50
- Variables de entorno: `INV_URL`, `MONITOR_URL`, `NATS_URL`, `MONGODB_URL`

## Outputs Esperados

| Archivo | Descripcion |
|---|---|
| `experiments/experiment_a/cp_a1_happy_path.py` | Caso de prueba: happy path |
| `experiments/experiment_a/cp_a2_stock_negativo.py` | Caso de prueba: stock negativo -> rollback |
| `experiments/experiment_a/cp_a3_concurrencia.py` | Caso de prueba: 2 reservas simultaneas |
| `experiments/experiment_a/cp_a4_divergencia.py` | Caso de prueba: divergencia de reservas |
| `experiments/experiment_a/cp_a5_failover.py` | Caso de prueba: self-test fallido -> failover |
| `experiments/experiment_a/run_experiment_a.py` | Orquestador de los 5 casos |
| `experiments/experiment_a/helpers.py` | Funciones compartidas (reset stock, suscripcion NATS, medicion) |
| `experiments/experiment_a/config.py` | Configuracion de URLs y umbrales |
| `experiments/experiment_a/requirements.txt` | Dependencias (httpx, nats-py, pymongo) |

## Agente Responsable

`harness-asr1`

## Convenciones a Respetar

- Cada caso de prueba referencia el ASR-1 especifico que valida
- HeartBeat < 300 ms es el criterio de exito principal
- Los scripts usan `httpx` (async HTTP client) para las peticiones
- Suscripcion a NATS usa `nats-py` para observar HeartBeats en tiempo real
- La medicion de tiempos usa `time.monotonic_ns()` para precision sub-millisegundo
- Logs JSON estructurados para integracion con el validador de metricas
- Stock de COCA-COLA-350 se reinicia a 9 entre cada caso

## Pasos de Ejecucion

1. **Crear estructura de directorios:**
   ```
   experiments/experiment_a/
   ```

2. **Implementar `experiments/experiment_a/helpers.py`:**
   ```python
   import httpx
   import nats
   import time
   import json
   import asyncio
   from pymongo import MongoClient

   async def reset_inventario(inv_url: str):
       """Reinicia stock a valores iniciales."""
       async with httpx.AsyncClient() as client:
           await client.post(f"{inv_url}/reset")

   async def suscribir_heartbeat(nats_url: str, topic: str, timeout_s: float = 5.0):
       """Se suscribe a un topic NATS y espera un mensaje con timeout."""
       nc = await nats.connect(nats_url)
       js = nc.jetstream()
       sub = await js.subscribe(topic)
       try:
           msg = await asyncio.wait_for(sub.next_msg(), timeout=timeout_s)
           return json.loads(msg.data.decode())
       except asyncio.TimeoutError:
           return None
       finally:
           await nc.close()

   async def verificar_stock(inv_url: str, sku: str) -> int:
       """Obtiene stock actual de un SKU."""
       async with httpx.AsyncClient() as client:
           resp = await client.get(f"{inv_url}/inventario/{sku}")
           return resp.json()["stock"]

   def calcular_latencia(t_start_ns: int, t_end_ns: int) -> float:
       """Calcula latencia en milisegundos."""
       return (t_end_ns - t_start_ns) / 1_000_000

   def emitir_resultado(caso: str, veredicto: str, metricas: dict, evidencias: dict):
       """Imprime resultado estructurado."""
       resultado = {
           "caso": caso,
           "veredicto": veredicto,
           "metricas": metricas,
           "evidencias": evidencias,
           "timestamp": time.time(),
       }
       print(json.dumps(resultado, indent=2))
       return resultado
   ```

3. **Implementar CP-A1 (`cp_a1_happy_path.py`):**
   ```python
   """CP-A1: Happy path -- orden valida, self-test OK, sin correcciones.
   ASR-1 validado: control negativo (el sistema NO debe actuar).
   """
   async def main():
       await reset_inventario(config.INV_URL)

       # Suscribirse a heartbeat.inventario.ok ANTES de enviar la orden
       heartbeat_task = asyncio.create_task(
           suscribir_heartbeat(config.NATS_URL, "heartbeat.inventario.ok")
       )

       # Enviar orden valida: cantidad 5 <= stock 9
       async with httpx.AsyncClient() as client:
           resp = await client.post(f"{config.INV_URL}/reservar", json={
               "SKU": "COCA-COLA-350",
               "cantidad": 5,
               "actor_id": "tendero_001"
           })
           assert resp.status_code == 200, f"Reserva fallo: {resp.text}"

       # Esperar HeartBeat tipo OK
       heartbeat = await heartbeat_task
       assert heartbeat is not None, "No se recibio HeartBeat"
       assert heartbeat["tipo"] == "SELF_TEST_OK"
       assert heartbeat["self_test"]["resultado"] == "OK"

       # Verificar stock descontado
       stock = await verificar_stock(config.INV_URL, "COCA-COLA-350")
       assert stock == 4, f"Stock esperado 4, obtenido {stock}"

       # Verificar ausencia de eventos de correccion (esperar brevemente)
       correccion = await suscribir_heartbeat(
           config.NATS_URL, "correccion.*", timeout_s=2.0
       )
       assert correccion is None, "Se detecto evento de correccion inesperado"

       emitir_resultado("CP-A1", "PASS", {}, {"stock_final": stock})
   ```

4. **Implementar CP-A2 (`cp_a2_stock_negativo.py`):**
   ```python
   """CP-A2: Stock negativo -- reserva que deja SKU en -1 -> rollback.
   ASR-1 validado: VALCOH detecta stock negativo < 300 ms.
   """
   async def main():
       await reset_inventario(config.INV_URL)

       # Suscribirse a heartbeat ANTES de la orden
       heartbeat_task = asyncio.create_task(
           suscribir_heartbeat(config.NATS_URL, "heartbeat.inventario.stock_negativo")
       )
       t0 = time.monotonic_ns()

       # Enviar orden que excede stock: cantidad 10 > stock 9
       async with httpx.AsyncClient() as client:
           resp = await client.post(f"{config.INV_URL}/reservar", json={
               "SKU": "COCA-COLA-350",
               "cantidad": 10,
               "actor_id": "tendero_001"
           })

       # Esperar HeartBeat tipo STOCK_NEGATIVO
       heartbeat = await heartbeat_task
       t1 = time.monotonic_ns()

       assert heartbeat is not None, "No se recibio HeartBeat STOCK_NEGATIVO"
       assert heartbeat["tipo"] == "STOCK_NEGATIVO"

       t_deteccion = calcular_latencia(t0, t1)

       # Esperar a que el Corrector complete el rollback (max 500ms)
       await asyncio.sleep(0.5)

       # Verificar stock restaurado
       stock = await verificar_stock(config.INV_URL, "COCA-COLA-350")

       metricas = {
           "t_deteccion_ms": t_deteccion,
           "t_deteccion_ok": t_deteccion < 300,
       }
       evidencias = {
           "heartbeat_tipo": heartbeat["tipo"],
           "stock_final": stock,
           "stock_restaurado": stock == 9,
       }
       veredicto = "PASS" if (t_deteccion < 300 and stock == 9) else "FAIL"
       emitir_resultado("CP-A2", veredicto, metricas, evidencias)
   ```

5. **Implementar CP-A3 (`cp_a3_concurrencia.py`):**
   ```python
   """CP-A3: 2 reservas simultaneas del mismo SKU -> ESTADO_CONCURRENTE.
   ASR-1 validado: deteccion de conflicto concurrente.
   """
   async def main():
       await reset_inventario(config.INV_URL)

       heartbeat_task = asyncio.create_task(
           suscribir_heartbeat(config.NATS_URL, "heartbeat.inventario.estado_concurrente")
       )
       t0 = time.monotonic_ns()

       # Lanzar 2 reservas simultaneas de 6 unidades cada una (6+6=12 > 9)
       async with httpx.AsyncClient() as client:
           results = await asyncio.gather(
               client.post(f"{config.INV_URL}/reservar", json={
                   "SKU": "COCA-COLA-350", "cantidad": 6, "actor_id": "tendero_001"
               }),
               client.post(f"{config.INV_URL}/reservar", json={
                   "SKU": "COCA-COLA-350", "cantidad": 6, "actor_id": "tendero_002"
               }),
           )

       heartbeat = await heartbeat_task
       t1 = time.monotonic_ns()

       t_deteccion = calcular_latencia(t0, t1)

       # Esperar correccion
       await asyncio.sleep(0.5)
       stock = await verificar_stock(config.INV_URL, "COCA-COLA-350")

       metricas = {"t_deteccion_ms": t_deteccion, "t_deteccion_ok": t_deteccion < 300}
       evidencias = {
           "heartbeat_tipo": heartbeat["tipo"] if heartbeat else None,
           "stock_final": stock,
           "stock_esperado": 3,  # 9 - 6 = 3 (solo una reserva confirmada)
       }
       veredicto = "PASS" if (heartbeat and t_deteccion < 300 and stock == 3) else "FAIL"
       emitir_resultado("CP-A3", veredicto, metricas, evidencias)
   ```

6. **Implementar CP-A4 (`cp_a4_divergencia.py`):**
   ```python
   """CP-A4: Divergencia de reservas inyectada en BD.
   ASR-1 validado: VALCOH check 2 detecta inconsistencia sin transaccion activa.
   """
   async def main():
       await reset_inventario(config.INV_URL)

       heartbeat_task = asyncio.create_task(
           suscribir_heartbeat(config.NATS_URL, "heartbeat.inventario.divergencia_reservas")
       )

       # Inyectar divergencia directamente en MongoDB:
       # reservas_activas suman 7 pero stock_actual es 5 (no cuadra con stock_inicial 9)
       client_mongo = MongoClient(config.MONGODB_URL)
       db = client_mongo["ccp"]
       db.inventario.update_one(
           {"SKU": "COCA-COLA-350"},
           {"$set": {
               "stock": 5,
               "reservas_activas": [
                   {"id": "r1", "cantidad": 4, "actor_id": "tendero_x", "activa": True},
                   {"id": "r2", "cantidad": 3, "actor_id": "tendero_y", "activa": True},
               ]
           }}
       )
       t0 = time.monotonic_ns()

       # Esperar que el proximo ciclo de HeartBeat detecte la divergencia
       heartbeat = await heartbeat_task
       t1 = time.monotonic_ns()

       t_deteccion = calcular_latencia(t0, t1)

       # Esperar reconciliacion
       await asyncio.sleep(0.5)
       stock = await verificar_stock(config.INV_URL, "COCA-COLA-350")

       metricas = {"t_deteccion_ms": t_deteccion}
       evidencias = {
           "heartbeat_tipo": heartbeat["tipo"] if heartbeat else None,
           "stock_final": stock,
       }
       veredicto = "PASS" if (heartbeat and heartbeat["tipo"] == "DIVERGENCIA_RESERVAS") else "FAIL"
       emitir_resultado("CP-A4", veredicto, metricas, evidencias)
   ```

7. **Implementar CP-A5 (`cp_a5_failover.py`):**
   ```python
   """CP-A5: Self-test forzado a fallar -> failover a INV-Standby.
   ASR-1 validado: redundancia pasiva, failover completado < 500 ms.
   """
   async def main():
       await reset_inventario(config.INV_URL)

       heartbeat_task = asyncio.create_task(
           suscribir_heartbeat(config.NATS_URL, "heartbeat.inventario.self_test_failed")
       )
       t0 = time.monotonic_ns()

       # Forzar fallo del VALCOH via endpoint de inyeccion de fallas
       async with httpx.AsyncClient() as client:
           resp = await client.post(f"{config.INV_URL}/fault-inject", json={
               "tipo": "self_test_failed"
           })
           assert resp.status_code == 200

       heartbeat = await heartbeat_task
       t1 = time.monotonic_ns()

       # Esperar failover
       await asyncio.sleep(1.0)

       # Verificar que INV-Standby esta respondiendo
       async with httpx.AsyncClient() as client:
           try:
               resp = await client.get(f"{config.INV_STANDBY_URL}/health", timeout=2.0)
               standby_up = resp.status_code == 200
           except:
               standby_up = False

           # Verificar que INV-Standby puede atender reservas
           if standby_up:
               resp = await client.post(f"{config.INV_STANDBY_URL}/reservar", json={
                   "SKU": "AGUA-500", "cantidad": 1, "actor_id": "tendero_001"
               })
               standby_funcional = resp.status_code == 200
           else:
               standby_funcional = False

       t_failover = calcular_latencia(t0, time.monotonic_ns())

       metricas = {
           "t_deteccion_ms": calcular_latencia(t0, t1),
           "t_failover_ms": t_failover,
           "t_failover_ok": t_failover < 500,
       }
       evidencias = {
           "heartbeat_tipo": heartbeat["tipo"] if heartbeat else None,
           "standby_up": standby_up,
           "standby_funcional": standby_funcional,
       }
       veredicto = "PASS" if (heartbeat and standby_up and standby_funcional) else "FAIL"
       emitir_resultado("CP-A5", veredicto, metricas, evidencias)
   ```

8. **Implementar `run_experiment_a.py`:**
   ```python
   """Orquestador del Experimento A: ejecuta CP-A1 a CP-A5 en secuencia."""
   import asyncio
   import json
   from helpers import reset_inventario
   import config

   async def main():
       resultados = []
       casos = [
           ("CP-A1", "cp_a1_happy_path"),
           ("CP-A2", "cp_a2_stock_negativo"),
           ("CP-A3", "cp_a3_concurrencia"),
           ("CP-A4", "cp_a4_divergencia"),
           ("CP-A5", "cp_a5_failover"),
       ]

       for nombre, modulo in casos:
           print(f"\n{'='*60}")
           print(f"Ejecutando {nombre}...")
           print(f"{'='*60}")

           # Reset estado entre casos
           await reset_inventario(config.INV_URL)

           # Importar y ejecutar caso
           mod = __import__(modulo)
           resultado = await mod.main()
           resultados.append(resultado)

       # Resumen
       print(f"\n{'='*60}")
       print("RESUMEN EXPERIMENTO A")
       print(f"{'='*60}")
       for r in resultados:
           print(f"  {r['caso']}: {r['veredicto']}")

       # H1 confirmada si CP-A2 a CP-A5 pasan
       criticos = [r for r in resultados if r["caso"] != "CP-A1"]
       h1 = all(r["veredicto"] == "PASS" for r in criticos)
       print(f"\n  H1 (ASR-1): {'CONFIRMADA' if h1 else 'REFUTADA'}")

       # Guardar resultados
       with open("resultados_experiment_a.json", "w") as f:
           json.dump(resultados, f, indent=2)

   asyncio.run(main())
   ```

9. **Crear `requirements.txt`:**
   ```
   httpx>=0.25.0
   nats-py>=2.6.0
   pymongo>=4.6.0
   ```

10. **Verificar:** ejecutar cada script individualmente y confirmar output JSON correcto.

## Notas de Arquitectura

| Elemento | Decision | Razonamiento |
|---|---|---|
| Scripts Python puros | Sin framework de testing (pytest) | Simplicidad; cada script es un programa independiente que emite veredicto JSON |
| httpx async | Cliente HTTP asincrono | Permite lanzar requests concurrentes (CP-A3) y no bloquear mientras espera NATS |
| Suscripcion NATS antes de inyectar | Se suscribe al topic ANTES de enviar la orden | Evita race condition donde el HeartBeat se publica antes de que el observador este listo |
| Reset entre casos | POST /reset reinicia stock | Aislamiento de estado entre casos de prueba; cada caso parte del mismo estado inicial |
| Inyeccion directa a MongoDB (CP-A4) | Bypass del API para crear divergencia | La divergencia de reservas no se puede crear via la API normal (que mantiene consistencia) |
