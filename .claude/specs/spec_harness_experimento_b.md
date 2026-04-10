# Spec: Harness de Experimento B -- Validacion de H2 (ASR-2, DDoS de Negocio)

## Objetivo

Implementar 4 scripts Python que ejecutan los casos de prueba del Experimento B, simulando patrones de ataque DDoS de capa de negocio y verificando que el motor CEP detecta y bloquea atacantes en menos de 300 ms cuando se activan >= 2 senales, y NO bloquea tenderos legitimos.

## Alcance

**En scope:**
- CP-B1: Happy path -- tendero legitimo, sin bloqueo
- CP-B2: Ataque completo -- 3 senales activas, bloqueo < 300 ms
- CP-B3: 1 sola senal activa -- NO debe bloquear (validacion de falso positivo)
- CP-B4: 2 senales activas -- umbral minimo, debe bloquear
- Medicion de t_deteccion desde primera orden sospechosa hasta respuesta 429
- Script orquestador que ejecuta los 4 en secuencia

**Fuera de scope:**
- Implementacion del servicio CEP (spec_validacion_cep)
- Experimento A (spec separada)
- Analisis estadistico final (spec de metricas)

## Criterios de Aceptacion

- [ ] CP-B1: 4 ordenes con SKUs variados -> todas retornan 200, sin eventos de seguridad
- [ ] CP-B2: 47 ordenes en 60s, mismo SKU, 89% cancelaciones -> bloqueo con 429 < 300 ms, stock intacto, JWT revocado, IP bloqueada
- [ ] CP-B3: 8 ordenes/min (rate alto) pero SKUs variados y 0% cancelaciones -> 1 senal activa -> NO bloqueado
- [ ] CP-B4: rate alto + concentracion SKU alta, cancelaciones normales -> 2 senales activas -> bloqueado
- [ ] Cada caso mide y reporta: t_deteccion, codigo_respuesta, stock_delta
- [ ] El cuerpo del HTTP 429 NO contiene palabras clave de deteccion ("rate", "SKU", "CEP", "senal", "umbral")
- [ ] Cada script imprime resultado en formato JSON
- [ ] Script orquestador `run_experiment_b.py` ejecuta los 4 con reset entre ellos

## Inputs Requeridos

- Servicio ValidacionCEP desplegado y operativo (spec_validacion_cep)
- Servicio ModuloSeguridad desplegado (spec_validacion_cep)
- Servicio LogAuditoria desplegado (spec_validacion_cep)
- Servicio ModuloInventario desplegado (spec_modulo_inventarios) -- para verificar stock intacto
- MongoDB con stock inicial
- Variables de entorno: `CEP_URL`, `INV_URL`, `SEG_URL`, `MONGODB_URL`

## Outputs Esperados

| Archivo | Descripcion |
|---|---|
| `experiments/experiment_b/cp_b1_happy_path.py` | Caso de prueba: tendero legitimo |
| `experiments/experiment_b/cp_b2_ataque_completo.py` | Caso de prueba: 3 senales activas |
| `experiments/experiment_b/cp_b3_una_senal.py` | Caso de prueba: 1 senal (no debe bloquear) |
| `experiments/experiment_b/cp_b4_umbral_minimo.py` | Caso de prueba: 2 senales (umbral exacto) |
| `experiments/experiment_b/run_experiment_b.py` | Orquestador de los 4 casos |
| `experiments/experiment_b/helpers.py` | Funciones compartidas |
| `experiments/experiment_b/config.py` | Configuracion |
| `experiments/experiment_b/requirements.txt` | Dependencias |

## Agente Responsable

`harness-asr2`

## Convenciones a Respetar

- Respuestas enmascaradas: verificar que el 429 NO contiene informacion sobre criterios de deteccion
- Ventana de 60 s: las ordenes del ataque deben enviarse dentro de una ventana de 60 segundos
- Las 3 senales CEP: rate > umbral, concentracion SKU > 80%, tasa cancelacion > 50%
- Umbral >= 2 senales para bloqueo
- t_deteccion < 300 ms (criterio ASR-2)
- Scripts usan `httpx` async y `asyncio`
- Logs JSON estructurados

