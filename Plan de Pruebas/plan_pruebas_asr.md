# Plan de Pruebas — ASR Disponibilidad y Seguridad
## CCP · Reto 2

---

## Experimento 1 — Detección de stock negativo via HeartBeat (ASR-2)

### Título
**Experimento de Disponibilidad:** Verificación del mecanismo HeartBeat para detectar y corregir una reserva de inventario con stock negativo en menos de 300 ms.

---

### Propósito
Demostrar que el sistema detecta automáticamente una reserva inválida (stock queda en negativo tras la operación) y ejecuta un rollback coordinado antes de que el tendero reciba una confirmación errónea, cumpliendo la restricción de latencia de detección de **< 300 ms** establecida en el ASR-2.

El experimento valida tres tácticas de disponibilidad de forma encadenada:
1. **Detectar (HeartBeat):** el Módulo de Inventarios emite un webhook al Monitor cuando detecta stock negativo internamente.
2. **Corregir (Rollback coordinado):** el Corrector revierte la reserva en Inventarios y cancela el pedido en Gestor de Pedidos en paralelo.
3. **Enmascarar (Respuesta controlada):** el tendero recibe un mensaje accionable con la cantidad real disponible, sin exponer trazas internas.

---

### Diseño del Experimento

| Elemento | Definición |
|---|---|
| **Variable independiente** | Condición detonante: `cantidad_solicitada > stock_disponible` (stock fijo = 9, orden = 10 unidades) |
| **Variable dependiente** | Δt_detección = `T_monitor_recibe_webhook` − `T_reserva_negativa_registrada` (en ms) |
| **Variables controladas** | 1 réplica por servicio · MongoDB standalone · sin carga concurrente adicional · stock inicial fijo (9 u.) · nodo único minikube · misma máquina ejecutora en todas las iteraciones |
| **Número de repeticiones** | **n = 30** iteraciones independientes — umbral mínimo para aplicar el Teorema Central del Límite y asumir distribución aproximadamente normal |
| **Métricas a reportar** | Media (x̄) · Mediana (p50) · Percentil 95 (p95) · Percentil 99 (p99) · Desviación estándar (σ) · Tasa de cumplimiento (% iteraciones con Δt < 300 ms) |
| **Criterio de aceptación** | **p95 < 300 ms** con n = 30; rollback exitoso (stock = valor_inicial) en el **100 %** de las iteraciones |
| **Tipo de experimento** | Caja negra controlada — se estimulan las entradas y se miden las salidas sin modificar los componentes internos durante la ejecución |

---

### Protocolo de Medición

#### Puntos de captura de timestamps

| ID | Componente | Campo en log JSON | Descripción |
|---|---|---|---|
| **T₀** | Módulo de Inventarios | `epoch_ms_reserva_negativa` | Instante exacto en que el stock queda registrado como negativo en MongoDB |
| **T₁** | Monitor | `epoch_ms_heartbeat_recibido` | Instante en que el Monitor recibe y procesa el webhook HeartBeat |
| **T₂** | Corrector | `epoch_ms_rollback_completo` | Instante en que ambas operaciones de rollback confirman éxito |

**Δt_detección** = T₁ − T₀ → métrica principal (SLA: < 300 ms)
**Δt_rollback** = T₂ − T₁ → métrica secundaria (SLA: < 500 ms adicionales)

#### Formato de log estructurado requerido

Cada servicio involucrado debe emitir una línea JSON por evento con estos campos mínimos:

```json
{
  "servicio": "modulo-inventarios",
  "evento": "stock_negativo_detectado",
  "epoch_ms": 1718000000123,
  "orden_id": "ord-uuid-001",
  "sku": "COCA-COLA-350",
  "stock_resultante": -1,
  "iteracion": 1
}
```

#### Exportación y cálculo de estadísticas

