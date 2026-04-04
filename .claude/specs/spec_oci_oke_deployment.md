# Spec: Fase 6 — Despliegue en Oracle Cloud Infrastructure (OKE)

## Objetivo

Migrar el experimento CCP completo — que actualmente funciona en un cluster Kind local con
9/9 casos de prueba pasados — a un cluster OKE (Oracle Kubernetes Engine) en produccion real
sobre OCI. El objetivo es demostrar que los ASRs de Disponibilidad y Seguridad se cumplen
en infraestructura cloud real, no solo en simulacion local.

## Alcance

**En scope:**
- Provisionamiento del cluster OKE y recursos OCI asociados
- Push de imagenes Docker a OCIR (OCI Container Registry)
- Instalacion de NATS JetStream y MongoDB Replica Set con storage OCI
- Kustomize overlay para adaptar manifiestos Kind → OKE
- Ejecucion de los 9 casos de prueba apuntando a IPs publicas OCI
- Script de setup y teardown reproducible

**Fuera de scope:**
- Modificacion de la logica de los 6 microservicios (permanecen identicos)
- Cambios en los scripts de experimentos (solo cambian URLs base)
- DNS personalizado, TLS/HTTPS, Ingress Controller
- CI/CD pipeline (el despliegue es manual y reproducible)

## Prerequisitos OCI

El operador debe tener antes de invocar al agente:

```bash
# 1. OCI CLI instalado y configurado
oci --version                              # >= 3.x
oci setup config                           # ya ejecutado con tenancy OCID, user OCID, region, API key

# 2. kubectl apuntando al cluster OKE (se configura en paso 1)
kubectl version --client                   # >= 1.28

# 3. Docker con acceso a OCIR
docker login <region>.ocir.io              # user: <namespace>/oracleidentitycloudservice/<email>
                                           # password: auth token generado en OCI Console

# 4. Helm
helm version                               # >= 3.12

# 5. Variables de entorno requeridas (el operador las exporta antes de ejecutar)
export OCI_REGION="sa-bogota-1"            # region OCI (ajustar segun cuenta)
export OCI_TENANCY_NAMESPACE="<namespace>" # visible en OCI Console > Tenancy details
export OCI_COMPARTMENT_ID="ocid1.compartment.oc1..<unique_id>"
export OCIR_USERNAME="${OCI_TENANCY_NAMESPACE}/oracleidentitycloudservice/<email>"
export OCIR_PASSWORD="<auth_token>"        # generado en OCI Console > User > Auth Tokens
```

## Infraestructura OCI a Provisionar

| Recurso | Especificacion | Justificacion |
|---|---|---|
| OKE Cluster | Kubernetes 1.28+, public endpoint | Compatible con kubectl remoto |
| Node Pool | 3 workers, VM.Standard.E4.Flex, 2 OCPUs, 8 GB RAM | Suficiente para 6 pods + NATS + MongoDB |
| OCIR | Repository `ccp/` con 6 imagenes | Reemplaza `kind load docker-image` |
| Block Volumes | 50 GB por nodo MongoDB (StorageClass `oci-bv`) | PVCs reales para MongoDB Replica Set |
| Load Balancer | Creado automaticamente por Services tipo LoadBalancer | Acceso publico a modulo-inventarios y validacion-cep |
| VCN + Subnets | Creados por OKE (managed) | Red del cluster |

## Diferencias Clave vs Kind Local

| Aspecto | Kind (local) | OKE (OCI) |
|---|---|---|
| Imagenes | `docker build` + `kind load docker-image` | `docker build` + `docker tag` + `docker push` a OCIR |
| `imagePullPolicy` | `Never` | `Always` |
| `imagePullSecrets` | No necesario | `ocir-secret` en cada namespace que use imagenes OCIR |
| Service type | `NodePort` (30090, 30091, ...) | `LoadBalancer` para servicios con acceso externo |
| Storage MongoDB | `emptyDir` / PVC local Kind | `StorageClass: oci-bv` (OCI Block Volume) |
| Storage NATS | Memory + file local | PVC con `StorageClass: oci-bv` |
| `nodeSelector` | `role: primary` / `role: standby` | Remover o usar labels OKE equivalentes |
| URLs internas | Igual (Kubernetes DNS) | Igual (Kubernetes DNS) |
| URLs externas | `localhost:30090` | `<LoadBalancer-IP>:8090` |

