# Spec: Validacion CEP (Complex Event Processing) y Modulo de Seguridad

## Objetivo

Implementar el servicio FastAPI `ValidacionCEP` que analiza ordenes entrantes en una ventana deslizante de 60 segundos por actor, evalua 3 senales de comportamiento anomalo, y bloquea ordenes cuando se detecta un patron de ataque DDoS de capa de negocio. Incluye el Modulo de Seguridad que ejecuta la revocacion de JWT y bloqueo de IP.

## Alcance

**En scope:**
- Servicio `ValidacionCEP` (:8081) con motor de correlacion de 3 senales
- Ventana deslizante de 60 s por `actor_id` implementada en memoria
- Senal 1: rate de ordenes > umbral configurable (default: 5 ordenes/minuto)
- Senal 2: concentracion de SKU > 80% (un solo SKU domina las ordenes)
- Senal 3: tasa de cancelacion historica > 50%
- Motor de correlacion: >= 2 senales activas = ataque confirmado
- Respuesta enmascarada HTTP 429 sin exponer criterios de deteccion
- Modulo de Seguridad: revocacion de JWT, bloqueo temporal de IP, notificacion
- Log de Auditoria independiente en MongoDB
- Manifiestos Kubernetes

**Fuera de scope:**
- API Gateway y autenticacion JWT (simulados en el harness)
- Anti-Spoofing de red
- Gestor de Ordenes (el harness invoca directamente al CEP)

## Criterios de Aceptacion

- [ ] Endpoint `POST /validar-orden` acepta `{actor_id, sku, cantidad, ip, jwt_token, timestamp}`
- [ ] Con 0-1 senales activas: retorna 200 `{valido: true}`
- [ ] Con >= 2 senales activas: retorna 429 `{mensaje: "Sesion suspendida temporalmente"}`
- [ ] El cuerpo del 429 NO contiene las palabras: "rate", "SKU", "CEP", "senal", "umbral", "concentracion", "cancelacion"
- [ ] Ventana deslizante de 60 s: ordenes anteriores a 60 s no cuentan para el calculo
- [ ] Senal 1 (rate): se activa cuando ordenes_en_ventana > RATE_THRESHOLD (configurable)
- [ ] Senal 2 (concentracion SKU): se activa cuando max_sku_count / total_ordenes > 0.80
- [ ] Senal 3 (cancelaciones): se activa cuando cancelaciones_historicas / ordenes_historicas > 0.50
- [ ] `t_deteccion` (tiempo desde inicio de evaluacion hasta decision) < 300 ms
- [ ] Cuando se detecta ataque: JWT revocado, IP bloqueada, evento registrado en Log de Auditoria
- [ ] `GET /blocklist` retorna lista de IPs bloqueadas
- [ ] `GET /health` retorna 200
- [ ] `POST /reset` limpia ventanas, blocklist y tokens revocados (para entre casos de prueba)

## Inputs Requeridos

- Cluster Kind con MongoDB operativo (spec_infraestructura)
- No depende de NATS (el CEP recibe ordenes via HTTP sincrono)

## Outputs Esperados

| Archivo | Descripcion |
|---|---|
| `services/validacion_cep/main.py` | Servicio FastAPI con endpoint de validacion |
| `services/validacion_cep/motor_cep.py` | Motor de correlacion de senales |
| `services/validacion_cep/ventana.py` | Implementacion de ventana deslizante por actor |
| `services/validacion_cep/senales.py` | Calculo de cada una de las 3 senales |
| `services/validacion_cep/models.py` | Modelos Pydantic |
| `services/validacion_cep/config.py` | Configuracion (umbrales, ventana) |
| `services/validacion_cep/requirements.txt` | Dependencias |
| `services/validacion_cep/Dockerfile` | Imagen Docker |
| `services/modulo_seguridad/main.py` | Servicio de seguridad (revocacion JWT, bloqueo IP) |
| `services/modulo_seguridad/jwt_manager.py` | Gestion de tokens revocados |
| `services/modulo_seguridad/ip_blocker.py` | Gestion de IPs bloqueadas |
| `services/modulo_seguridad/requirements.txt` | Dependencias |
| `services/modulo_seguridad/Dockerfile` | Imagen Docker |
| `services/log_auditoria/main.py` | Servicio de log de auditoria independiente |
| `services/log_auditoria/requirements.txt` | Dependencias |
| `services/log_auditoria/Dockerfile` | Imagen Docker |
| `k8s/validacion-cep.yaml` | Deployment + Service |
| `k8s/modulo-seguridad.yaml` | Deployment + Service |
| `k8s/log-auditoria.yaml` | Deployment + Service |