```bash
# Extraer T₀ y T₁ desde los logs de cada servicio
kubectl logs -n business deployment/modulo-inventarios \
  | grep '"evento":"stock_negativo_detectado"' \
  | jq -r '[.iteracion, .epoch_ms, .orden_id] | @csv' > t0.csv

kubectl logs -n availability deployment/monitor \
  | grep '"evento":"heartbeat_recibido"' \
  | jq -r '[.iteracion, .epoch_ms, .orden_id] | @csv' > t1.csv
```

```python
import pandas as pd

t0 = pd.read_csv('t0.csv', names=['iter', 'epoch_ms', 'orden_id'])
t1 = pd.read_csv('t1.csv', names=['iter', 'epoch_ms', 'orden_id'])
df = t0.merge(t1, on='orden_id', suffixes=('_t0', '_t1'))
df['delta_ms'] = df['epoch_ms_t1'] - df['epoch_ms_t0']

print(df['delta_ms'].describe(percentiles=[.50, .95, .99]))
print(f"Cumplimiento p95 < 300 ms: {(df['delta_ms'] < 300).mean()*100:.1f}%")
```

---

### Protocolo de Ejecución — 30 iteraciones

Ejecutar el siguiente ciclo **30 veces** de forma consecutiva. Cada iteración debe ser independiente: estado reseteado, logs limpios.

```
Para i = 1 hasta 30:
  1. RESET   → kubectl exec mongo -- mongosh: db.inventario.updateOne({sku:"COCA-COLA-350"}, {$set:{stock:9}})
  2. RESET   → kubectl exec mongo -- mongosh: db.pedidos.deleteMany({orden_id:/test/})
  3. MARCAR  → Anotar número de iteración (campo "iteracion": i) en variable de entorno del script
  4. ENVIAR  → curl -X POST /ordenes -d '{"sku":"COCA-COLA-350","cantidad":10,"iteracion":<i>}'
  5. ESPERAR → sleep 1s (ventana para que el rollback complete)
  6. VERIFICAR → kubectl exec mongo: db.inventario.findOne({sku:"COCA-COLA-350"}).stock == 9
  7. VERIFICAR → kubectl exec mongo: db.pedidos.findOne({orden_id:<id>}).estado == "cancelado"
  8. COOLDOWN → sleep 2s antes de la siguiente iteración
```

> Automatizar los pasos 1–8 en un script `bash` o `Python` para garantizar reproducibilidad y eliminar variabilidad manual.

---

### Resultados esperados

| # | Verificación | Criterio de éxito |
|---|---|---|
| 1 | El Módulo de Inventarios emite el webhook HeartBeat al detectar stock negativo | El webhook llega al Monitor en **< 300 ms** desde que la reserva queda registrada |
| 2 | El Monitor correlaciona el SKU afectado con la orden en vuelo | El Monitor consulta al Gestor de Órdenes y obtiene el `orden_id` correcto |
| 3 | El Corrector ejecuta el rollback en paralelo | La reserva en Inventarios se revierte **y** el pedido en Gestor de Pedidos se cancela; ambas operaciones completan en < 500 ms adicionales al paso 1 |
| 4 | El stock regresa al valor correcto | `stock(SKU) == stock_inicial` después del rollback |
| 5 | El tendero recibe el mensaje accionable | La notificación incluye la cantidad disponible real y **no** expone mensajes de error internos |
| 6 | No se genera una segunda reserva negativa del mismo SKU durante el rollback | El stock nunca baja de cero después de que el Corrector completa |

**Escenario de entrada:**
- Stock inicial del SKU `COCA-COLA-350`: **9 unidades**
- Orden del tendero: **10 unidades** del mismo SKU
- Resultado esperado post-reserva: stock = -1 (condición que dispara el HeartBeat)
- Resultado esperado post-rollback: stock = 9

---

### Recursos requeridos

#### Infraestructura
| Recurso | Especificación |
|---|---|
| Clúster Kubernetes local | minikube o k3d con al menos 4 GB RAM disponibles |
| Namespaces activos | `business`, `availability`, `notifications`, `data` |