## Inputs Requeridos

- Todos los archivos de `services/*/Dockerfile` (6 servicios ya construidos)
- Manifiestos en `k8s/*.yaml` (base para el overlay Kustomize)
- `infra/mongodb-values.yaml` y `infra/nats-values.yaml` (base para valores OCI)
- `experiments/experiment_a/run_experiment_a.py` y `experiments/experiment_b/run_experiment_b.py`
- `scripts/validate_asrs.py`, `scripts/init_inventory.py`
- Credenciales OCI (variables de entorno del operador)

## Outputs Esperados

```
infra/oci/
├── setup_oke.sh              # Provisionamiento OKE cluster + node pool
├── teardown_oke.sh           # Eliminacion limpia de todos los recursos OCI
├── ocir_push.sh              # Build + tag + push de las 6 imagenes a OCIR
├── imagepullsecret.sh        # Crea el Secret de autenticacion OCIR
├── mongodb-values-oci.yaml   # Helm values para MongoDB en OKE
├── nats-values-oci.yaml      # Helm values para NATS en OKE
├── deploy_services.sh        # Instala Helm charts + aplica Kustomize overlay
└── verify_oci.sh             # Verificacion end-to-end del despliegue

k8s/overlays/oci/
├── kustomization.yaml        # Overlay Kustomize: image refs, imagePullSecrets, Service types
├── patch-services-lb.yaml    # Patch: NodePort → LoadBalancer para servicios con acceso externo
├── patch-imagepull.yaml      # Patch: agrega imagePullSecrets a todos los Deployments
└── patch-remove-nodeselector.yaml  # Patch: remueve nodeSelector de modulo-inventarios e inv-standby
```

## Agente Responsable

`oci-oke-deployer`

## Convenciones a Respetar

- Los 6 microservicios no se modifican en absoluto; la migracion es puramente de infraestructura
- Las URLs internas del cluster (Kubernetes DNS) permanecen identicas
- Los nombres de los Deployments, Services y namespaces (`ccp`, `data`, `messaging`) no cambian
- Los HeartBeats deben seguir cumpliendo < 300 ms (verificar en OKE)
- Los mensajes al tendero siguen usando respuestas enmascaradas (no cambia la logica)
- Los streams NATS mantienen los mismos nombres y subjects

## Pasos de Ejecucion

### Paso 1 — Provisionar el Cluster OKE

