# Spec: Infraestructura del Cluster Kind con NATS JetStream y MongoDB Replica Set

## Objetivo

Provisionar un cluster Kubernetes local con Kind que contenga 1 control-plane y 2 worker nodes, con NATS JetStream y MongoDB Replica Set desplegados via Helm. Este cluster es la base sobre la que corren todos los servicios del CCP y debe estar completamente operativo antes de desplegar cualquier microservicio.

## Alcance

**En scope:**
- Archivo `kind-config.yaml` con 3 nodos (1 control-plane, 2 workers)
- Namespaces `ccp` (servicios), `data` (MongoDB), `messaging` (NATS)
- Despliegue de NATS JetStream via Helm chart `nats/nats`
- Despliegue de MongoDB Replica Set via Helm chart `bitnami/mongodb` con replica set habilitado
- Creación de streams NATS: `heartbeat.inventario.*`, `correccion.*`, `failover.*`
- Labels en nodos para scheduling: `ModuloInventario-standby` en worker-node-2, MongoDB Secondary en worker-node-2
- Script de verificación que confirma todos los pods Running

**Fuera de scope:**
- Despliegue de microservicios del CCP (cubierto por otras specs)
- Configuración de red externa / ingress real

## Criterios de Aceptacion

- [ ] `kind get clusters` muestra el cluster `ccp-experiment`
- [ ] `kubectl get nodes` muestra 3 nodos Ready (1 control-plane, 2 workers)
- [ ] Namespace `ccp`, `data`, `messaging` existen
- [ ] Pod NATS en estado Running en namespace `messaging`
- [ ] Streams NATS creados: `HEARTBEAT_INVENTARIO`, `CORRECCION`, `FAILOVER`
- [ ] MongoDB Replica Set con Primary en worker-node-1 y Secondary en worker-node-2, ambos Running en namespace `data`
- [ ] `rs.status()` en MongoDB muestra replica set sano
- [ ] worker-node-2 tiene label `role=standby`

## Inputs Requeridos

- Instalacion local de Docker Desktop
- `brew install kind helm kubectl`

## Outputs Esperados

| Archivo | Descripcion |
|---|---|
| `infra/kind-config.yaml` | Configuracion del cluster Kind con 3 nodos y port mappings |
| `infra/namespaces.yaml` | Manifiesto de namespaces |
| `infra/nats-values.yaml` | Values para Helm chart de NATS con JetStream habilitado |
| `infra/mongodb-values.yaml` | Values para Helm chart de MongoDB con replica set |
| `infra/nats-streams.yaml` | Manifiesto o script para crear los streams NATS |
| `infra/node-labels.sh` | Script para etiquetar nodos |
| `infra/setup.sh` | Script maestro que ejecuta todo el setup en orden |
| `infra/verify.sh` | Script que verifica que toda la infra esta operativa |

## Agente Responsable

`infra-setup`

## Convenciones a Respetar

- HeartBeat < 300 ms: la infraestructura debe tener latencia minima entre pods (Kind lo garantiza por ser local, pero verificar con ping entre pods)
- Nombres de streams NATS deben coincidir con los topics definidos en el diseno: `heartbeat.inventario.{ok,stock_negativo,divergencia_reservas,estado_concurrente,self_test_failed}`
- MongoDB debe usar autenticacion basica (usuario/password) para no complicar el harness

## Pasos de Ejecucion

1. Crear archivo `infra/kind-config.yaml`:
   ```yaml
   kind: Cluster
   apiVersion: kind.x-k8s.io/v1alpha4
   nodes:
     - role: control-plane
       extraPortMappings:
         - containerPort: 30080
           hostPort: 8080
           protocol: TCP
         - containerPort: 30222
           hostPort: 4222
           protocol: TCP
     - role: worker
       labels:
         node-role: primary
     - role: worker
       labels:
         node-role: standby
   ```

2. Crear el cluster:
   ```bash
   kind create cluster --name ccp-experiment --config infra/kind-config.yaml
   ```