#### Servicios desplegados
| Servicio | Namespace | Réplicas mínimas para la prueba |
|---|---|---|
| Gestor de Órdenes | `business` | 1 |
| Módulo de Inventarios | `business` | 1 |
| Gestor de Pedidos | `business` | 1 |
| Monitor | `availability` | 1 |
| Corrector | `availability` | 1 |
| Servicio de Notificaciones | `notifications` | 1 |
| MongoDB | `data` | 1 (standalone para PoC) |

#### Herramientas de prueba
| Herramienta | Propósito |
|---|---|
| `curl` / Postman / k6 | Enviar la orden del tendero al Gestor de Órdenes |
| Logs estructurados (stdout JSON) | Capturar timestamps de cada paso del flujo |
| `kubectl logs -f` | Observar en tiempo real los eventos del Monitor y el Corrector |
| MongoDB shell / Compass | Verificar el estado del stock antes y después del rollback |
| Script de precondición | Poblar MongoDB con stock = 9 para `COCA-COLA-350` antes de cada ejecución |

#### Datos de prueba
```json
{
  "tendero_id": "tendero_001",
  "jwt": "<token_válido>",
  "orden": {
    "sku": "COCA-COLA-350",
    "cantidad": 10
  }
}
```

---

### Elementos de la arquitectura involucrados

```
[Tendero] ──► [Gestor de Órdenes]
                  │
                  ├── REST ──► [Módulo de Inventarios]  ← stock queda en -1
                  │                    │
                  │              webhook HeartBeat (HTTP POST)
                  │                    │
                  │             [Monitor]  ← detecta en < 300 ms
                  │                    │
                  │              REST ──► [Gestor de Órdenes] (consulta orden)
                  │              REST ──► [Gestor de Pedidos] (marca problemática)
                  │                    │
                  │             [Corrector] (rollback paralelo)
                  │             ├── REST ──► [Módulo de Inventarios] (revertir reserva)
                  │             └── REST ──► [Gestor de Pedidos] (cancelar pedido)
                  │
                  └── REST ──► [Notificaciones] ──► [Tendero]
```

**Tácticas ejercidas:** HeartBeat · Rollback coordinado · Respuesta enmascarada

---

### Esfuerzo estimado

| Actividad | Responsable sugerido | Tiempo estimado |
|---|---|---|
| Configurar clúster K8s local y desplegar servicios mínimos | Equipo completo | 4–6 h |
| Desarrollar stubs/mocks de los servicios no implementados | 1–2 personas | 6–8 h |
| Implementar el endpoint de webhook HeartBeat en Monitor | 1 persona | 3–4 h |
| Implementar la lógica de rollback paralelo en Corrector | 1 persona | 4–6 h |
| Instrumentar logs con timestamps para medir latencia | 1 persona | 2–3 h |
| Escribir script de precondición y ejecución del experimento | 1 persona | 2 h |
| Ejecutar el experimento y documentar resultados | Equipo completo | 2–3 h |
| **Total estimado** | | **23–32 h** |

> El rango varía según el nivel de implementación real vs. uso de stubs. Con stubs bien definidos el esfuerzo se concentra en el extremo inferior.

---
---

## Experimento 2 — Detección de DDoS de capa de negocio via CEP (ASR-3)

### Título
**Experimento de Seguridad:** Verificación del analizador CEP para detectar un ataque DDoS semántico (órdenes masivas de un actor para agotar inventario) en menos de 300 ms, bloqueando el actor sin afectar a usuarios legítimos.

---

### Propósito
Demostrar que el componente de Validación de Seguridad, mediante un motor de Procesamiento de Eventos Complejos (CEP) con ventana deslizante de 60 s, identifica el patrón de ataque antes de que cualquier orden llegue al Módulo de Inventarios o al Gestor de Pedidos, cumpliendo el tiempo de respuesta de **< 300 ms** del ASR-3.