## Agente Responsable

`cep-implementor`

## Convenciones a Respetar

- Respuestas enmascaradas: el HTTP 429 retorna un mensaje generico sin exponer criterios de deteccion. Jamas incluir en la respuesta informacion sobre senales, umbrales, o logica interna del CEP
- Ventana deslizante de 60 s: segun definicion en CLAUDE.md y diseno del experimento
- Umbral >= 2 senales = ataque confirmado (definido en CLAUDE.md)
- Log de Auditoria independiente del Modulo de Seguridad para garantizar persistencia forense
- Logs JSON estructurados con campos: `timestamp_ms`, `service`, `event`, `actor_id`, `senales_activas`, `score`, `accion`
- t_deteccion < 300 ms (criterio ASR-2)

## Pasos de Ejecucion

1. **Crear estructura de directorios:**
   ```
   services/validacion_cep/
   services/modulo_seguridad/
   services/log_auditoria/
   ```

2. **Implementar `services/validacion_cep/ventana.py`:**
   ```python
   from collections import defaultdict, deque
   import time

   class VentanaDeslizante:
       def __init__(self, duracion_s: int = 60):
           self.duracion_s = duracion_s
           self.ordenes_por_actor: dict[str, deque] = defaultdict(deque)

       def agregar_orden(self, actor_id: str, orden: dict):
           ahora = time.time()
           cola = self.ordenes_por_actor[actor_id]
           cola.append({"timestamp": ahora, **orden})
           # Purgar ordenes fuera de ventana
           while cola and cola[0]["timestamp"] < ahora - self.duracion_s:
               cola.popleft()

       def obtener_ordenes(self, actor_id: str) -> list[dict]:
           ahora = time.time()
           cola = self.ordenes_por_actor[actor_id]
           while cola and cola[0]["timestamp"] < ahora - self.duracion_s:
               cola.popleft()
           return list(cola)

       def limpiar(self):
           self.ordenes_por_actor.clear()
   ```

3. **Implementar `services/validacion_cep/senales.py`:**
   ```python
   def evaluar_senal_rate(ordenes_en_ventana: list, umbral: int = 5) -> bool:
       """Senal 1: rate de ordenes > umbral por minuto."""
       return len(ordenes_en_ventana) > umbral

   def evaluar_senal_concentracion_sku(ordenes_en_ventana: list, umbral: float = 0.80) -> bool:
       """Senal 2: un solo SKU representa > 80% de las ordenes."""
       if not ordenes_en_ventana:
           return False
       sku_counts = {}
       for orden in ordenes_en_ventana:
           sku = orden.get("sku", "")
           sku_counts[sku] = sku_counts.get(sku, 0) + 1
       max_count = max(sku_counts.values())
       return (max_count / len(ordenes_en_ventana)) > umbral

   def evaluar_senal_cancelaciones(actor_id: str, db, umbral: float = 0.50) -> bool:
       """Senal 3: tasa de cancelacion historica > 50%."""
       stats = db.get_actor_stats(actor_id)
       if stats["total_ordenes"] == 0:
           return False
       return (stats["cancelaciones"] / stats["total_ordenes"]) > umbral
   ```