3. Crear namespaces (`infra/namespaces.yaml`):
   ```yaml
   apiVersion: v1
   kind: Namespace
   metadata:
     name: ccp
   ---
   apiVersion: v1
   kind: Namespace
   metadata:
     name: data
   ---
   apiVersion: v1
   kind: Namespace
   metadata:
     name: messaging
   ```

4. Agregar repos Helm e instalar NATS:
   ```bash
   helm repo add nats https://nats-io.github.io/k8s/helm/charts/
   helm repo add bitnami https://charts.bitnami.com/bitnami
   helm repo update
   helm install nats nats/nats -n messaging -f infra/nats-values.yaml
   ```

5. Crear `infra/nats-values.yaml`:
   ```yaml
   nats:
     jetstream:
       enabled: true
       memStorage:
         enabled: true
         size: 256Mi
       fileStorage:
         enabled: true
         size: 1Gi
   ```

6. Instalar MongoDB Replica Set:
   ```bash
   helm install mongodb bitnami/mongodb -n data -f infra/mongodb-values.yaml
   ```

7. Crear `infra/mongodb-values.yaml`:
   ```yaml
   architecture: replicaset
   replicaCount: 2
   auth:
     rootPassword: "ccp-experiment-2024"
     replicaSetKey: "ccp-rs-key"
   persistence:
     size: 1Gi
   nodeSelector:
     # Primary en worker con label primary, secondary se distribuye automaticamente
   ```

8. Etiquetar nodos:
   ```bash
   kubectl label nodes ccp-experiment-worker role=primary
   kubectl label nodes ccp-experiment-worker2 role=standby
   ```

9. Crear streams NATS usando `nats` CLI o un Job de Kubernetes:
   ```bash
   # Usando port-forward al pod NATS
   kubectl port-forward svc/nats -n messaging 4222:4222 &
   nats stream add HEARTBEAT_INVENTARIO --subjects "heartbeat.inventario.*" --storage memory --replicas 1 --retention limits --max-msgs 10000 --max-age 1h
   nats stream add CORRECCION --subjects "correccion.*" --storage memory --replicas 1 --retention limits --max-msgs 10000 --max-age 1h
   nats stream add FAILOVER --subjects "failover.*" --storage memory --replicas 1 --retention limits --max-msgs 10000 --max-age 1h
   ```

10. Crear `infra/verify.sh` que ejecuta:
    ```bash
    #!/bin/bash
    set -e
    echo "=== Verificando cluster Kind ==="
    kubectl get nodes -o wide
    echo "=== Verificando namespaces ==="
    kubectl get ns ccp data messaging
    echo "=== Verificando NATS ==="
    kubectl get pods -n messaging
    echo "=== Verificando MongoDB ==="
    kubectl get pods -n data
    echo "=== Verificando streams NATS ==="
    nats stream ls
    echo "=== Verificando MongoDB Replica Set ==="
    kubectl exec -n data mongodb-0 -- mongosh --eval "rs.status()" -u root -p ccp-experiment-2024
    echo "=== Infraestructura OK ==="
    ```

11. Ejecutar `verify.sh` y confirmar que todo pasa.

## Notas de Arquitectura

| Elemento | Decision | Razonamiento |
|---|---|---|
| Kind en lugar de minikube | Kind soporta multi-node nativamente | Necesitamos 2 workers para simular failover de INV-Standby en nodo separado |
| NATS JetStream (no Core) | JetStream provee persistencia y replay | Necesario para que el Colector de Metricas pueda re-leer eventos; ademas garantiza at-least-once delivery |
| MongoDB Replica Set | Replica set con 2 miembros | Simula la redundancia de datos; INV-Standby lee del secondary |
| Streams con retention limits | Max 10000 msgs, max-age 1h | Suficiente para el experimento; evita acumulacion |
| Namespaces separados | ccp / data / messaging | Aislamiento logico; facilita cleanup selectivo entre ejecuciones |