El experimento valida cuatro tácticas de seguridad:
1. **Detectar (Analizador CEP):** correlación de ≥ 2 de 3 señales (rate de órdenes, concentración de SKU, tasa de cancelación histórica).
2. **Resistir (Perímetro lógico):** ninguna orden del atacante alcanza Inventarios o Pedidos.
3. **Reaccionar (Revocar acceso):** JWT revocado e IP bloqueada temporalmente (24 h).
4. **Recuperarse (Log de auditoría):** el payload forense se persiste en Object Storage de forma independiente al Módulo de Seguridad.

---

### Diseño del Experimento

| Elemento | Definición |
|---|---|
| **Variable independiente** | Patrón de ataque inyectado: rate de órdenes · concentración de SKU · tasa de cancelación histórica (los tres configurables en el script k6) |
| **Variable dependiente primaria** | Δt_detección = `T_rechazo_VS` − `T_llegada_GO` (en ms) — latencia del CEP |
| **Variable dependiente secundaria** | Tasa de falsos positivos: % de órdenes legítimas concurrentes bloqueadas incorrectamente |
| **Variable dependiente terciaria** | Tasa de penetración: % de órdenes del atacante que llegan al Módulo de Inventarios (esperado: 0 %) |
| **Variables controladas** | Historial de cancelaciones pre-cargado al 89 % · JWT válidos pre-generados · 1 réplica por servicio · sin carga adicional · mismo nodo minikube |
| **Número de repeticiones** | **n = 30** ciclos completos de ataque para la métrica de latencia · **n = 10** ciclos con usuario legítimo concurrente para la métrica de falsos positivos |
| **Métricas a reportar** | Media (x̄) · p50 · p95 · p99 · σ de Δt_detección · Tasa de falsos positivos (%) · Tasa de penetración al inventario (%) · Tasa de publicación a NATS (%) |
| **Criterios de aceptación** | p95 Δt_detección < 300 ms · falsos positivos < 1 % · penetración al inventario = 0 % · publicación NATS = 100 % |
| **Tipo de experimento** | Caja negra con observabilidad de bus de mensajes — se verifica tanto la latencia de rechazo como los efectos secundarios (NATS, JWT, blocklist) |

---

### Protocolo de Medición

#### Puntos de captura de timestamps

| ID | Componente | Campo en log JSON | Descripción |
|---|---|---|---|
| **T₀** | Gestor de Órdenes | `epoch_ms_orden_recibida` | Instante en que GO recibe la orden del atacante y la envía a VS |
| **T₁** | Validación de Seguridad | `epoch_ms_orden_rechazada` | Instante en que VS determina el rechazo y retorna a GO |
| **T_nats** | Validación de Seguridad | `epoch_ms_alerta_publicada` | Instante en que VS publica el evento a NATS `seguridad.alertas` |
| **T_seg** | Módulo de Seguridad | `epoch_ms_jwt_revocado` | Instante en que el JWT del atacante queda inválido |

**Δt_detección** = T₁ − T₀ → métrica principal (SLA: < 300 ms)
**Δt_reacción** = T_seg − T_nats → latencia de respuesta post-detección (referencial)

#### Verificaciones adicionales por iteración

| Verificación | Cómo medirla |
|---|---|
| 0 llamadas al Módulo de Inventarios | `kubectl logs -n business deployment/modulo-inventarios` — contar líneas con `orden_id` del atacante |
| NATS publicó el evento | `nats sub seguridad.alertas` activo durante la prueba; contar mensajes recibidos |
| JWT revocado | Enviar petición con el mismo JWT post-ataque → debe retornar HTTP 401 |
| IP en blocklist | `kubectl exec mongo: db.blocklist.findOne({ip:"190.24.113.45"})` — verificar TTL 24h |
| Usuario legítimo no bloqueado | La orden de `tendero_002` retorna HTTP 200 |

#### Exportación y cálculo de estadísticas