```bash
#!/bin/bash
# infra/oci/setup_oke.sh

set -euo pipefail

: "${OCI_REGION:?Variable OCI_REGION requerida}"
: "${OCI_COMPARTMENT_ID:?Variable OCI_COMPARTMENT_ID requerida}"
: "${OCI_TENANCY_NAMESPACE:?Variable OCI_TENANCY_NAMESPACE requerida}"

CLUSTER_NAME="ccp-experiment-oke"
K8S_VERSION="v1.28.2"
NODE_SHAPE="VM.Standard.E4.Flex"
NODE_OCPUS=2
NODE_MEMORY_GB=8
NODE_COUNT=3

echo ">>> [1/4] Creando VCN para OKE..."
# OKE Quick Create genera VCN + subnets automaticamente.
# Si se necesita control fino, usar oci network vcn create.

echo ">>> [2/4] Creando cluster OKE '${CLUSTER_NAME}'..."
oci ce cluster create \
  --compartment-id "${OCI_COMPARTMENT_ID}" \
  --name "${CLUSTER_NAME}" \
  --kubernetes-version "${K8S_VERSION}" \
  --type ENHANCED_CLUSTER \
  --endpoint-subnet-id "${ENDPOINT_SUBNET_ID}" \
  --service-lb-subnet-ids "[\"${LB_SUBNET_ID}\"]" \
  --wait-for-state SUCCEEDED \
  --wait-for-state FAILED

CLUSTER_ID=$(oci ce cluster list \
  --compartment-id "${OCI_COMPARTMENT_ID}" \
  --name "${CLUSTER_NAME}" \
  --lifecycle-state ACTIVE \
  --query 'data[0].id' --raw-output)

echo "    Cluster ID: ${CLUSTER_ID}"

echo ">>> [3/4] Creando node pool..."
oci ce node-pool create \
  --compartment-id "${OCI_COMPARTMENT_ID}" \
  --cluster-id "${CLUSTER_ID}" \
  --name "ccp-workers" \
  --kubernetes-version "${K8S_VERSION}" \
  --node-shape "${NODE_SHAPE}" \
  --node-shape-config "{\"ocpus\": ${NODE_OCPUS}, \"memoryInGBs\": ${NODE_MEMORY_GB}}" \
  --size "${NODE_COUNT}" \
  --node-image-id "${NODE_IMAGE_ID}" \
  --placement-configs "[{\"availabilityDomain\": \"${AD}\", \"subnetId\": \"${WORKER_SUBNET_ID}\"}]" \
  --wait-for-state SUCCEEDED \
  --wait-for-state FAILED

echo ">>> [4/4] Configurando kubeconfig..."
oci ce cluster create-kubeconfig \
  --cluster-id "${CLUSTER_ID}" \
  --file "${HOME}/.kube/config" \
  --region "${OCI_REGION}" \
  --token-version 2.0.0 \
  --kube-endpoint PUBLIC_ENDPOINT

kubectl get nodes
echo "    OKE cluster listo."
```

**Nota importante:** Los valores `ENDPOINT_SUBNET_ID`, `LB_SUBNET_ID`, `WORKER_SUBNET_ID`,
`NODE_IMAGE_ID` y `AD` dependen de la cuenta OCI del operador. Se pueden obtener con:
```bash
# Listar availability domains
oci iam availability-domain list --compartment-id "${OCI_COMPARTMENT_ID}"

# Listar subnets del compartment
oci network subnet list --compartment-id "${OCI_COMPARTMENT_ID}"

# Listar imagenes de nodo compatibles con OKE
oci ce node-pool-options get --node-pool-option-id all --query 'data.sources[?sourceType==`IMAGE`].{id:imageId,name:sourceName}' | head -20
```

**Alternativa Terraform (recomendada para reproducibilidad):**
El agente puede generar un modulo Terraform usando el provider `oci` que provisionaria
VCN + OKE + Node Pool en un solo `terraform apply`. Esta alternativa se documenta como
mejora futura.

### Paso 2 — Push de Imagenes a OCIR

```bash
#!/bin/bash
# infra/oci/ocir_push.sh

set -euo pipefail

: "${OCI_REGION:?Variable OCI_REGION requerida}"
: "${OCI_TENANCY_NAMESPACE:?Variable OCI_TENANCY_NAMESPACE requerida}"

BASE_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
OCIR_PREFIX="${OCI_REGION}.ocir.io/${OCI_TENANCY_NAMESPACE}/ccp"
TAG="latest"

# Mapeo de servicios: directorio → nombre de imagen
declare -A SERVICES=(
  ["modulo_inventarios"]="modulo-inventarios"
  ["monitor"]="monitor"
  ["corrector"]="corrector"
  ["validacion_cep"]="validacion-cep"
  ["modulo_seguridad"]="modulo-seguridad"
  ["log_auditoria"]="log-auditoria"
)

echo "============================================================"
echo " CCP — Build & Push to OCIR: ${OCIR_PREFIX}"
echo "============================================================"

for dir in "${!SERVICES[@]}"; do
  image_name="${SERVICES[$dir]}"
  local_tag="ccp/${image_name}:${TAG}"
  remote_tag="${OCIR_PREFIX}/${image_name}:${TAG}"

  echo ""
  echo ">>> Building ${local_tag} from services/${dir}/..."
  docker build -t "${local_tag}" "${BASE_DIR}/services/${dir}/"

  echo ">>> Tagging as ${remote_tag}..."
  docker tag "${local_tag}" "${remote_tag}"

  echo ">>> Pushing ${remote_tag}..."
  docker push "${remote_tag}"

  echo "    Done: ${image_name}"
done

echo ""
echo "============================================================"
echo " Todas las imagenes subidas a OCIR."
echo " Verificar con: oci artifacts container image list --compartment-id \${OCI_COMPARTMENT_ID}"
echo "============================================================"
```