## Pasos de Ejecucion

1. **Crear estructura de directorios:**
   ```
   experiments/experiment_b/
   ```

2. **Implementar `experiments/experiment_b/helpers.py`:**
   ```python
   import httpx
   import time
   import json
   import asyncio

   PALABRAS_PROHIBIDAS = ["rate", "sku", "cep", "senal", "señal", "umbral",
                           "concentracion", "concentración", "cancelacion", "cancelación"]

   async def reset_cep(cep_url: str):
       """Limpia ventanas CEP, blocklist y tokens revocados."""
       async with httpx.AsyncClient() as client:
           await client.post(f"{cep_url}/reset")

   async def reset_seguridad(seg_url: str):
       """Limpia blocklist y tokens revocados."""
       async with httpx.AsyncClient() as client:
           await client.post(f"{seg_url}/reset")

   async def enviar_orden(cep_url: str, actor_id: str, sku: str, cantidad: int,
                          ip: str = "192.168.1.100", jwt_token: str = "valid_jwt") -> dict:
       """Envia una orden al CEP y retorna respuesta."""
       async with httpx.AsyncClient() as client:
           resp = await client.post(f"{cep_url}/validar-orden", json={
               "actor_id": actor_id,
               "sku": sku,
               "cantidad": cantidad,
               "ip": ip,
               "jwt_token": jwt_token,
           })
           return {
               "status_code": resp.status_code,
               "body": resp.json(),
               "timestamp_ns": time.monotonic_ns(),
           }

   def verificar_respuesta_enmascarada(body: dict) -> bool:
       """Verifica que la respuesta 429 no expone criterios de deteccion."""
       body_str = json.dumps(body).lower()
       for palabra in PALABRAS_PROHIBIDAS:
           if palabra in body_str:
               return False
       return True

   async def verificar_stock_intacto(inv_url: str, sku: str, stock_esperado: int) -> bool:
       """Verifica que el stock no fue afectado."""
       async with httpx.AsyncClient() as client:
           resp = await client.get(f"{inv_url}/inventario/{sku}")
           return resp.json()["stock"] == stock_esperado

   async def verificar_jwt_revocado(seg_url: str, jwt_token: str) -> bool:
       """Verifica que el JWT fue revocado."""
       async with httpx.AsyncClient() as client:
           resp = await client.get(f"{seg_url}/token-revocado/{jwt_token}")
           return resp.json().get("revocado", False)

   async def verificar_ip_bloqueada(seg_url: str, ip: str) -> bool:
       """Verifica que la IP fue bloqueada."""
       async with httpx.AsyncClient() as client:
           resp = await client.get(f"{seg_url}/blocklist")
           return ip in resp.json().get("ips", [])

   def emitir_resultado(caso: str, veredicto: str, metricas: dict, evidencias: dict):
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

3. **Implementar CP-B1 (`cp_b1_happy_path.py`):**
   ```python
   """CP-B1: Tendero legitimo -- 4 ordenes con SKUs variados, sin bloqueo.
   ASR-2 validado: control negativo (el CEP NO debe actuar).
   """
   async def main():
       await reset_cep(config.CEP_URL)
       await reset_seguridad(config.SEG_URL)

       skus = ["COCA-COLA-350", "AGUA-500", "ARROZ-1KG", "COCA-COLA-350"]
       respuestas = []

       for sku in skus:
           resp = await enviar_orden(config.CEP_URL, "tendero_001", sku, 1)
           respuestas.append(resp)
           await asyncio.sleep(0.5)  # Espaciadas para no disparar rate

       # Todas deben ser 200
       todas_ok = all(r["status_code"] == 200 for r in respuestas)

       # No debe haber eventos de seguridad
       jwt_ok = not await verificar_jwt_revocado(config.SEG_URL, "valid_jwt_tendero_001")
       ip_ok = not await verificar_ip_bloqueada(config.SEG_URL, "192.168.1.100")

       evidencias = {
           "respuestas": [r["status_code"] for r in respuestas],
           "jwt_revocado": not jwt_ok,
           "ip_bloqueada": not ip_ok,
       }
       veredicto = "PASS" if (todas_ok and jwt_ok and ip_ok) else "FAIL"
       emitir_resultado("CP-B1", veredicto, {}, evidencias)
   ```

4. **Implementar CP-B2 (`cp_b2_ataque_completo.py`):**
   ```python
   """CP-B2: Ataque DDoS completo -- 47 ordenes en 60s, 3 senales activas.
   ASR-2 validado: deteccion CEP < 300 ms con bloqueo total.
   """
   async def main():
       await reset_cep(config.CEP_URL)
       await reset_seguridad(config.SEG_URL)

       actor_id = "bot_8821"
       ip = "10.0.0.50"
       jwt = "jwt_bot_8821"

       # Preparar historial de cancelaciones altas (>50%)
       # Inyectar directamente en MongoDB o via endpoint de setup
       client_mongo = MongoClient(config.MONGODB_URL)
       db = client_mongo["ccp"]
       db.actor_stats.update_one(
           {"actor_id": actor_id},
           {"$set": {"total_ordenes": 100, "cancelaciones": 89}},
           upsert=True,
       )

       stock_antes = await verificar_stock(config.INV_URL, "COCA-COLA-350")

       t0 = time.monotonic_ns()
       primera_429 = None
       respuestas = []

       # Enviar 47 ordenes: 43 con COCA-COLA-350, 4 con otros SKUs
       for i in range(47):
           sku = "COCA-COLA-350" if i < 43 else "AGUA-500"
           resp = await enviar_orden(config.CEP_URL, actor_id, sku, 500, ip, jwt)
           respuestas.append(resp)

           if resp["status_code"] == 429 and primera_429 is None:
               primera_429 = resp
               t1 = resp["timestamp_ns"]

           # ~47 ordenes en 60 s => ~1.27 s entre ordenes
           await asyncio.sleep(1.27)

       t_deteccion = (t1 - t0) / 1_000_000 if primera_429 else float("inf")

       # Verificar respuesta enmascarada
       enmascarada = verificar_respuesta_enmascarada(primera_429["body"]) if primera_429 else False

       # Verificar stock intacto
       stock_despues = await verificar_stock(config.INV_URL, "COCA-COLA-350")
       stock_intacto = stock_despues == stock_antes

       # Verificar JWT revocado e IP bloqueada
       jwt_revocado = await verificar_jwt_revocado(config.SEG_URL, jwt)
       ip_bloqueada = await verificar_ip_bloqueada(config.SEG_URL, ip)

       # Verificar evento en log de auditoria
       evento_auditoria = db.auditoria.find_one({"actor_id": actor_id})

       metricas = {
           "t_deteccion_ms": t_deteccion,
           "t_deteccion_ok": t_deteccion < 300,
           "ordenes_enviadas": len(respuestas),
           "ordenes_bloqueadas": sum(1 for r in respuestas if r["status_code"] == 429),
       }
       evidencias = {
           "primera_429_en_orden": next((i for i, r in enumerate(respuestas) if r["status_code"] == 429), None),
           "respuesta_enmascarada": enmascarada,
           "stock_antes": stock_antes,
           "stock_despues": stock_despues,
           "stock_intacto": stock_intacto,
           "jwt_revocado": jwt_revocado,
           "ip_bloqueada": ip_bloqueada,
           "evento_auditoria": evento_auditoria is not None,
       }
       veredicto = "PASS" if (
           t_deteccion < 300 and stock_intacto and enmascarada
           and jwt_revocado and ip_bloqueada
       ) else "FAIL"
       emitir_resultado("CP-B2", veredicto, metricas, evidencias)
   ```

5. **Implementar CP-B3 (`cp_b3_una_senal.py`):**
   ```python
   """CP-B3: Una sola senal activa (rate alto) -- NO debe bloquear.
   ASR-2 validado: precision, 1 senal no alcanza umbral >= 2.
   """
   async def main():
       await reset_cep(config.CEP_URL)
       await reset_seguridad(config.SEG_URL)

       actor_id = "tendero_002"
       ip = "192.168.1.200"
       jwt = "jwt_tendero_002"

       # Historial limpio (0% cancelaciones)
       client_mongo = MongoClient(config.MONGODB_URL)
       db = client_mongo["ccp"]
       db.actor_stats.update_one(
           {"actor_id": actor_id},
           {"$set": {"total_ordenes": 50, "cancelaciones": 2}},
           upsert=True,
       )

       # Enviar 8 ordenes en 1 minuto con SKUs VARIADOS (no concentrados)
       skus = ["COCA-COLA-350", "AGUA-500", "ARROZ-1KG", "COCA-COLA-350",
               "AGUA-500", "ARROZ-1KG", "COCA-COLA-350", "AGUA-500"]
       respuestas = []

       for sku in skus:
           resp = await enviar_orden(config.CEP_URL, actor_id, sku, 1, ip, jwt)
           respuestas.append(resp)
           await asyncio.sleep(7.5)  # 8 ordenes en 60s = cada 7.5s

       # NINGUNA debe ser 429 (solo 1 senal: rate)
       alguna_bloqueada = any(r["status_code"] == 429 for r in respuestas)

       # JWT NO debe estar revocado
       jwt_ok = not await verificar_jwt_revocado(config.SEG_URL, jwt)

       evidencias = {
           "respuestas": [r["status_code"] for r in respuestas],
           "alguna_bloqueada": alguna_bloqueada,
           "jwt_revocado": not jwt_ok,
           "senales_esperadas": {"rate": True, "concentracion": False, "cancelaciones": False},
       }
       veredicto = "PASS" if (not alguna_bloqueada and jwt_ok) else "FAIL"
       emitir_resultado("CP-B3", veredicto, {}, evidencias)
   ```

6. **Implementar CP-B4 (`cp_b4_umbral_minimo.py`):**
   ```python
   """CP-B4: 2 senales activas (rate + concentracion SKU) -- debe bloquear.
   ASR-2 validado: umbral minimo de 2 senales es suficiente.
   """
   async def main():
       await reset_cep(config.CEP_URL)
       await reset_seguridad(config.SEG_URL)

       actor_id = "bot_5555"
       ip = "10.0.0.99"
       jwt = "jwt_bot_5555"

       # Historial de cancelaciones BAJO (no dispara senal 3)
       client_mongo = MongoClient(config.MONGODB_URL)
       db = client_mongo["ccp"]
       db.actor_stats.update_one(
           {"actor_id": actor_id},
           {"$set": {"total_ordenes": 100, "cancelaciones": 10}},  # 10% < 50%
           upsert=True,
       )

       t0 = time.monotonic_ns()
       primera_429 = None
       respuestas = []

       # Enviar 10 ordenes en 60s, 9 del mismo SKU (90% concentracion)
       # Rate: 10/min > umbral => senal 1 activa
       # Concentracion: 90% > 80% => senal 2 activa
       # Cancelaciones: 10% < 50% => senal 3 inactiva
       for i in range(10):
           sku = "COCA-COLA-350" if i < 9 else "AGUA-500"
           resp = await enviar_orden(config.CEP_URL, actor_id, sku, 1, ip, jwt)
           respuestas.append(resp)

           if resp["status_code"] == 429 and primera_429 is None:
               primera_429 = resp
               t1 = resp["timestamp_ns"]

           await asyncio.sleep(6)  # 10 ordenes en 60s

       t_deteccion = (t1 - t0) / 1_000_000 if primera_429 else float("inf")

       # Verificar bloqueo
       jwt_revocado = await verificar_jwt_revocado(config.SEG_URL, jwt)
       ip_bloqueada = await verificar_ip_bloqueada(config.SEG_URL, ip)
       enmascarada = verificar_respuesta_enmascarada(primera_429["body"]) if primera_429 else False

       metricas = {
           "t_deteccion_ms": t_deteccion,
           "t_deteccion_ok": t_deteccion < 300,
       }
       evidencias = {
           "primera_429_en_orden": next((i for i, r in enumerate(respuestas) if r["status_code"] == 429), None),
           "respuesta_enmascarada": enmascarada,
           "jwt_revocado": jwt_revocado,
           "ip_bloqueada": ip_bloqueada,
           "senales_esperadas": {"rate": True, "concentracion": True, "cancelaciones": False},
       }
       veredicto = "PASS" if (primera_429 and jwt_revocado and ip_bloqueada and enmascarada) else "FAIL"
       emitir_resultado("CP-B4", veredicto, metricas, evidencias)
   ```

7. **Implementar `run_experiment_b.py`:**
   ```python
   """Orquestador del Experimento B: ejecuta CP-B1 a CP-B4 en secuencia."""
   async def main():
       resultados = []
       casos = [
           ("CP-B1", "cp_b1_happy_path"),
           ("CP-B2", "cp_b2_ataque_completo"),
           ("CP-B3", "cp_b3_una_senal"),
           ("CP-B4", "cp_b4_umbral_minimo"),
       ]

       for nombre, modulo in casos:
           print(f"\n{'='*60}")
           print(f"Ejecutando {nombre}...")
           print(f"{'='*60}")

           # Reset estado entre casos
           await reset_cep(config.CEP_URL)
           await reset_seguridad(config.SEG_URL)

           mod = __import__(modulo)
           resultado = await mod.main()
           resultados.append(resultado)

       # Resumen
       print(f"\n{'='*60}")
       print("RESUMEN EXPERIMENTO B")
       print(f"{'='*60}")
       for r in resultados:
           print(f"  {r['caso']}: {r['veredicto']}")

       # H2 confirmada si CP-B2 y CP-B4 pasan (bloqueo) Y CP-B3 pasa (no falso positivo)
       criticos = {r["caso"]: r["veredicto"] for r in resultados}
       h2 = (criticos.get("CP-B2") == "PASS" and
              criticos.get("CP-B3") == "PASS" and
              criticos.get("CP-B4") == "PASS")
       print(f"\n  H2 (ASR-2): {'CONFIRMADA' if h2 else 'REFUTADA'}")

       with open("resultados_experiment_b.json", "w") as f:
           json.dump(resultados, f, indent=2)

   asyncio.run(main())
   ```

8. **Crear `requirements.txt`:**
   ```
   httpx>=0.25.0
   pymongo>=4.6.0
   ```

9. **Verificar:** ejecutar cada script individualmente contra servicios desplegados.

## Notas de Arquitectura

| Elemento | Decision | Razonamiento |
|---|---|---|
| Sleep entre ordenes | Simula patron temporal realista | 47 ordenes en 60 s = 1.27 s entre ordenes; sin sleep se enviarian todas al instante y no probaria la ventana deslizante |
| Inyeccion de historial en MongoDB | Precarga cancelaciones historicas del actor | La senal 3 requiere historial; no podemos generarlo en tiempo real en el harness |
| Verificacion de enmascaramiento | Busca palabras prohibidas en el body del 429 | Cumple convencion critica de CLAUDE.md: nunca exponer criterios de deteccion |
| CP-B3 como control de falso positivo | 1 senal activa NO debe bloquear | Valida que el umbral >= 2 funciona correctamente; evita falsos positivos |
| t_deteccion medido desde primera orden | No desde el inicio del script | El criterio ASR-2 es "tiempo de identificacion" desde que el patron es detectable |