```bash
kubectl logs -n business deployment/gestor-ordenes \
  | grep '"actor_id":"tendero_8821"' \
  | jq -r '[.iteracion, .epoch_ms_orden_recibida, .orden_id] | @csv' > t0_cep.csv

kubectl logs -n security deployment/validacion-seguridad \
  | grep '"evento":"orden_rechazada"' \
  | jq -r '[.iteracion, .epoch_ms_orden_rechazada, .orden_id] | @csv' > t1_cep.csv
```

```python
import pandas as pd

t0 = pd.read_csv('t0_cep.csv', names=['iter', 'epoch_ms', 'orden_id'])
t1 = pd.read_csv('t1_cep.csv', names=['iter', 'epoch_ms', 'orden_id'])
df = t0.merge(t1, on='orden_id', suffixes=('_t0', '_t1'))
df['delta_ms'] = df['epoch_ms_t1'] - df['epoch_ms_t0']

print(df['delta_ms'].describe(percentiles=[.50, .95, .99]))
print(f"Cumplimiento p95 < 300 ms: {(df['delta_ms'] < 300).mean()*100:.1f}%")
```

---

### Protocolo de Ejecución — 30 iteraciones

Ejecutar el siguiente ciclo **30 veces**. Cada iteración resetea el estado del atacante para garantizar independencia.

```
Para i = 1 hasta 30:
  1. RESET   → kubectl exec mongo: db.blocklist.deleteOne({ip:"190.24.113.45"})
  2. RESET   → kubectl exec mongo: db.jwt_revocados.deleteOne({actor_id:"tendero_8821"})
  3. PRELOAD → kubectl exec mongo: db.historial.updateOne(
                 {actor_id:"tendero_8821"}, {$set:{tasa_cancelacion_2h:0.89}})
  4. MARCAR  → Configurar variable "iteracion": i en el script k6
  5. LANZAR  → k6 run --vus 1 --duration 60s ataque_script.js
               (50 órdenes de tendero_8821 + 1 orden de tendero_002 en paralelo)
  6. ESPERAR → Completar los 60s de la ventana CEP + 5s buffer
  7. VERIFICAR → Contar llamadas a Inventarios con orden_id del atacante (esperado: 0)
  8. VERIFICAR → nats sub --count=1 seguridad.alertas (esperado: 1 mensaje publicado)
  9. VERIFICAR → HTTP 401 con JWT del atacante post-bloqueo
 10. VERIFICAR → HTTP 200 con JWT de tendero_002
 11. COOLDOWN → sleep 5s
```

> El script k6 (`ataque_script.js`) debe parametrizar `iteracion` e inyectar el campo en el payload de cada orden para poder correlacionar logs con iteración.

---

### Resultados esperados

| # | Verificación | Criterio de éxito |
|---|---|---|
| 1 | El CEP evalúa las 3 señales dentro de la ventana de 60 s | Las señales se evalúan con cada orden recibida del actor; sin latencia perceptible para el atacante |
| 2 | Con ≥ 2 señales activas, la orden es rechazada antes de llegar a Inventarios | El Módulo de Inventarios **no** registra ninguna llamada proveniente del atacante |
| 3 | La detección ocurre en < 300 ms desde que la orden llega al Gestor de Órdenes | El timestamp del rechazo en VS − timestamp de llegada en GO < 300 ms |
| 4 | NATS publica el evento `seguridad.alertas` con el payload forense completo | El mensaje incluye: `actor_id`, `ip`, `timestamp`, `jwt_token`, `orden`, `señales_activas`, `score_riesgo` |
| 5 | El Módulo de Seguridad revoca el JWT del actor | El JWT del atacante es inválido para solicitudes posteriores (verificable con el API Gateway) |
| 6 | El Módulo de Seguridad bloquea la IP del atacante | La IP figura en la lista de bloqueo temporal con TTL de 24 h |
| 7 | El Log de Auditoría persiste el evento en Object Storage | El archivo existe en MinIO (local) con el payload forense completo |
| 8 | Un usuario legítimo concurrente no es bloqueado | Órdenes de otro `tendero_id` con comportamiento normal completan exitosamente durante el ataque |
| 9 | El atacante recibe únicamente HTTP 429 sin información sobre los criterios del CEP | El cuerpo de la respuesta no contiene referencias a señales, umbrales ni reglas |

