# CCP — Experimento de Disponibilidad y Seguridad

Proyecto académico de maestría en arquitectura de software.

Implementa y ejecuta un experimento controlado sobre un **Centro de Control de Pedidos (CCP)** para validar empíricamente dos Atributos de Calidad arquitectónicamente significativos (ASR):

| ASR | Atributo | Hipótesis |
|-----|----------|-----------|
| **ASR-1** | Disponibilidad | El pipeline HeartBeat + VALCOH detecta cualquier inconsistencia de inventario en **< 300 ms** |
| **ASR-2** | Seguridad | El motor CEP detecta un ataque DDoS de capa de negocio en **< 300 ms** |

**Resultado del experimento: 9/9 casos de prueba exitosos. H1 y H2 confirmadas.**

El experimento puede ejecutarse en **dos entornos**:
- **Local (Kind)** — Kubernetes en Docker, sin dependencias de nube
- **OCI (OKE)** — Oracle Kubernetes Engine en producción real

---

## Tabla de contenidos

1. [Arquitectura del sistema](#1-arquitectura-del-sistema)
2. [Estructura del repositorio](#2-estructura-del-repositorio)
3. [Prerrequisitos](#3-prerrequisitos)
4. [Ejecución local (Kind)](#4-ejecución-local-kind)
5. [Despliegue en OCI (OKE)](#5-despliegue-en-oci-oke)
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
├── ASR_escenario*.md              # Documentación de escenarios (diagramas Mermaid)
├── infra/
│   ├── deploy.sh                  # Orquestador unificado: DEPLOY_TARGET=local|oci
│   ├── setup.sh                   # Setup local: Kind + NATS + MongoDB
│   ├── build-and-load.sh          # docker build + kind load (todos los servicios)
│   ├── verify.sh                  # Verifica 9 condiciones de salud (local)
│   ├── kind-config.yaml           # Cluster Kind (1 control-plane + 2 workers)
│   ├── namespaces.yaml            # Namespaces: ccp, data, messaging
│   ├── mongodb-replicaset.yaml    # MongoDB RS StatefulSet (mongo:7.0)
│   ├── mongodb-rs-init-job.yaml   # Seed de inventario inicial
│   ├── nats-values.yaml           # Helm values NATS (local)
│   └── oci/                       # Scripts exclusivos para OCI/OKE
│       ├── setup_oke.sh           # Provisiona cluster OKE + node pool + kubeconfig
│       ├── teardown_oke.sh        # Elimina todos los recursos OCI
│       ├── ocir_push.sh           # Build + tag + push de 6 imágenes a OCIR
│       ├── imagepullsecret.sh     # Crea ocir-secret en namespaces ccp/data/messaging
│       ├── deploy_services.sh     # Instala Helm + aplica overlay Kustomize
│       ├── verify_oci.sh          # Verifica pods, LB IPs y health checks
│       ├── nats-values-oci.yaml   # Helm values NATS con PVC 5Gi (oci-bv)
│       └── mongodb-values-oci.yaml# Helm values MongoDB RS con PVC 50Gi (oci-bv)
├── k8s/
│   ├── modulo-inventarios.yaml    # NodePort 30090
│   ├── inv-standby.yaml + inv-standby-svc.yaml
│   ├── monitor.yaml               # NodePort 30091
│   ├── corrector.yaml             # NodePort 30092
│   ├── validacion-cep.yaml        # NodePort 30094
│   ├── modulo-seguridad.yaml      # NodePort 30093
│   ├── log-auditoria.yaml         # NodePort 30096
│   └── overlays/oci/              # Kustomize overlay para OKE
│       ├── kustomization.yaml     # Refs OCIR + 3 patches
│       ├── patch-imagepull.yaml   # imagePullSecrets + imagePullPolicy: Always
│       ├── patch-services-lb.yaml # NodePort → LoadBalancer (INV + CEP)
│       └── patch-remove-nodeselector.yaml
├── services/                      # Código fuente (Python 3.11 + FastAPI)
│   ├── modulo_inventarios/        # HeartBeat + VALCOH + reservas
│   ├── monitor/                   # Suscriptor NATS → router
│   ├── corrector/                 # Rollback, reconciliación, failover
│   ├── validacion_cep/            # Motor CEP con ventana deslizante
│   ├── modulo_seguridad/          # Revocación JWT + bloqueo de actores
│   └── log_auditoria/             # Persistencia forense en MongoDB
├── experiments/
│   ├── experiment_a/              # CP-A1 a CP-A5 (ASR-1 Disponibilidad)
│   └── experiment_b/              # CP-B1 a CP-B4 (ASR-2 Seguridad)
└── scripts/
    ├── run_experiments.sh         # Orquestador: port-forwards + A + B + reporte
    ├── validate_asrs.py           # Genera reporte final desde los JSON de resultados
    ├── live_dashboard.py          # Dashboard en tiempo real (terminal)
    └── final_report.json          # Último reporte generado
```

---

## 3. Prerrequisitos

### Comunes (ambos modos)

| Herramienta | Versión mínima | Instalación |
|-------------|---------------|-------------|
| Docker Desktop | 4.x | https://www.docker.com/products/docker-desktop |
| kubectl | 1.28+ | `brew install kubectl` |
| Helm | 3.12+ | `brew install helm` |
| Python | 3.11+ | `brew install python@3.11` |

```bash
pip3 install httpx
```

### Solo para modo local

| Herramienta | Versión mínima | Instalación |
|-------------|---------------|-------------|
| Kind | 0.20+ | `brew install kind` |
| NATS CLI | 0.1+ | `brew install nats-io/nats-tools/nats` |

### Solo para modo OCI

| Herramienta | Versión mínima | Instalación |
|-------------|---------------|-------------|
| OCI CLI | 3.x | `brew install oci-cli` |

Además se requieren las siguientes variables de entorno con credenciales OCI (ver [sección 5](#5-despliegue-en-oci-oke)):

```
OCI_REGION, OCI_TENANCY_NAMESPACE, OCI_COMPARTMENT_ID, OCIR_USERNAME, OCIR_PASSWORD
```

> **Nota**: Docker Desktop debe estar corriendo antes de ejecutar cualquier paso.

---

## 4. Ejecución local (Kind)

Este modo despliega el experimento en un cluster Kubernetes local usando Kind (Kubernetes in Docker). No requiere cuenta de nube.

### 4.1 Setup en un comando

```bash
DEPLOY_TARGET=local bash infra/deploy.sh
```

Este comando ejecuta en secuencia:
1. `bash infra/setup.sh` — crea el cluster Kind, instala NATS y MongoDB, crea los streams
2. `bash infra/build-and-load.sh` — construye las 6 imágenes Docker y las carga en Kind
3. `kubectl apply -f k8s/` — despliega los 7 pods de servicios

Duración aproximada: **5-8 minutos** la primera vez.

### 4.2 Setup paso a paso

#### Crear el cluster Kind

```bash
kind create cluster --name ccp-experiment --config infra/kind-config.yaml
```

Crea un cluster con:
- 1 nodo `control-plane`
- 1 nodo `worker` con label `node-role=primary`
- 1 nodo `worker` con label `node-role=standby`

#### Crear namespaces

```bash
kubectl apply -f infra/namespaces.yaml
```

#### Instalar NATS JetStream

```bash
helm repo add nats https://nats-io.github.io/k8s/helm/charts/
helm repo update
helm install nats nats/nats -n messaging -f infra/nats-values.yaml
kubectl get pods -n messaging -w  # esperar Running
```

#### Crear los streams NATS

```bash
kubectl port-forward svc/nats -n messaging 4222:4222 &
sleep 2

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

kill %1
```

#### Desplegar MongoDB Replica Set

```bash
kubectl apply -f infra/mongodb-replicaset.yaml
kubectl get pods -n data -w
# Esperar: mongodb-0 Running, mongodb-1 Running, mongodb-rs-init-* Completed
```

El job de inicialización configura el replica set `rs0` y crea la colección `inventario` con seed:
- `COCA-COLA-350`: stock=9
- `AGUA-500`: stock=100
- `ARROZ-1KG`: stock=50

#### Construir y cargar imágenes

```bash
bash infra/build-and-load.sh
```

O manualmente:

```bash
for svc in modulo_inventarios monitor corrector validacion_cep modulo_seguridad log_auditoria; do
  img=$(echo $svc | tr '_' '-')
  docker build -t ccp/$img:latest services/$svc/
  kind load docker-image ccp/$img:latest --name ccp-experiment
done
```

#### Desplegar los servicios

```bash
kubectl apply -f k8s/
kubectl get pods -n ccp -w
```

Todos los pods deben estar `1/1 Running`:

```
NAME                               READY   STATUS    RESTARTS   AGE
corrector-xxx                      1/1     Running   0          1m
log-auditoria-xxx                  1/1     Running   0          1m
modulo-inventarios-xxx             1/1     Running   0          1m
modulo-inventarios-standby-xxx     1/1     Running   0          1m
modulo-seguridad-xxx               1/1     Running   0          1m
monitor-xxx                        1/1     Running   0          1m
validacion-cep-xxx                 1/1     Running   0          1m
```

#### Verificar infraestructura

```bash
bash infra/verify.sh
```

Salida esperada:

```
============================================================
 CCP Experiment — Verificacion de Infraestructura
============================================================
  ✅  Cluster 'ccp-experiment' existe
  ✅  3 nodos Ready
  ✅  Namespace 'ccp' existe
  ✅  Namespace 'data' existe
  ✅  Namespace 'messaging' existe
  ✅  mongodb-0 Running en data
  ✅  mongodb-1 Running en data
  ✅  Stream HEARTBEAT_INVENTARIO
  ✅  Stream CORRECCION
  ✅  Stream FAILOVER
============================================================
 Resultado: 9 pasaron, 0 fallaron
============================================================
```

> **macOS + Kind**: los NodePorts **no son accesibles desde el host** cuando Kind corre en Docker Desktop. Todos los scripts (`run_experiments.sh`, `live_dashboard.py`) usan `kubectl port-forward` automáticamente.

---

## 5. Despliegue en OCI (OKE)

Este modo despliega el experimento en un cluster OKE real en Oracle Cloud Infrastructure. Requiere cuenta OCI y credenciales configuradas.

### 5.1 Configurar credenciales OCI

#### Instalar y configurar OCI CLI

```bash
# macOS
brew install oci-cli

# Configurar (genera ~/.oci/config con tenancy OCID, user OCID, API key, region)
oci setup config
```

#### Exportar variables de entorno

```bash
export OCI_REGION="sa-bogota-1"                     # tu región OCI
export OCI_TENANCY_NAMESPACE="<namespace>"           # OCI Console > Tenancy details
export OCI_COMPARTMENT_ID="ocid1.compartment.oc1..<id>"
export OCIR_USERNAME="${OCI_TENANCY_NAMESPACE}/oracleidentitycloudservice/<tu-email>"
export OCIR_PASSWORD="<auth_token>"                 # OCI Console > User > Auth Tokens
```

Para obtener el namespace del tenancy:

```bash
oci os ns get --query 'data' --raw-output
```

Para crear un Auth Token (necesario para OCIR):
1. OCI Console → esquina superior derecha → tu usuario → **Auth Tokens**
2. **Generate Token** → copiar el valor (no se muestra de nuevo)

#### Verificar login a OCIR

```bash
docker login "${OCI_REGION}.ocir.io" \
  -u "${OCIR_USERNAME}" \
  -p "${OCIR_PASSWORD}"
# Expected: Login Succeeded
```

### 5.2 Despliegue en un comando

Con las variables exportadas:

```bash
DEPLOY_TARGET=oci bash infra/deploy.sh
```

Este comando ejecuta en secuencia:
1. `bash infra/oci/setup_oke.sh` — provisiona el cluster OKE y configura kubeconfig
2. `bash infra/oci/ocir_push.sh` — construye las 6 imágenes y las sube a OCIR
3. `bash infra/oci/deploy_services.sh` — instala NATS y MongoDB vía Helm, aplica el overlay Kustomize y espera los rollouts

Duración aproximada: **15-25 minutos** (el cluster OKE tarda ~10 min en estar activo).

### 5.3 Despliegue paso a paso

#### Paso 1 — Provisionar el cluster OKE

```bash
bash infra/oci/setup_oke.sh
```

Este script:
- Crea un cluster OKE llamado `ccp-experiment-oke` (Kubernetes 1.28, endpoint público)
- Crea un node pool `ccp-workers` con 3 nodos `VM.Standard.E4.Flex` (2 OCPUs, 8 GB RAM)
- Descarga el kubeconfig y lo fusiona con `~/.kube/config`

> **Prerequisito**: antes de correr el script debes obtener los IDs de subnet y availability domain de tu tenancy:
> ```bash
> # Ver availability domains disponibles
> oci iam availability-domain list --compartment-id "${OCI_COMPARTMENT_ID}"
>
> # Ver subnets del compartment
> oci network subnet list --compartment-id "${OCI_COMPARTMENT_ID}"
>
> # Ver imágenes de nodo compatibles con OKE
> oci ce node-pool-options get --node-pool-option-id all \
>   --query 'data.sources[?sourceType==`IMAGE`].{id:imageId,name:sourceName}' | head -20
> ```
> Edita `infra/oci/setup_oke.sh` y reemplaza `${ENDPOINT_SUBNET_ID}`, `${LB_SUBNET_ID}`, `${WORKER_SUBNET_ID}`, `${NODE_IMAGE_ID}` y `${AD}` con los valores de tu cuenta.

Verificar que el cluster esté listo:

```bash
kubectl get nodes
# NAME          STATUS   ROLES   AGE   VERSION
# 10.x.x.x     Ready    node    5m    v1.28.x
# 10.x.x.x     Ready    node    5m    v1.28.x
# 10.x.x.x     Ready    node    5m    v1.28.x
```

#### Paso 2 — Subir imágenes a OCIR

```bash
bash infra/oci/ocir_push.sh
```

Este script construye las 6 imágenes Docker, las etiqueta con la ruta OCIR y las sube:

```
<region>.ocir.io/<namespace>/ccp/modulo-inventarios:latest
<region>.ocir.io/<namespace>/ccp/monitor:latest
<region>.ocir.io/<namespace>/ccp/corrector:latest
<region>.ocir.io/<namespace>/ccp/validacion-cep:latest
<region>.ocir.io/<namespace>/ccp/modulo-seguridad:latest
<region>.ocir.io/<namespace>/ccp/log-auditoria:latest
```

Verificar en OCI Console → **Container Registry** → repositorio `ccp/`.

#### Paso 3 — Crear imagePullSecret para OCIR

El secret permite que los pods descarguen imágenes desde OCIR:

```bash
bash infra/oci/imagepullsecret.sh
```

Crea el secret `ocir-secret` en los namespaces `ccp`, `data` y `messaging`.

#### Paso 4 — Instalar NATS JetStream

```bash
helm repo add nats https://nats-io.github.io/k8s/helm/charts/ 2>/dev/null || true
helm repo update

kubectl create namespace messaging --dry-run=client -o yaml | kubectl apply -f -

helm install nats nats/nats \
  -n messaging \
  -f infra/oci/nats-values-oci.yaml \
  --wait --timeout 5m
```

Usa `StorageClass: oci-bv` (OCI Block Volume) con PVC de 5 Gi para persistencia JetStream.

#### Paso 5 — Instalar MongoDB Replica Set

```bash
helm repo add bitnami https://charts.bitnami.com/bitnami 2>/dev/null || true
helm repo update

kubectl create namespace data --dry-run=client -o yaml | kubectl apply -f -

helm install mongodb bitnami/mongodb \
  -n data \
  -f infra/oci/mongodb-values-oci.yaml \
  --wait --timeout 10m
```

Usa `StorageClass: oci-bv` con PVCs de 50 Gi por réplica.

#### Paso 6 — Crear streams NATS

```bash
kubectl port-forward svc/nats -n messaging 4222:4222 &
sleep 3

nats stream add HEARTBEAT_INVENTARIO \
  --subjects "heartbeat.inventario.*" \
  --storage memory --replicas 1 \
  --retention limits --max-msgs 10000 --max-age 1h \
  --server nats://localhost:4222

nats stream add CORRECCION \
  --subjects "correccion.*" \
  --storage memory --replicas 1 \
  --retention limits --max-msgs 10000 --max-age 1h \
  --server nats://localhost:4222

nats stream add FAILOVER \
  --subjects "failover.*" \
  --storage memory --replicas 1 \
  --retention limits --max-msgs 10000 --max-age 1h \
  --server nats://localhost:4222

kill %1
```

#### Paso 7 — Aplicar el overlay Kustomize

```bash
bash infra/oci/deploy_services.sh
```

El overlay en `k8s/overlays/oci/` aplica 3 transformaciones sobre los manifiestos base:
- **Imágenes**: reemplaza `ccp/<servicio>:latest` por `<region>.ocir.io/<namespace>/ccp/<servicio>:latest`
- **imagePullPolicy**: cambia `Never` → `Always` en todos los Deployments
- **imagePullSecrets**: agrega `ocir-secret` a todos los Deployments
- **Service type**: cambia `NodePort` → `LoadBalancer` en `modulo-inventarios` y `validacion-cep`
- **nodeSelector**: elimina `nodeSelector` (no aplica en OKE; el scheduler lo maneja)

Espera el rollout de los 7 Deployments.

#### Paso 8 — Inicializar MongoDB con seed

```bash
kubectl port-forward svc/mongodb-headless -n data 27017:27017 &
sleep 3

python3 scripts/init_inventory.py

kill %1
```

#### Paso 9 — Verificar despliegue

```bash
bash infra/oci/verify_oci.sh
```

Salida esperada:

```
============================================================
 CCP — Verificacion del Despliegue OKE
============================================================
>>> [1/5] Pods en namespace ccp...
NAME                            READY   STATUS    RESTARTS
corrector-xxx                   1/1     Running   0
log-auditoria-xxx               1/1     Running   0
modulo-inventarios-xxx          1/1     Running   0
inv-standby-xxx                 1/1     Running   0
modulo-seguridad-xxx            1/1     Running   0
monitor-xxx                     1/1     Running   0
validacion-cep-xxx              1/1     Running   0

>>> [4/5] Services con IPs externas...
NAME                  TYPE           CLUSTER-IP    EXTERNAL-IP      PORT(S)
modulo-inventarios    LoadBalancer   10.x.x.x      <IP-PUBLICA>     8090:xxx/TCP
validacion-cep        LoadBalancer   10.x.x.x      <IP-PUBLICA>     8094:xxx/TCP

>>> [5/5] Health checks via LoadBalancer IPs...
    modulo-inventarios: http://<IP>:8090/health -> OK
    validacion-cep: http://<IP>:8094/health -> OK
============================================================
```

### 5.4 Teardown (eliminar recursos OCI)

Para eliminar todos los recursos y evitar costos:

```bash
bash infra/oci/teardown_oke.sh
```

Este script elimina en orden: servicios K8s, releases Helm, PVCs, namespaces, y el cluster OKE. Las imágenes de OCIR se eliminan manualmente desde OCI Console → Container Registry.

---

## 6. Ejecutar el experimento

Los scripts de experimento funcionan igual en ambos entornos (local y OCI) porque ambos usan `kubectl port-forward` para exponer los servicios en `localhost:30090-30096`.

### Opción A — Script completo (recomendado)

```bash
bash scripts/run_experiments.sh
```

El script:
1. Levanta port-forwards para los 6 servicios
2. Ejecuta Experimento A (CP-A1 a CP-A5) — ASR-1 Disponibilidad
3. Ejecuta Experimento B (CP-B1 a CP-B4) — ASR-2 Seguridad
4. Genera `scripts/final_report.json`
5. Cierra los port-forwards al salir

Duración: **3-4 minutos**.

### Opción B — Experimentos por separado

```bash
# Abrir port-forwards
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

# Reporte final
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

### Limpiar estado entre ejecuciones

```bash
# Resetear inventario a valores iniciales
curl -s -X POST http://localhost:30090/reset

# Desactivar fault injection si quedó activo
curl -s -X POST http://localhost:30090/fault-inject \
     -H "Content-Type: application/json" \
     -d '{"tipo": "none"}'

# Limpiar ventana CEP
curl -s -X POST http://localhost:30094/reset
```

---

## 7. Dashboard en tiempo real

Muestra el estado del sistema en vivo desde la terminal, actualizándose cada 2 segundos.

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

Inyecta automáticamente:

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

**Hipótesis H1: CONFIRMADA** — todos los tiempos de detección están entre **0 y 24 ms**, 10–300× por debajo del umbral de 300 ms.

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

### Modo local (Kind)

#### El cluster Kind no existe

```bash
kind create cluster --name ccp-experiment --config infra/kind-config.yaml
```

#### MongoDB no arranca o el RS no se inicializa

```bash
kubectl logs -n data job/mongodb-rs-init

# Si el job expiró, recrearlo
kubectl delete job mongodb-rs-init -n data
kubectl apply -f infra/mongodb-rs-init-job.yaml
```

#### El Monitor no arranca (consumer NATS ya existe)

Ocurre cuando hay dos pods intentando usar el mismo consumer durable:

```bash
kubectl get pods -n ccp | grep monitor
kubectl delete pod -n ccp <nombre-pod-viejo> --force --grace-period=0
```

#### Las imágenes no se encuentran en Kind (`ErrImageNeverPull`)

```bash
kind load docker-image ccp/<servicio>:latest --name ccp-experiment
kubectl rollout restart deployment/<servicio> -n ccp
```

#### Los port-forwards no responden

```bash
pkill -f "kubectl port-forward"
sleep 2
kubectl port-forward -n ccp svc/modulo-inventarios 30090:8090 &
# ... resto de servicios
```

#### Resetear el experimento completamente

```bash
kind delete cluster --name ccp-experiment
DEPLOY_TARGET=local bash infra/deploy.sh
```

---

### Modo OCI (OKE)

#### `ImagePullBackOff` — los pods no pueden descargar imágenes

```bash
kubectl describe pod <pod> -n ccp | grep -A 10 Events

# Verificar que el secret existe
kubectl get secret ocir-secret -n ccp

# Recrear el secret
bash infra/oci/imagepullsecret.sh

# Verificar que la imagen existe en OCIR
oci artifacts container image list --compartment-id "${OCI_COMPARTMENT_ID}" \
  --repository-name ccp
```

#### PVC en `Pending` — MongoDB no arranca

```bash
# Verificar que la StorageClass existe
kubectl get sc
# Debe aparecer: oci-bv

# Ver por qué el PVC no se asigna
kubectl describe pvc -n data
```

#### Load Balancer sin IP externa

La IP pública puede tardar 3-5 minutos en asignarse. Si no aparece después de 10 minutos:

```bash
kubectl describe svc modulo-inventarios -n ccp | grep -A 5 Events
```

Verificar en OCI Console → **Networking → Load Balancers** que no haya errores. El subnet del Load Balancer debe tener reglas de seguridad que permitan tráfico entrante en los puertos 8090 y 8094.

#### `oci ce cluster create` falla por permisos

```bash
# Verificar políticas IAM del compartment
oci iam policy list --compartment-id "${OCI_COMPARTMENT_ID}"

# Verificar service limits
oci limits value list --compartment-id "${OCI_COMPARTMENT_ID}" \
  --service-name oke --query 'data[*].{name:name,value:value}'
```

#### t_deteccion > 300 ms en OKE

Improbable pero puede ocurrir si los pods están en zonas de disponibilidad distintas. Verificar:

```bash
kubectl get pods -n ccp -o wide
# Si los pods están en nodos de ADs distintos, verificar latencia con:
kubectl exec -n ccp <pod-modulo-inventarios> -- curl -s http://nats.messaging.svc.cluster.local:4222
```

#### Teardown y recreación completa

```bash
bash infra/oci/teardown_oke.sh
DEPLOY_TARGET=oci bash infra/deploy.sh
```