4. **Implementar `services/validacion_cep/motor_cep.py`:**
   ```python
   async def evaluar_orden(actor_id: str, orden: dict, ventana: VentanaDeslizante, db) -> CepResult:
       t_start = time.monotonic_ns()

       ventana.agregar_orden(actor_id, orden)
       ordenes = ventana.obtener_ordenes(actor_id)

       senales = {
           "rate": evaluar_senal_rate(ordenes, config.RATE_THRESHOLD),
           "concentracion_sku": evaluar_senal_concentracion_sku(ordenes, config.SKU_THRESHOLD),
           "cancelaciones": evaluar_senal_cancelaciones(actor_id, db, config.CANCEL_THRESHOLD),
       }

       activas = sum(1 for v in senales.values() if v)
       es_ataque = activas >= 2

       t_deteccion_ms = (time.monotonic_ns() - t_start) / 1_000_000

       return CepResult(
           es_ataque=es_ataque,
           senales=senales,
           senales_activas=activas,
           t_deteccion_ms=t_deteccion_ms,
       )
   ```

5. **Implementar `services/validacion_cep/main.py`:**
   - Endpoint `POST /validar-orden`:
     - Recibe `{actor_id, sku, cantidad, ip, jwt_token}`
     - Primero verifica si el JWT o IP ya estan bloqueados -> 401/403
     - Ejecuta motor CEP
     - Si es ataque: llama a Modulo de Seguridad (HTTP) y Log de Auditoria (HTTP), retorna 429
     - Si no es ataque: retorna 200 `{valido: true}`
     - IMPORTANTE: el 429 retorna `{"mensaje": "Sesion suspendida temporalmente. Contacta soporte si crees que es un error.", "codigo": "SESION_SUSPENDIDA"}`
   - Endpoint `GET /health`
   - Endpoint `POST /reset`: limpia ventanas

6. **Implementar `services/modulo_seguridad/main.py`:**
   - Endpoint `POST /bloquear`:
     - Recibe `{actor_id, ip, jwt_token, senales, score}`
     - Revoca JWT (agrega a blacklist en memoria + MongoDB)
     - Bloquea IP (agrega a blocklist con TTL de 24h)
     - Log de la accion
   - Endpoint `GET /blocklist`: retorna IPs bloqueadas
   - Endpoint `GET /token-revocado/{jwt_token}`: verifica si token esta revocado
   - Endpoint `POST /reset`: limpia blocklist y tokens revocados

7. **Implementar `services/log_auditoria/main.py`:**
   - Endpoint `POST /registrar`:
     - Recibe evento completo (actor_id, ip, senales, payload, traza CEP)
     - Persiste en MongoDB coleccion `auditoria` (independiente de modulo_seguridad)
   - Endpoint `GET /eventos/{actor_id}`: consulta eventos de un actor
   - Endpoint `GET /health`

8. **Crear Dockerfiles** para los 3 servicios.

9. **Crear manifiestos Kubernetes** para los 3 servicios.

10. **Verificar:**
    - Enviar 3 ordenes de un actor con SKUs variados -> 200 (no bloqueado)
    - Enviar 10 ordenes del mismo actor, mismo SKU -> eventualmente 429
    - Verificar que el cuerpo del 429 no expone criterios
    - Verificar evento en Log de Auditoria en MongoDB

## Notas de Arquitectura

| Elemento | Decision | Razonamiento |
|---|---|---|
| Ventana deslizante en memoria | deque por actor_id, no en MongoDB | Latencia < 1 ms para lookup; MongoDB solo para cancelaciones historicas y auditoria |
| 3 senales independientes | Cada senal se evalua por separado | Facilita ajustar umbrales individualmente; motor de correlacion combina resultados |
| Umbral >= 2 senales | Definido en la arquitectura del CCP | Reduce falsos positivos: 1 sola senal (ej. rate alto) no es suficiente para bloquear |
| Respuesta 429 enmascarada | Mensaje generico sin criterios | Atacante no puede inferir que senales disparo; cumple convencion de CLAUDE.md |
| Log de Auditoria independiente | Servicio separado del Modulo de Seguridad | Persistencia forense garantizada incluso si Modulo de Seguridad falla |
| JWT blacklist en memoria + MongoDB | Dual storage | Memoria para performance en runtime; MongoDB para persistencia entre reinicios |