**Escenario de ataque:**
- Actor: `tendero_8821` con JWT válido
- IP: `190.24.113.45`
- Patrón: 50 órdenes en 60 s, 46 apuntando a SKU `COCA-COLA-350`, 89 % de cancelaciones históricas
- Señales activadas: rate (señal 1) + concentración SKU (señal 2) + cancelaciones (señal 3) → score 0.97

**Escenario concurrente legítimo:**
- Actor: `tendero_002` con JWT válido diferente
- Patrón: 2 órdenes en 60 s, SKUs variados, 0 % cancelaciones
- Resultado esperado: orden procesada normalmente, sin bloqueo

---

### Recursos requeridos

#### Infraestructura
| Recurso | Especificación |
|---|---|
| Clúster Kubernetes local | minikube o k3d con al menos 6 GB RAM disponibles |
| Namespaces activos | `ingress`, `business`, `security`, `messaging`, `observability`, `data` |

#### Servicios desplegados
| Servicio | Namespace | Réplicas mínimas para la prueba |
|---|---|---|
| API Gateway | `ingress` | 1 |
| Gestor de Órdenes | `business` | 1 |
| Validación de Seguridad (con motor CEP) | `security` | 1 |
| Módulo de Seguridad | `security` | 1 |
| NATS / JetStream | `messaging` | 1 (standalone para PoC) |
| Log de Auditoría | `observability` | 1 |
| MinIO | `data` | 1 |
| MongoDB | `data` | 1 (standalone para PoC) |

> El Módulo de Inventarios y el Gestor de Pedidos deben estar desplegados pero **no deben recibir llamadas durante el ataque** — su ausencia de logs es parte del criterio de éxito.

#### Herramientas de prueba
| Herramienta | Propósito |
|---|---|
| k6 o script Python/bash | Simular ráfaga de 50 órdenes en 60 s desde el actor atacante |
| `curl` / Postman | Enviar orden del tendero legítimo concurrente |
| NATS CLI o `nats sub` | Verificar que el evento `seguridad.alertas` se publica correctamente |
| MinIO Console / `mc` | Verificar que el objeto forense existe en el bucket de auditoría |
| `kubectl logs -f` | Observar en tiempo real los logs de VS, Módulo de Seguridad y Log de Auditoría |
| Script de precondición | Poblar historial de cancelaciones del atacante en MongoDB (89 % en las últimas 2 h) |

#### Datos de prueba — atacante
```json
{
  "actor_id": "tendero_8821",
  "ip": "190.24.113.45",
  "jwt": "<token_válido_atacante>",
  "historial_cancelaciones_2h": 89,
  "patron_ataque": {
    "total_ordenes_60s": 50,
    "ordenes_mismo_sku": 46,
    "sku_objetivo": "COCA-COLA-350",
    "cantidad_por_orden": 500
  }
}
```

#### Datos de prueba — usuario legítimo
```json
{
  "actor_id": "tendero_002",
  "jwt": "<token_válido_legítimo>",
  "orden": {
    "sku": "PEPSI-500",
    "cantidad": 5
  }
}
```

---

### Elementos de la arquitectura involucrados

```
[Atacante (bot)] ──► [API Gateway]  ← JWT válido, rate HTTP dentro del límite
                          │
                     [Gestor de Órdenes]
                          │
                     REST síncrono
                          │
                [Validación de Seguridad]
                          │
              ┌─── Motor CEP (ventana 60 s) ───┐
              │  Señal 1: rate órdenes ⚠        │
              │  Señal 2: concentración SKU ⚠   │
              │  Señal 3: tasa cancelación ⚠    │
              └─────────── ≥ 2 señales ─────────┘
                          │
              BLOQUEADA — nunca llega a:
              [Módulo de Inventarios]   [Gestor de Pedidos]
                          │
              Publica a [NATS · seguridad.alertas]
                    ┌─────┴─────┐
                    ▼           ▼
          [Módulo de       [Log de Auditoría]
           Seguridad]       (→ MinIO / OCI OS)
          · Revoca JWT
          · Bloquea IP
          · Alerta equipo
                    │
              [Gestor de Órdenes] ──► HTTP 429 ──► [Atacante]
```