### Paso 3 — Crear imagePullSecret para OCIR

```bash
#!/bin/bash
# infra/oci/imagepullsecret.sh

set -euo pipefail

: "${OCI_REGION:?Variable OCI_REGION requerida}"
: "${OCIR_USERNAME:?Variable OCIR_USERNAME requerida}"
: "${OCIR_PASSWORD:?Variable OCIR_PASSWORD requerida}"

NAMESPACES=("ccp" "data" "messaging")
SECRET_NAME="ocir-secret"
REGISTRY="${OCI_REGION}.ocir.io"

for ns in "${NAMESPACES[@]}"; do
  echo ">>> Creando secret '${SECRET_NAME}' en namespace '${ns}'..."
  kubectl create secret docker-registry "${SECRET_NAME}" \
    --docker-server="${REGISTRY}" \
    --docker-username="${OCIR_USERNAME}" \
    --docker-password="${OCIR_PASSWORD}" \
    --docker-email="noreply@oci.com" \
    --namespace="${ns}" \
    --dry-run=client -o yaml | kubectl apply -f -
  echo "    Secret creado en ${ns}."
done
```

### Paso 4 — Instalar NATS JetStream via Helm (con PVC OCI)

```yaml
# infra/oci/nats-values-oci.yaml
config:
  jetstream:
    enabled: true
    memStorage:
      enabled: true
      size: 256Mi
    fileStorage:
      enabled: true
      size: 5Gi
      storageDirectory: /data/jetstream
      storageClassName: oci-bv
```

```bash
kubectl create namespace messaging --dry-run=client -o yaml | kubectl apply -f -

helm repo add nats https://nats-io.github.io/k8s/helm/charts/ 2>/dev/null || true
helm repo update

helm install nats nats/nats \
  -n messaging \
  -f infra/oci/nats-values-oci.yaml \
  --wait --timeout 5m
```

### Paso 5 — Instalar MongoDB Replica Set via Helm (con StorageClass OCI)

```yaml
# infra/oci/mongodb-values-oci.yaml
architecture: replicaset
replicaCount: 2
arbiter:
  enabled: false
auth:
  rootPassword: "ccp-experiment-2024"
  replicaSetKey: "ccp-rs-key"
persistence:
  size: 50Gi
  storageClass: "oci-bv"
resources:
  requests:
    cpu: 500m
    memory: 1Gi
  limits:
    cpu: "1"
    memory: 2Gi
```

```bash
kubectl create namespace data --dry-run=client -o yaml | kubectl apply -f -

helm repo add bitnami https://charts.bitnami.com/bitnami 2>/dev/null || true
helm repo update

helm install mongodb bitnami/mongodb \
  -n data \
  -f infra/oci/mongodb-values-oci.yaml \
  --wait --timeout 5m
```

### Paso 6 — Crear Kustomize Overlay para OCI

