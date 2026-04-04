# CCP — Experimento de Disponibilidad y Seguridad

Proyecto académico de maestría en arquitectura de software.

Implementa y ejecuta un experimento controlado sobre un **Centro de Control de Pedidos (CCP)** para validar empíricamente dos Atributos de Calidad arquitectónicamente significativos (ASR):

| ASR | Atributo | Hipótesis |
|-----|----------|-----------|
| **ASR-1** | Disponibilidad | El pipeline HeartBeat + VALCOH detecta cualquier inconsistencia de inventario en **< 300 ms** |
| **ASR-2** | Seguridad | El motor CEP detecta un ataque DDoS de capa de negocio en **< 300 ms** |

**Resultado del experimento: 9/9 casos de prueba exitosos. H1 y H2 confirmadas.**

---

## Tabla de contenidos

1. [Arquitectura del sistema](#1-arquitectura-del-sistema)
2. [Estructura del repositorio](#2-estructura-del-repositorio)
3. [Prerrequisitos](#3-prerrequisitos)
4. [Levantar la infraestructura](#4-levantar-la-infraestructura)
5. [Construir y desplegar los servicios](#5-construir-y-desplegar-los-servicios)
6. [Ejecutar el experimento](#6-ejecutar-el-experimento)
7. [Dashboard en tiempo real](#7-dashboard-en-tiempo-real)
8. [Resultados obtenidos](#8-resultados-obtenidos)
9. [Referencia de endpoints](#9-referencia-de-endpoints)
10. [Solución de problemas](#10-solución-de-problemas)

---

## 1. Arquitectura del sistema

### Componentes

```
┌─────────────────────────────────────────────────────────────┐
│                    Namespace: ccp                           │
│                                                             │
│  ┌──────────────────┐      HeartBeat (NATS)                 │
│  │ ModuloInventarios│ ──────────────────────► ┌──────────┐ │
│  │   + VALCOH       │                         │ Monitor  │ │
│  │   (primary)      │                         └────┬─────┘ │
│  └──────────────────┘                              │route  │
│  ┌──────────────────┐                         ┌────▼─────┐ │
│  │   INV-Standby    │ ◄── /activar ───────────│Corrector │ │
│  │   (pasivo)       │                         └──────────┘ │
│  └──────────────────┘                                       │
│                                                             │
│  ┌──────────────────┐      /bloquear                        │
│  │  ValidacionCEP   │ ──────────────► ┌────────────────┐   │
│  │  (motor CEP)     │                 │ModuloSeguridad │   │
│  └──────────────────┘                 └───────┬────────┘   │
│                                               │/registrar  │
│                                        ┌──────▼──────┐     │
│                                        │LogAuditoria │     │
│                                        └─────────────┘     │
└─────────────────────────────────────────────────────────────┘

┌──────────────────────┐   ┌────────────────────────────────┐
│   Namespace: data    │   │   Namespace: messaging         │
│  MongoDB RS          │   │   NATS JetStream               │
│  mongodb-0 (PRIMARY) │   │   Streams:                     │
│  mongodb-1 (SECONDARY│   │   · HEARTBEAT_INVENTARIO       │
└──────────────────────┘   │   · CORRECCION                 │
                           │   · FAILOVER                   │
                           └────────────────────────────────┘
```

### Tácticas arquitectónicas implementadas

**Disponibilidad (ASR-1)**
- **HeartBeat expandido**: el `ModuloInventarios` publica cada 5 s en uno de 5 topics NATS clasificados según el resultado del self-test (`ok`, `stock_negativo`, `divergencia_reservas`, `estado_concurrente`, `self_test_failed`).
- **VALCOH** (Validación de Coherencia): 3 checks ejecutados en memoria antes de cada HeartBeat: (1) `stock >= 0`, (2) `suma(reservas_activas) == stock_inicial - stock`, (3) reservas huérfanas > 10 min.
- **Monitor-Corrector**: el Monitor se suscribe a NATS y enruta cada HeartBeat al endpoint correcto del Corrector (`/corregir`, `/reconciliar`, `/failover`).
- **Redundancia pasiva**: `INV-Standby` corre en el nodo secundario con `STANDBY_MODE=true` (sin HeartBeat). El Corrector lo activa vía `POST /activar` cuando detecta `SELF_TEST_FAILED`.

**Seguridad (ASR-2)**
- **CEP con ventana deslizante**: el `ValidacionCEP` evalúa 3 señales en cada request dentro de una ventana de 60 s: (1) tasa > 10 req, (2) concentración SKU > 80%, (3) tasa de cancelaciones > 50%. Si ≥ 2 señales activas → ataque confirmado.
- **Respuesta enmascarada**: el motor devuelve HTTP 429 con mensaje genérico ("Servicio temporalmente no disponible") sin exponer criterios internos.
- **Revocación de acceso**: `ModuloSeguridad` revoca el JWT del actor y lo bloquea por 24 h.
- **Log de auditoría independiente**: `LogAuditoria` persiste en MongoDB de forma independiente del flujo principal para garantizar trazabilidad forense.

---

## 2. Estructura del repositorio

```
.
├── ASR_escenario*.md          # Documentación de escenarios (diagramas Mermaid)
├── infra/                     # Infraestructura Kubernetes local
│   ├── kind-config.yaml       # Cluster Kind (1 control-plane + 2 workers)
│   ├── namespaces.yaml        # Namespaces: ccp, data, messaging
│   ├── mongodb-replicaset.yaml# MongoDB Replica Set StatefulSet (mongo:7.0)
│   ├── mongodb-rs-init-job.yaml# Job de inicialización del RS y seed de datos
│   ├── nats-values.yaml       # Helm values para NATS JetStream
│   ├── setup.sh               # Script maestro de setup de infraestructura
│   └── verify.sh              # Script de verificación de infraestructura
├── k8s/                       # Manifiestos Kubernetes de servicios
│   ├── modulo-inventarios.yaml
│   ├── inv-standby.yaml
│   ├── inv-standby-svc.yaml
│   ├── monitor.yaml
│   ├── corrector.yaml
│   ├── validacion-cep.yaml
│   ├── modulo-seguridad.yaml
│   └── log-auditoria.yaml
├── services/                  # Código fuente de microservicios (Python/FastAPI)
│   ├── modulo_inventarios/    # HeartBeat + VALCOH + reservas
│   ├── monitor/               # Suscriptor NATS → router
│   ├── corrector/             # Acciones correctivas (rollback, reconciliar, failover)
│   ├── validacion_cep/        # Motor CEP con ventana deslizante
│   ├── modulo_seguridad/      # Revocación JWT + bloqueo de actores
│   └── log_auditoria/         # Persistencia forense en MongoDB
├── experiments/
│   ├── experiment_a/          # CP-A1 a CP-A5 (ASR-1 Disponibilidad)
│   └── experiment_b/          # CP-B1 a CP-B4 (ASR-2 Seguridad)
└── scripts/
    ├── run_experiments.sh     # Orquestador principal (port-forwards + A + B + reporte)
    ├── validate_asrs.py       # Genera reporte final a partir de los JSON de resultados
    ├── live_dashboard.py      # Dashboard en tiempo real (terminal)
    └── final_report.json      # Último reporte generado
```

---

## 3. Prerrequisitos

| Herramienta | Versión mínima | Instalación |
|-------------|---------------|-------------|
| Docker Desktop | 4.x | https://www.docker.com/products/docker-desktop |
| Kind | 0.20+ | `brew install kind` |
| kubectl | 1.28+ | `brew install kubectl` |
| Helm | 3.x | `brew install helm` |
| Python | 3.11+ | `brew install python@3.11` |
| NATS CLI | 0.1+ | `brew install nats-io/nats-tools/nats` |

Dependencias Python (solo para los scripts del host):

```bash
pip3 install httpx
```

> **Nota**: Docker Desktop debe estar corriendo antes de ejecutar cualquier paso.

---

## 4. Levantar la infraestructura

Todo el setup de infraestructura se puede ejecutar con un solo script:

```bash
bash infra/setup.sh
```

O paso a paso:

### 4.1 Crear el cluster Kind

```bash
kind create cluster --name ccp-experiment --config infra/kind-config.yaml
```

Esto crea un cluster con:
- 1 nodo `control-plane` (con port mapping 8080 y 4222 al host)
- 1 nodo `worker` con label `node-role=primary`
- 1 nodo `worker` con label `node-role=standby`

### 4.2 Crear namespaces

```bash
kubectl apply -f infra/namespaces.yaml
```

Crea los namespaces `ccp`, `data` y `messaging`.

### 4.3 Desplegar NATS JetStream

```bash
helm repo add nats https://nats-io.github.io/k8s/helm/charts/
helm repo update
helm install nats nats/nats -n messaging -f infra/nats-values.yaml
```

Espera a que el pod esté `Running`:

```bash
kubectl get pods -n messaging -w
```

### 4.4 Crear streams NATS

Una vez que NATS esté corriendo, crea los 3 streams del experimento:

```bash
# Port-forward temporal
kubectl port-forward svc/nats -n messaging 4222:4222 &
sleep 2

# Crear streams
nats stream add HEARTBEAT_INVENTARIO \
  --subjects "heartbeat.inventario.>" \
  --storage file --retention limits \
  --max-msgs 10000 --max-age 1h \
  --replicas 1 --server nats://localhost:4222

nats stream add CORRECCION \
  --subjects "correccion.>" \
  --storage file --retention limits \
  --max-msgs 10000 --max-age 1h \
  --replicas 1 --server nats://localhost:4222

nats stream add FAILOVER \
  --subjects "failover.>" \
  --storage file --retention limits \
  --max-msgs 1000 --max-age 24h \
  --replicas 1 --server nats://localhost:4222

kill %1  # cerrar port-forward
```

### 4.5 Desplegar MongoDB Replica Set

```bash
kubectl apply -f infra/mongodb-replicaset.yaml
```

Espera a que ambos pods estén `Running` y el job de init haya completado:

```bash
kubectl get pods -n data -w
# Esperar: mongodb-0 Running, mongodb-1 Running, mongodb-rs-init-* Completed
```

El job de inicialización:
- Configura el replica set `rs0` (mongodb-0 como PRIMARY, mongodb-1 como SECONDARY)
- Crea la base de datos `ccp` con la colección `inventario` y seed inicial:
  - `COCA-COLA-350`: stock=9
  - `AGUA-500`: stock=100
  - `ARROZ-1KG`: stock=50

### 4.6 Verificar infraestructura

```bash
bash infra/verify.sh
```

Salida esperada:
```
============================================================
 CCP Experiment — Verificacion de Infraestructura
============================================================

--- Cluster Kind ---
  ✅  Cluster 'ccp-experiment' existe
  ✅  3 nodos Ready
  ✅  worker-node-2 tiene label role=standby

--- Namespaces ---
  ✅  Namespace 'ccp' existe
  ✅  Namespace 'data' existe
  ✅  Namespace 'messaging' existe

--- MongoDB Replica Set ---
  ✅  mongodb-0 Running en data
  ✅  mongodb-1 Running en data

--- Streams NATS ---
  ✅  Stream HEARTBEAT_INVENTARIO
  ✅  Stream CORRECCION
  ✅  Stream FAILOVER
============================================================
 Resultado: 9 pasaron, 0 fallaron
============================================================
```

---

## 5. Construir y desplegar los servicios

### 5.1 Construir imágenes Docker

```bash
docker build -t ccp/modulo-inventarios:latest services/modulo_inventarios/
docker build -t ccp/monitor:latest             services/monitor/
docker build -t ccp/corrector:latest           services/corrector/
docker build -t ccp/validacion-cep:latest      services/validacion_cep/
docker build -t ccp/modulo-seguridad:latest    services/modulo_seguridad/
docker build -t ccp/log-auditoria:latest       services/log_auditoria/
```

O en paralelo:

```bash
for svc in modulo_inventarios monitor corrector validacion_cep modulo_seguridad log_auditoria; do
  img=$(echo $svc | tr '_' '-')
  docker build -t ccp/$img:latest services/$svc/ &
done
wait && echo "Todos los builds completados"
```

### 5.2 Cargar imágenes al cluster Kind

Kind no accede al registry local de Docker; las imágenes deben cargarse explícitamente:

```bash
kind load docker-image ccp/modulo-inventarios:latest --name ccp-experiment
kind load docker-image ccp/monitor:latest             --name ccp-experiment
kind load docker-image ccp/corrector:latest           --name ccp-experiment
kind load docker-image ccp/validacion-cep:latest      --name ccp-experiment
kind load docker-image ccp/modulo-seguridad:latest    --name ccp-experiment
kind load docker-image ccp/log-auditoria:latest       --name ccp-experiment
```

### 5.3 Desplegar en Kubernetes

```bash
kubectl apply -f k8s/modulo-inventarios.yaml
kubectl apply -f k8s/inv-standby.yaml
kubectl apply -f k8s/inv-standby-svc.yaml
kubectl apply -f k8s/monitor.yaml
kubectl apply -f k8s/corrector.yaml
kubectl apply -f k8s/validacion-cep.yaml
kubectl apply -f k8s/modulo-seguridad.yaml
kubectl apply -f k8s/log-auditoria.yaml
```

### 5.4 Verificar pods

```bash
kubectl get pods -n ccp
```

Todos los pods deben estar `1/1 Running`:

```
NAME                                          READY   STATUS    RESTARTS   AGE
corrector-xxx                                 1/1     Running   0          1m
log-auditoria-xxx                             1/1     Running   0          1m
modulo-inventarios-xxx                        1/1     Running   0          1m
modulo-inventarios-standby-xxx                1/1     Running   0          1m
modulo-seguridad-xxx                          1/1     Running   0          1m
monitor-xxx                                   1/1     Running   0          1m
validacion-cep-xxx                            1/1     Running   0          1m
```

> **Nota sobre el Monitor**: usa un consumer durable de NATS. Si el pod se reinicia mientras otro ya está conectado, el nuevo pod no podrá suscribirse hasta que el pod viejo libere el consumer. En ese caso, forzar la eliminación del pod viejo:
> ```bash
> kubectl delete pod -n ccp <nombre-pod-viejo> --force --grace-period=0
> ```

---

## 6. Ejecutar el experimento

### Opción A — Script completo (recomendado)

Ejecuta ambos experimentos en secuencia, genera el reporte final:

```bash
bash scripts/run_experiments.sh
```

El script:
1. Levanta port-forwards para todos los servicios
2. Ejecuta Experimento A (CP-A1 a CP-A5)
3. Ejecuta Experimento B (CP-B1 a CP-B4)
4. Genera `scripts/final_report.json`
5. Cierra los port-forwards al salir

Duración aproximada: **3-4 minutos** (incluye esperas de HeartBeat y envíos espaciados).

### Opción B — Experimentos por separado

```bash
# Primero abrir port-forwards en background
kubectl port-forward -n ccp svc/modulo-inventarios 30090:8090 &>/dev/null &
kubectl port-forward -n ccp svc/monitor            30091:8091 &>/dev/null &
kubectl port-forward -n ccp svc/corrector          30092:8092 &>/dev/null &
kubectl port-forward -n ccp svc/modulo-seguridad   30093:8093 &>/dev/null &
kubectl port-forward -n ccp svc/validacion-cep     30094:8094 &>/dev/null &
kubectl port-forward -n ccp svc/log-auditoria      30096:8096 &>/dev/null &
sleep 3

# Experimento A — ASR-1 Disponibilidad
python3 experiments/experiment_a/run_experiment_a.py

# Experimento B — ASR-2 Seguridad
python3 experiments/experiment_b/run_experiment_b.py

# Reporte final (solo lectura de resultados, no re-ejecuta)
python3 scripts/validate_asrs.py
```

### Casos de prueba

#### Experimento A — ASR-1 Disponibilidad

| Caso | Escenario | Mecanismo | Criterio de aceptación |
|------|-----------|-----------|----------------------|
| **CP-A1** | Happy path — inventario consistente | VALCOH check normal | `t_self_test < 300ms` y HeartBeat tipo `SELF_TEST_OK` |
| **CP-A2** | Stock negativo inyectado | VALCOH Check 1: `stock < 0` | `t_self_test < 300ms` y Corrector ejecuta `/corregir` |
| **CP-A3** | Estado concurrente simulado | Locking optimista en versión | `t_self_test < 300ms` y HeartBeat tipo `ESTADO_CONCURRENTE` |
| **CP-A4** | Divergencia de reservas simulada | VALCOH Check 2: `suma != delta` | `t_self_test < 300ms` y HeartBeat tipo `DIVERGENCIA_RESERVAS` |
| **CP-A5** | Fallo estructural → failover | VALCOH forzado a `SELF_TEST_FAILED` | Monitor activa Corrector `/failover` → INV-Standby activado |

#### Experimento B — ASR-2 Seguridad

| Caso | Escenario | Mecanismo | Criterio de aceptación |
|------|-----------|-----------|----------------------|
| **CP-B1** | Happy path — tráfico normal | 5 requests en 30 s | Sin falsos positivos (0 respuestas 429) |
| **CP-B2** | DDoS gradual — 15 requests rápidos | Rate > 10 + concentración SKU > 80% | HTTP 429 a partir del req 11 y `t_deteccion < 300ms` |
| **CP-B3** | DDoS con JWT válido | JWT válido no exime del análisis CEP | HTTP 429 igualmente y `jwt_bypassed = false` |
| **CP-B4** | Umbral de correlación exacto | ≥ 2 señales = ataque; 1 señal = no ataque | 12 req → 429; 9 req → 200 |

### Limpiar el estado entre ejecuciones

Si necesitas re-ejecutar los experimentos con estado limpio:

```bash
# Puerto forward activo
kubectl port-forward -n ccp svc/modulo-inventarios 30090:8090 &
sleep 2

# Resetear inventario (stocks a valores iniciales)
curl -s -X POST http://localhost:30090/reset

# Limpiar fault injection si quedó activo
curl -s -X POST http://localhost:30090/fault-inject \
     -H "Content-Type: application/json" \
     -d '{"tipo": "none"}'

# Limpiar ventana CEP
kubectl port-forward -n ccp svc/validacion-cep 30094:8094 &
sleep 1
curl -s -X POST http://localhost:30094/reset
```

---

## 7. Dashboard en tiempo real

El dashboard muestra el estado del sistema en vivo desde la terminal, actualizándose cada 2 segundos.

### Modo monitoreo

```bash
python3 scripts/live_dashboard.py
```

Muestra:
- **HeartBeat**: tipo actual y `t_self_test_ms`
- **Monitor**: último tipo enrutado y `t_clasificacion_ms`
- **Contadores**: HeartBeats OK/ERROR, correcciones, failovers
- **CEP**: tamaño de ventana, señales activas, ataques detectados
- **Feed de eventos** en tiempo real con colores por severidad

### Modo demo (secuencia automática de fallas)

```bash
python3 scripts/live_dashboard.py --demo
```

Inyecta automáticamente la siguiente secuencia, observable en tiempo real:

```
1. Estado normal        (10s) → SELF_TEST_OK continuo
2. Stock negativo       (12s) → VALCOH detecta → Corrector corrige
3. Estado normal        (8s)  → recuperado
4. Divergencia reservas (12s) → VALCOH detecta → Corrector reconcilia
5. Estado normal        (8s)  → recuperado
6. Estado concurrente   (10s) → VALCOH detecta
7. Estado normal        (8s)  → recuperado
8. Fallo estructural    (12s) → Monitor activa FAILOVER → Standby activado
9. Estado normal        (10s) → sistema estable
```

### Si los port-forwards ya están activos

```bash
python3 scripts/live_dashboard.py --no-portforward
```

---

## 8. Resultados obtenidos

Ejecución del 4 de abril de 2026. Umbral ASR: 300 ms.

### ASR-1 — Disponibilidad

| Caso | Resultado | `t_self_test` | `t_clasificacion` | Total |
|------|-----------|--------------|------------------|-------|
| CP-A1 Happy path | ✅ PASS | 4.0 ms | 0.2 ms | **4.2 ms** |
| CP-A2 Stock negativo | ✅ PASS | 23.9 ms | — | **23.9 ms** |
| CP-A3 Concurrencia | ✅ PASS | < 1 ms | — | **< 1 ms** |
| CP-A4 Divergencia reservas | ✅ PASS | < 1 ms | — | **< 1 ms** |
| CP-A5 Failover | ✅ PASS | — | — | Failover activado |

**Hipótesis H1: CONFIRMADA** — todos los tiempos de detección están entre **0 y 24 ms**, 10-300× por debajo del umbral de 300 ms.

### ASR-2 — Seguridad

| Caso | Resultado | `t_deteccion` | Detalle |
|------|-----------|--------------|---------|
| CP-B1 Happy path | ✅ PASS | — | 0 falsos positivos |
| CP-B2 DDoS gradual | ✅ PASS | **0.011 ms** | 429 desde req 11/15 |
| CP-B3 DDoS con JWT válido | ✅ PASS | **0.009 ms** | JWT no bypasea CEP |
| CP-B4 Umbral correlación | ✅ PASS | **0.019 ms** | 12 req→ataque; 9 req→ok |

**Hipótesis H2: CONFIRMADA** — el motor CEP detecta ataques en **< 0.1 ms**, 3000× por debajo del umbral de 300 ms.

Los resultados completos están en [`scripts/final_report.json`](scripts/final_report.json).

---

## 9. Referencia de endpoints

### ModuloInventarios (`:8090`)

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/health` | Estado del servicio (`primary` o `standby`) |
| GET | `/inventario/{sku}` | Consultar stock de un SKU |
| POST | `/reservar` | Crear reserva (locking optimista por `version`) |
| POST | `/reset` | Resetear inventario a valores iniciales |
| POST | `/fault-inject` | Inyectar falla: `stock_negativo`, `divergencia_reservas`, `estado_concurrente`, `self_test_failed`, `none` |

### ValidacionCEP (`:8094`)

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| POST | `/validar` | Evaluar request: `{actor_id, sku, accion, jwt_valido}` → 200 OK o 429 |
| GET | `/stats` | Tamaño de ventana, ataques detectados, últimas señales |
| POST | `/reset` | Limpiar ventana deslizante (aislamiento entre tests) |
| GET | `/health` | Estado del servicio |

### Monitor (`:8091`)

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/health` | Estado |
| GET | `/stats` | Conteo de mensajes procesados por tipo |

### Corrector (`:8092`)

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| POST | `/corregir` | Rollback de stock negativo (recibe HeartBeatPayload) |
| POST | `/reconciliar` | Reconciliación de reservas divergentes |
| POST | `/failover` | Activar INV-Standby |
| GET | `/stats` | Conteo de acciones tomadas |

### ModuloSeguridad (`:8093`)

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| POST | `/bloquear` | Revocar JWT y bloquear actor por 24 h |
| GET | `/verificar/{actor_id}` | Verificar si un actor está bloqueado |
| POST | `/desbloquear/{actor_id}` | Desbloquear (para tests) |
| GET | `/stats` | Total bloqueados y tokens revocados |

### LogAuditoria (`:8096`)

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| POST | `/registrar` | Persistir evento de auditoría en MongoDB |
| GET | `/eventos` | Listar eventos (`?actor_id=X&limit=50`) |
| GET | `/health` | Estado |

---

## 10. Solución de problemas

### El cluster Kind no existe

```bash
kind create cluster --name ccp-experiment --config infra/kind-config.yaml
```

### MongoDB no arranca o el RS no se inicializa

```bash
# Ver logs del job de init
kubectl logs -n data job/mongodb-rs-init

# Si el job expiró, eliminarlo y volver a aplicar
kubectl delete job mongodb-rs-init -n data
kubectl apply -f infra/mongodb-rs-init-job.yaml
```

### El Monitor no arranca (consumer NATS ya existe)

Ocurre cuando hay dos pods del Monitor intentando usar el mismo consumer durable:

```bash
# Identificar el pod viejo
kubectl get pods -n ccp | grep monitor

# Eliminar el pod viejo forzosamente
kubectl delete pod -n ccp <nombre-pod-viejo> --force --grace-period=0
```

### Las imágenes no se encuentran en Kind (`ErrImageNeverPull`)

Las imágenes deben cargarse al cluster después de cada `docker build`:

```bash
kind load docker-image ccp/<servicio>:latest --name ccp-experiment
kubectl rollout restart deployment/<servicio> -n ccp
```

### Los port-forwards no responden

```bash
# Verificar que los pods estén Running
kubectl get pods -n ccp

# Reiniciar los port-forwards manualmente
pkill -f "kubectl port-forward"
sleep 2
kubectl port-forward -n ccp svc/modulo-inventarios 30090:8090 &
# ... resto de servicios
```

### Resetear el experimento completamente

```bash
# Eliminar el cluster y empezar desde cero
kind delete cluster --name ccp-experiment

# Recrear todo
kind create cluster --name ccp-experiment --config infra/kind-config.yaml
bash infra/setup.sh
```