**Tácticas ejercidas:** CEP · Perímetro lógico · Revocación de acceso · Log de auditoría inmutable · Respuesta genérica

---

### Esfuerzo estimado

| Actividad | Responsable sugerido | Tiempo estimado |
|---|---|---|
| Configurar clúster K8s local con todos los namespaces necesarios | Equipo completo | 4–6 h |
| Desplegar y configurar NATS/JetStream con topic `seguridad.alertas` | 1 persona | 2–3 h |
| Implementar motor CEP con ventana deslizante de 60 s y las 3 señales | 1–2 personas | 8–12 h |
| Integrar Validación de Seguridad con NATS (publicación de alertas) | 1 persona | 3–4 h |
| Implementar Módulo de Seguridad (revocación JWT, lista de bloqueo IP) | 1 persona | 4–6 h |
| Implementar Log de Auditoría (suscripción NATS + escritura a MinIO) | 1 persona | 3–4 h |
| Poblar historial de cancelaciones del atacante en MongoDB | 1 persona | 1–2 h |
| Escribir script de carga (k6 / Python) para simular el ataque | 1 persona | 2–3 h |
| Ejecutar el experimento (ataque + usuario legítimo concurrente) y documentar | Equipo completo | 2–3 h |
| **Total estimado** | | **29–43 h** |

> El componente de mayor esfuerzo es el motor CEP. Si se utiliza una librería de CEP existente (por ejemplo, `esper`, `flink-cep`, o una implementación simple con ventana en Redis), el rango se reduce al extremo inferior.

---

## Resumen comparativo

| Dimensión | Experimento 1 (ASR-2 — HeartBeat) | Experimento 2 (ASR-3 — CEP DDoS) |
|---|---|---|
| **Atributo de calidad** | Disponibilidad | Seguridad |
| **Táctica principal** | Detectar / Corregir | Detectar / Resistir / Reaccionar |
| **Métrica clave** | Δt_detección (p95 < 300 ms) + rollback = 100 % | Δt_detección (p95 < 300 ms) + penetración inventario = 0 % |
| **Variable dependiente** | Latencia webhook HeartBeat | Latencia CEP + tasa falsos positivos + tasa penetración |
| **Repeticiones** | n = 30 | n = 30 (latencia) + n = 10 (falsos positivos) |
| **Complejidad de implementación** | Media | Alta (motor CEP + NATS + múltiples consumidores) |
| **Esfuerzo estimado** | 23–32 h | 29–43 h |
| **Riesgo principal** | Latencia del webhook excede 300 ms en carga real | Falsos positivos del CEP bloqueando usuarios legítimos |
| **Orden de ejecución recomendado** | Primero | Segundo (depende de NATS y MongoDB) |

---

## Ambiente de Ejecución

> Todos los experimentos deben ejecutarse en el mismo ambiente físico para garantizar comparabilidad. Registrar los siguientes datos antes de cada sesión de pruebas.

### Especificación requerida

| Parámetro | Valor mínimo recomendado | Valor a registrar |
|---|---|---|
| **SO y versión** | macOS 13+ / Ubuntu 22.04+ | ____________ |
| **CPU** | 4 núcleos físicos | ____________ |
| **RAM total** | 16 GB | ____________ |
| **RAM asignada a minikube** | 8 GB | ____________ |
| **CPUs asignadas a minikube** | 4 | ____________ |
| **Versión minikube** | v1.32+ | ____________ |
| **Versión kubectl** | v1.29+ | ____________ |
| **Versión k6** | v0.49+ | ____________ |
| **Driver minikube** | docker o hyperkit | ____________ |
| **Otras apps corriendo** | Ninguna de alto consumo | ____________ |