```yaml
# k8s/overlays/oci/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - ../../modulo-inventarios.yaml
  - ../../inv-standby.yaml
  - ../../inv-standby-svc.yaml
  - ../../monitor.yaml
  - ../../corrector.yaml
  - ../../validacion-cep.yaml
  - ../../modulo-seguridad.yaml
  - ../../log-auditoria.yaml

images:
  - name: ccp/modulo-inventarios
    newName: ${OCI_REGION}.ocir.io/${OCI_TENANCY_NAMESPACE}/ccp/modulo-inventarios
    newTag: latest
  - name: ccp/monitor
    newName: ${OCI_REGION}.ocir.io/${OCI_TENANCY_NAMESPACE}/ccp/monitor
    newTag: latest
  - name: ccp/corrector
    newName: ${OCI_REGION}.ocir.io/${OCI_TENANCY_NAMESPACE}/ccp/corrector
    newTag: latest
  - name: ccp/validacion-cep
    newName: ${OCI_REGION}.ocir.io/${OCI_TENANCY_NAMESPACE}/ccp/validacion-cep
    newTag: latest
  - name: ccp/modulo-seguridad
    newName: ${OCI_REGION}.ocir.io/${OCI_TENANCY_NAMESPACE}/ccp/modulo-seguridad
    newTag: latest
  - name: ccp/log-auditoria
    newName: ${OCI_REGION}.ocir.io/${OCI_TENANCY_NAMESPACE}/ccp/log-auditoria
    newTag: latest

patches:
  - path: patch-imagepull.yaml
  - path: patch-services-lb.yaml
  - path: patch-remove-nodeselector.yaml
```

```yaml
# k8s/overlays/oci/patch-imagepull.yaml
# Agrega imagePullSecrets y cambia imagePullPolicy a Always en todos los Deployments
apiVersion: apps/v1
kind: Deployment
metadata:
  name: modulo-inventarios
  namespace: ccp
spec:
  template:
    spec:
      imagePullSecrets:
        - name: ocir-secret
      containers:
        - name: modulo-inventarios
          imagePullPolicy: Always
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: inv-standby
  namespace: ccp
spec:
  template:
    spec:
      imagePullSecrets:
        - name: ocir-secret
      containers:
        - name: inv-standby
          imagePullPolicy: Always
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: monitor
  namespace: ccp
spec:
  template:
    spec:
      imagePullSecrets:
        - name: ocir-secret
      containers:
        - name: monitor
          imagePullPolicy: Always
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: corrector
  namespace: ccp
spec:
  template:
    spec:
      imagePullSecrets:
        - name: ocir-secret
      containers:
        - name: corrector
          imagePullPolicy: Always
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: validacion-cep
  namespace: ccp
spec:
  template:
    spec:
      imagePullSecrets:
        - name: ocir-secret
      containers:
        - name: validacion-cep
          imagePullPolicy: Always
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: modulo-seguridad
  namespace: ccp
spec:
  template:
    spec:
      imagePullSecrets:
        - name: ocir-secret
      containers:
        - name: modulo-seguridad
          imagePullPolicy: Always
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: log-auditoria
  namespace: ccp
spec:
  template:
    spec:
      imagePullSecrets:
        - name: ocir-secret
      containers:
        - name: log-auditoria
          imagePullPolicy: Always
```

```yaml
# k8s/overlays/oci/patch-services-lb.yaml
# Cambia NodePort → LoadBalancer para servicios con acceso externo
# Solo modulo-inventarios y validacion-cep necesitan IP publica (los experimentos los invocan)
apiVersion: v1
kind: Service
metadata:
  name: modulo-inventarios
  namespace: ccp
spec:
  type: LoadBalancer
  ports:
    - name: http
      port: 8090
      targetPort: 8090
---
apiVersion: v1
kind: Service
metadata:
  name: validacion-cep
  namespace: ccp
spec:
  type: LoadBalancer
  ports:
    - name: http
      port: 8094
      targetPort: 8094
```

```yaml
# k8s/overlays/oci/patch-remove-nodeselector.yaml
# Remueve nodeSelector de los Deployments que lo tienen (primary/standby)
# En OKE no etiquetamos nodos manualmente; el scheduler de K8s distribuye los pods
apiVersion: apps/v1
kind: Deployment
metadata:
  name: modulo-inventarios
  namespace: ccp
spec:
  template:
    spec:
      nodeSelector: null
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: inv-standby
  namespace: ccp
spec:
  template:
    spec:
      nodeSelector: null
```

### Paso 7 — Aplicar Manifiestos con Kustomize

```bash
#!/bin/bash
# infra/oci/deploy_services.sh

set -euo pipefail

: "${OCI_REGION:?Variable OCI_REGION requerida}"
: "${OCI_TENANCY_NAMESPACE:?Variable OCI_TENANCY_NAMESPACE requerida}"

BASE_DIR="$(cd "$(dirname "$0")/../.." && pwd)"

echo "============================================================"
echo " CCP — Deploy Services to OKE"
echo "============================================================"

# 1. Crear namespaces
echo ">>> [1/5] Creando namespaces..."
kubectl apply -f "${BASE_DIR}/infra/namespaces.yaml"

# 2. Crear imagePullSecret
echo ">>> [2/5] Creando imagePullSecret..."
bash "${BASE_DIR}/infra/oci/imagepullsecret.sh"

# 3. Sustituir variables de entorno en kustomization.yaml
#    Kustomize no soporta variables de entorno nativas; usamos envsubst
echo ">>> [3/5] Generando manifiestos con Kustomize..."
cd "${BASE_DIR}/k8s/overlays/oci"
envsubst < kustomization.yaml > kustomization-rendered.yaml
mv kustomization-rendered.yaml kustomization.yaml

# 4. Aplicar overlay
echo ">>> [4/5] Aplicando overlay OCI..."
kubectl apply -k "${BASE_DIR}/k8s/overlays/oci/"

# 5. Esperar rollouts
echo ">>> [5/5] Esperando rollouts..."
DEPLOYMENTS=(modulo-inventarios inv-standby monitor corrector validacion-cep modulo-seguridad log-auditoria)
for dep in "${DEPLOYMENTS[@]}"; do
  echo "    Waiting for ${dep}..."
  kubectl rollout status deployment/"${dep}" -n ccp --timeout=180s
done

echo ""
echo "============================================================"
echo " Servicios desplegados en OKE."
echo " Obtener IPs publicas con:"
echo "   kubectl get svc -n ccp"
echo "============================================================"
```

### Paso 8 — Inicializar MongoDB con seed

```bash
# Port-forward al MongoDB en OKE (o usar la IP interna si se tiene acceso VPN)
kubectl port-forward svc/mongodb-headless -n data 27017:27017 &
PF_PID=$!
sleep 3

python scripts/init_inventory.py

kill $PF_PID 2>/dev/null || true
```

### Paso 9 — Crear Streams NATS en OKE

```bash
kubectl port-forward svc/nats -n messaging 4222:4222 &
PF_PID=$!
sleep 3

nats stream add HEARTBEAT_INVENTARIO \
  --subjects "heartbeat.inventario.*" \
  --storage memory --replicas 1 \
  --retention limits --max-msgs 10000 --max-age 1h \
  --server nats://localhost:4222 2>/dev/null || echo "(Stream ya existe)"

nats stream add CORRECCION \
  --subjects "correccion.*" \
  --storage memory --replicas 1 \
  --retention limits --max-msgs 10000 --max-age 1h \
  --server nats://localhost:4222 2>/dev/null || echo "(Stream ya existe)"

nats stream add FAILOVER \
  --subjects "failover.*" \
  --storage memory --replicas 1 \
  --retention limits --max-msgs 10000 --max-age 1h \
  --server nats://localhost:4222 2>/dev/null || echo "(Stream ya existe)"

kill $PF_PID 2>/dev/null || true
```

### Paso 10 — Verificar Despliegue