### Consideraciones de red

- Todos los servicios se comunican dentro del clúster (sin latencia de red externa)
- El script de prueba corre **fuera** del clúster y accede vía `minikube service` o `kubectl port-forward`
- Registrar si se usa `port-forward` (agrega ~1–3 ms de overhead) o NodePort directo

---

## Análisis de Resultados

### Tabla de resultados esperada (por experimento)

Al finalizar las n = 30 iteraciones, completar la siguiente tabla:

#### Experimento 1 — Latencia HeartBeat (Δt_detección en ms)

| Métrica | Valor obtenido | Umbral SLA | Cumple |
|---|---|---|---|
| Media (x̄) | ________ ms | — | — |
| Mediana (p50) | ________ ms | — | — |
| Percentil 95 (p95) | ________ ms | **< 300 ms** | ✓ / ✗ |
| Percentil 99 (p99) | ________ ms | — | — |
| Desviación estándar (σ) | ________ ms | — | — |
| Valor mínimo | ________ ms | — | — |
| Valor máximo | ________ ms | — | — |
| Tasa rollback exitoso | ________ % | **100 %** | ✓ / ✗ |
| **Veredicto final** | | | **PASA / FALLA** |

#### Experimento 2 — Latencia CEP y efectividad de bloqueo

| Métrica | Valor obtenido | Umbral SLA | Cumple |
|---|---|---|---|
| Media Δt_detección (x̄) | ________ ms | — | — |
| p95 Δt_detección | ________ ms | **< 300 ms** | ✓ / ✗ |
| p99 Δt_detección | ________ ms | — | — |
| σ Δt_detección | ________ ms | — | — |
| Tasa de penetración a Inventarios | ________ % | **0 %** | ✓ / ✗ |
| Tasa de falsos positivos | ________ % | **< 1 %** | ✓ / ✗ |
| Tasa de publicación a NATS | ________ % | **100 %** | ✓ / ✗ |
| JWT revocado en todas las iteraciones | ________ % | **100 %** | ✓ / ✗ |
| **Veredicto final** | | | **PASA / FALLA** |

---

### Interpretación estadística

#### Regla de decisión
Un experimento **PASA** si y solo si **todos** los criterios de aceptación se cumplen simultáneamente. El incumplimiento de cualquier criterio es motivo de análisis de causa raíz antes de declarar el ASR como validado.

#### Intervalos de confianza (referencia)
Para reportar con mayor rigor estadístico, calcular el intervalo de confianza del 95 % para la media:

```
IC₉₅ = x̄ ± (1.96 × σ / √n)
```

Con n = 30 y σ estimada, el margen de error típico para latencias de red local es ± 5–15 ms.

#### Visualizaciones sugeridas para la presentación
| Gráfico | Métrica que muestra | Herramienta |
|---|---|---|
| Histograma de Δt (30 bins) | Distribución de latencias | Python matplotlib / Excel |
| Box plot | Media, percentiles, outliers | Python seaborn / Excel |
| Línea de tiempo por iteración | Evolución de la latencia a lo largo de las 30 corridas | Python matplotlib |
| Tabla de contingencia | Ataques bloqueados vs. no bloqueados (Exp. 2) | Excel / Google Sheets |

#### Señales de alerta en los resultados
| Patrón observado | Posible causa | Acción |
|---|---|---|
| p99 >> p95 (ej. p99 > 500 ms) | Outliers por GC de la JVM o cold start del pod | Agregar warm-up de 5 iteraciones descartadas |
| σ > 50 ms | Alta variabilidad; ambiente no controlado | Cerrar apps en segundo plano; repetir en ambiente limpio |
| Latencia crece con iteraciones | Memory leak o acumulación de logs en el pod | Reiniciar pods entre bloques de 10 iteraciones |
| Falsos positivos > 0 en Exp. 2 | Umbral del CEP demasiado sensible | Ajustar umbral de score mínimo y re-ejecutar |