```bash
#!/bin/bash
# infra/oci/verify_oci.sh

set -euo pipefail

echo "============================================================"
echo " CCP — Verificacion del Despliegue OKE"
echo "============================================================"

echo ""
echo ">>> [1/5] Pods en namespace ccp..."
kubectl get pods -n ccp -o wide
echo ""

echo ">>> [2/5] Pods en namespace data..."
kubectl get pods -n data -o wide
echo ""

echo ">>> [3/5] Pods en namespace messaging..."
kubectl get pods -n messaging -o wide
echo ""

echo ">>> [4/5] Services con IPs externas..."
kubectl get svc -n ccp
echo ""

echo ">>> [5/5] Health checks via LoadBalancer IPs..."
INV_IP=$(kubectl get svc modulo-inventarios -n ccp -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
CEP_IP=$(kubectl get svc validacion-cep -n ccp -o jsonpath='{.status.loadBalancer.ingress[0].ip}')

echo "    modulo-inventarios: http://${INV_IP}:8090/health"
curl -sf "http://${INV_IP}:8090/health" && echo " -> OK" || echo " -> FAIL"

echo "    validacion-cep: http://${CEP_IP}:8094/health"
curl -sf "http://${CEP_IP}:8094/health" && echo " -> OK" || echo " -> FAIL"

echo ""
echo "============================================================"
echo " Para ejecutar experimentos, usar:"
echo "   BASE_URL_INV=http://${INV_IP}:8090 BASE_URL_CEP=http://${CEP_IP}:8094 python scripts/validate_asrs.py"
echo "============================================================"
```

### Paso 11 — Ejecutar Experimentos contra OKE

Los scripts de experimento usan URLs hardcoded a `localhost:30090` y `localhost:30094`.
Para ejecutarlos contra OKE sin modificar los archivos originales, usar port-forward:

```bash
# Opcion A: Port-forward (recomendada — no modifica scripts)
kubectl port-forward svc/modulo-inventarios -n ccp 30090:8090 &
kubectl port-forward svc/validacion-cep -n ccp 30094:8094 &
kubectl port-forward svc/monitor -n ccp 30091:8091 &
kubectl port-forward svc/corrector -n ccp 30092:8092 &
kubectl port-forward svc/modulo-seguridad -n ccp 30093:8093 &
kubectl port-forward svc/log-auditoria -n ccp 30096:8096 &

# Ejecutar exactamente igual que en local
python scripts/validate_asrs.py

# Opcion B: Variables de entorno (requiere que los scripts las soporten)
# Esta opcion se deja como mejora futura
```

### Paso 12 — Teardown

```bash
#!/bin/bash
# infra/oci/teardown_oke.sh

set -euo pipefail

: "${OCI_COMPARTMENT_ID:?Variable OCI_COMPARTMENT_ID requerida}"

CLUSTER_NAME="ccp-experiment-oke"

echo ">>> Eliminando servicios..."
kubectl delete -k k8s/overlays/oci/ --ignore-not-found

echo ">>> Eliminando Helm releases..."
helm uninstall nats -n messaging --ignore-not-found 2>/dev/null || true
helm uninstall mongodb -n data --ignore-not-found 2>/dev/null || true

echo ">>> Eliminando PVCs..."
kubectl delete pvc --all -n data --ignore-not-found
kubectl delete pvc --all -n messaging --ignore-not-found

echo ">>> Eliminando namespaces..."
kubectl delete namespace ccp data messaging --ignore-not-found

CLUSTER_ID=$(oci ce cluster list \
  --compartment-id "${OCI_COMPARTMENT_ID}" \
  --name "${CLUSTER_NAME}" \
  --lifecycle-state ACTIVE \
  --query 'data[0].id' --raw-output)

if [ "${CLUSTER_ID}" != "null" ] && [ -n "${CLUSTER_ID}" ]; then
  echo ">>> Eliminando cluster OKE ${CLUSTER_ID}..."
  oci ce cluster delete \
    --cluster-id "${CLUSTER_ID}" \
    --force \
    --wait-for-state SUCCEEDED \
    --wait-for-state FAILED
  echo "    Cluster eliminado."
else
  echo "    Cluster no encontrado."
fi

echo ">>> Eliminando imagenes de OCIR..."
# Las imagenes se eliminan desde OCI Console o con:
# oci artifacts container image list --compartment-id ${OCI_COMPARTMENT_ID} --repository-name ccp
# oci artifacts container image delete --image-id <image-ocid> --force

echo ">>> Teardown completo."
```

## Criterios de Aceptacion

- [ ] Cluster OKE con 3 workers en estado `ACTIVE` (`oci ce cluster get`)
- [ ] 6 imagenes Docker subidas a OCIR (`oci artifacts container image list`)
- [ ] Todos los pods en estado `Running` en namespace `ccp` (`kubectl get pods -n ccp`)
- [ ] MongoDB Replica Set operativo en namespace `data` con PVCs de 50 GB
- [ ] NATS JetStream operativo en namespace `messaging` con PVC
- [ ] 3 streams NATS creados (HEARTBEAT_INVENTARIO, CORRECCION, FAILOVER)
- [ ] Load Balancer IPs accesibles publicamente para modulo-inventarios y validacion-cep
- [ ] Health check exitoso via IPs publicas (`curl http://<IP>:8090/health`)
- [ ] Los 9 casos de prueba (CP-A1..A5, CP-B1..B4) pasan con PASS
- [ ] `scripts/final_report.json` muestra `all_passed: true`
- [ ] Script `teardown_oke.sh` elimina todos los recursos sin dejar residuos

## Notas de Arquitectura

| Elemento | Decision | Razonamiento |
|---|---|---|
| Kustomize overlay vs fork de manifiestos | Overlay con patches | Mantiene los manifiestos Kind intactos; el overlay solo cambia lo necesario para OCI |
| Port-forward vs modificar scripts | Port-forward | Los scripts de experimento no se modifican; se reusan exactamente igual que en local |
| LoadBalancer solo para INV y CEP | 2 LBs, no 7 | Solo modulo-inventarios y validacion-cep necesitan acceso externo; el resto comunica via DNS interno |
| StorageClass oci-bv | Block Volume de OCI | StorageClass por defecto en OKE; provee PVCs persistentes reales (no emptyDir) |
| nodeSelector removido | null en overlay | OKE scheduler distribuye pods automaticamente; no necesitamos control manual de placement |
| Streams NATS con storage memory | Memory, no file | Coherente con la configuracion local; los HeartBeats son efimeros y no necesitan persistencia en disco |
| Imagenes con tag `latest` | Simplicidad para experimento academico | En produccion real se usarian tags versionados; para el experimento basta con `latest` + `imagePullPolicy: Always` |

## Troubleshooting OCI

| Problema | Causa probable | Solucion |
|---|---|---|
| `ImagePullBackOff` | imagePullSecret incorrecto o imagen no existe en OCIR | Verificar `kubectl describe pod <pod> -n ccp`; re-ejecutar `imagepullsecret.sh`; verificar que la imagen existe con `oci artifacts container image list` |
| LoadBalancer sin IP | Subnet del LB no tiene reglas de seguridad | Verificar Security Lists en OCI Console; el puerto 8090/8094 debe estar abierto en ingress |
| Pod `Pending` | Node pool sin capacidad o PVC no se puede crear | `kubectl describe pod` para ver eventos; verificar limits del shape con `oci ce node-pool get` |
| MongoDB no forma replica set | PVCs en `Pending` por StorageClass incorrecta | Verificar `kubectl get sc`; debe existir `oci-bv`; verificar `kubectl get pvc -n data` |
| t_deteccion > 300 ms | Latencia de red OCI entre pods | Verificar que los pods estan en el mismo AD; considerar usar `topology.kubernetes.io/zone` |
| NATS no conecta | Port-forward no activo o pod reiniciado | Verificar `kubectl get pods -n messaging`; re-hacer port-forward |
| `oci ce cluster create` falla | Quota excedida o permisos insuficientes | Verificar IAM policies del compartment; verificar service limits con `oci limits` |
