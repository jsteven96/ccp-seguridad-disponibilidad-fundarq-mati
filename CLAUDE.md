# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Propósito del repositorio

Proyecto académico de maestría en arquitectura de software que **diseña, implementa y valida empíricamente** dos Atributos de Significancia Arquitectónica (ASR) del CCP (Centro de Control de Pedidos) — un sistema de gestión de pedidos para tiendas de barrio.

El repositorio contiene:
1. **Diseño arquitectónico**: diagramas de secuencia Mermaid, diagrama de componentes y diagrama de despliegue (`.claude/docs/`)
2. **Implementación ejecutable**: 6 microservicios Python/FastAPI desplegados en Kubernetes local (Kind)
3. **Experimentos de validación**: 9 casos de prueba que confirman las dos hipótesis arquitectónicas
4. **Despliegue multi-entorno**: scripts para ejecutar el experimento en local (Kind) o en la nube (OCI/OKE) controlado por `DEPLOY_TARGET=local|oci`

**Resultados (2026-04-04)**: 9/9 casos de prueba pasados. H1 confirmada (detección < 300 ms). H2 confirmada (detección < 300 ms).

---

## ASR validados

### ASR-1 — Disponibilidad
**Hipótesis H1**: El mecanismo HeartBeat + VALCOH detecta cualquier inconsistencia de inventario en menos de 300 ms, permitiendo corrección automática sin intervención humana.

- **Táctica**: HeartBeat expandido (5 tipos de eventos) + self-test VALCOH (3 checks internos) + Monitor-Corrector + Failover a INV-Standby
- **Umbral**: < 300 ms desde detección hasta clasificación
- **Resultado**: 0.001–23.9 ms (10×–3000× por debajo del umbral)

### ASR-2 — Seguridad
**Hipótesis H2**: El motor CEP detecta un ataque DDoS de capa de negocio en menos de 300 ms usando correlación de señales, sin exponer criterios de detección al atacante.

- **Táctica**: CEP con ventana deslizante de 60 s, 3 señales correlacionadas, umbral ≥ 2 = ataque; respuesta enmascarada HTTP 429
- **Umbral**: < 300 ms desde primera señal hasta bloqueo
- **Resultado**: 0.009–0.019 ms

---

## Arquitectura del sistema

### Diseño (fuentes de verdad)

Los diagramas de diseño están en `.claude/docs/`:
- `diagrama_componentes_actualizado.drawio` — diagrama de componentes UML con capas: Aplicación Móvil, Capa de Acceso, Lógica de Negocio, Seguridad, Datos
- `diagrama_despliegue_actualizado.drawio` — diagrama de despliegue con nodos: OCI/AWS Cloud, Public Subnet (API Gateway, Load Balancer), Private Subnet/Kubernetes Cluster (namespaces workers + data)

### Componentes implementados

| Componente | Servicio | Puerto | Rol |
|---|---|---|---|
| Módulo de Inventarios (INV) | `services/modulo_inventarios/` | 8090 | Gestión de stock; VALCOH self-test; publica HeartBeat a NATS |
| INV-Standby | `services/modulo_inventarios/` (STANDBY_MODE=true) | 8095 | Réplica pasiva; se activa ante SELF_TEST_FAILED |
| Monitor (MON) | `services/monitor/` | 8091 | Suscriptor NATS JetStream; enruta HeartBeats al Corrector según tipo |
| Corrector (CORR) | `services/corrector/` | 8092 | Ejecuta rollback (`/corregir`), reconciliación (`/reconciliar`), failover (`/failover`) |
| Validación CEP (VS) | `services/validacion_cep/` | 8094 | Motor CEP: 3 señales, ventana 60 s, umbral ≥ 2 → HTTP 429 |
| Módulo de Seguridad (SEG) | `services/modulo_seguridad/` | 8093 | Bloqueo de actores por 24 h; revocación JWT |
| Log de Auditoría | `services/log_auditoria/` | 8096 | Persistencia forense independiente en MongoDB |

### NATS JetStream — topics y streams

| Stream | Topic(s) | Productor | Consumidor |
|---|---|---|---|
| `HEARTBEAT_INVENTARIO` | `heartbeat.inventario.ok` `heartbeat.inventario.stock_negativo` `heartbeat.inventario.divergencia_reservas` `heartbeat.inventario.estado_concurrente` `heartbeat.inventario.self_test_failed` | ModuloInventarios | Monitor |
| `CORRECCION` | `correccion.stock` `correccion.reservas` | Corrector | (observadores) |
| `FAILOVER` | `failover.activado` | Corrector | (observadores) |

### VALCOH — 3 checks del self-test

Ejecutado por `services/modulo_inventarios/valcoh.py` antes de cada HeartBeat:
1. **Check stock**: `stock >= 0` → si falla → tipo `STOCK_NEGATIVO`
2. **Check reservas**: `suma(reservas_activas) == stock_inicial - stock` → si falla → tipo `DIVERGENCIA_RESERVAS`
3. **Check concurrencia**: locking optimista via campo `version` en MongoDB → si falla → tipo `ESTADO_CONCURRENTE`

Si el self-test completo falla → tipo `SELF_TEST_FAILED` → failover a INV-Standby.

### Motor CEP — 3 señales

Implementado en `services/validacion_cep/cep_engine.py`, ventana deslizante de 60 s:
1. **Rate**: > 10 requests en la ventana
2. **Concentración SKU**: > 80% de requests sobre el mismo SKU
3. **Tasa de cancelación**: > 50% de operaciones son cancelaciones

Si ≥ 2 señales activas → ataque confirmado → HTTP 429 (mensaje genérico, sin exponer criterios).

---

## Estructura del repositorio

```
.
├── ASR_escenario1_happy_path.md              # Flujo exitoso — HeartBeat OK
├── ASR_escenario1_happy_path_solo_deteccion.md
├── ASR_escenario2_heartbeat_negativo.md      # Stock negativo → rollback
├── ASR_escenario2_heartbeat_negativo_solo_deteccion.md
├── ASR_escenario3_ddos_detectado.md          # DDoS → CEP → bloqueo
├── ASR_escenario3_ddos_detectado_solo_deteccion.md
├── README.md                                 # Guía completa (instalación, ejecución, endpoints)
├── infra/                                    # Infraestructura Kubernetes
│   ├── deploy.sh                             # Orquestador unificado: DEPLOY_TARGET=local|oci
│   ├── setup.sh                              # Script maestro local: Kind + NATS + MongoDB
│   ├── verify.sh                             # Verifica 9 condiciones de salud (local)
│   ├── kind-config.yaml                      # Cluster 3 nodos (1 CP + 2 workers)
│   ├── mongodb-replicaset.yaml               # StatefulSet MongoDB RS (Primary + Secondary)
│   ├── mongodb-rs-init-job.yaml              # Seed: colección inventario con 3 SKUs
│   ├── build-and-load.sh                     # docker build + kind load para todos los servicios
│   └── oci/                                  # Scripts exclusivos para OCI/OKE
│       ├── setup_oke.sh                      # Provisiona cluster OKE + node pool + kubeconfig
│       ├── teardown_oke.sh                   # Elimina todos los recursos OCI
│       ├── ocir_push.sh                      # Build + tag + push de 6 imágenes a OCIR
│       ├── imagepullsecret.sh                # Crea ocir-secret en namespaces ccp/data/messaging
│       ├── deploy_services.sh                # Instala Helm charts + aplica overlay Kustomize
│       ├── verify_oci.sh                     # Verifica pods, LB IPs y health checks en OKE
│       ├── nats-values-oci.yaml              # Helm values NATS con PVC 5Gi (StorageClass oci-bv)
│       └── mongodb-values-oci.yaml           # Helm values MongoDB RS con PVC 50Gi (StorageClass oci-bv)
├── k8s/                                      # Manifiestos de servicios
│   ├── modulo-inventarios.yaml               # NodePort 30090
│   ├── inv-standby.yaml + inv-standby-svc.yaml  # NodePort 30095
│   ├── monitor.yaml                          # NodePort 30091
│   ├── corrector.yaml                        # NodePort 30092
│   ├── validacion-cep.yaml                   # NodePort 30094
│   ├── modulo-seguridad.yaml                 # NodePort 30093
│   ├── log-auditoria.yaml                    # NodePort 30096
│   └── overlays/oci/                         # Kustomize overlay para OKE
│       ├── kustomization.yaml                # Refs OCIR + 3 patches
│       ├── patch-imagepull.yaml              # imagePullSecrets + imagePullPolicy: Always
│       ├── patch-services-lb.yaml            # NodePort → LoadBalancer (INV + CEP)
│       └── patch-remove-nodeselector.yaml    # Elimina nodeSelector (no aplica en OKE)
├── services/                                 # Código fuente (Python 3.11 + FastAPI)
│   ├── modulo_inventarios/                   # VALCOH + HeartBeat + fault injection
│   ├── monitor/                              # Router NATS por tipo de HeartBeat
│   ├── corrector/                            # Rollback + reconciliación + failover
│   ├── validacion_cep/                       # Motor CEP + ventana deslizante
│   ├── modulo_seguridad/                     # Bloqueo de actores + revocación JWT
│   └── log_auditoria/                        # Persistencia forense MongoDB
├── experiments/
│   ├── experiment_a/
│   │   ├── run_experiment_a.py               # 5 casos CP-A1 a CP-A5 (ASR-1)
│   │   └── results_a.json                    # Resultados: 5/5 passed
│   └── experiment_b/
│       ├── run_experiment_b.py               # 4 casos CP-B1 a CP-B4 (ASR-2)
│       └── results_b.json                    # Resultados: 4/4 passed
├── scripts/
│   ├── run_experiments.sh                    # Orquestador: port-forwards + A + B + reporte
│   ├── validate_asrs.py                      # Lee JSONs y genera final_report.json
│   ├── live_dashboard.py                     # Dashboard terminal tiempo real (--demo, --no-portforward)
│   ├── init_inventory.py                     # Seed manual de MongoDB
│   └── final_report.json                     # Reporte ejecutado: 9/9 passed
└── .claude/
    ├── diseño_experimento.md                 # Diseño teórico del experimento
    ├── docs/                                 # Diagramas visuales (Draw.io, PNG, PDF)
    ├── specs/                                # Especificaciones técnicas por área
    └── agents/                               # Guías de rol para agentes de IA
```

---

## Stack tecnológico

| Capa | Tecnología | Notas |
|---|---|---|
| Orquestación local | Kind (Kubernetes IN Docker) | 3 nodos: 1 control-plane + 2 workers |
| Orquestación cloud | OKE (Oracle Kubernetes Engine) | 3 workers VM.Standard.E4.Flex, 2 OCPUs, 8 GB |
| Registry local | `kind load docker-image` | Carga imágenes directamente en el cluster Kind |
| Registry cloud | OCIR (OCI Container Registry) | `<region>.ocir.io/<namespace>/ccp/<servicio>:latest` |
| Broker | NATS JetStream | Helm chart oficial; 3 streams |
| Base de datos | MongoDB 7.0 Replica Set | Primary :27017 / Secondary :27018; campo `SKU` en mayúsculas |
| Storage local | emptyDir / PVC Kind | Sin persistencia real (válido para experimento) |
| Storage cloud | OCI Block Volume (`oci-bv`) | PVCs reales: 50 GB MongoDB, 5 GB NATS |
| Microservicios | Python 3.11 + FastAPI + Uvicorn | motor==3.4.0 + pymongo==4.6.3 (pin necesario) |
| Mensajería async | nats-py==2.7.2 | JetStream con durable consumer + manual_ack=True |
| Acceso MongoDB | motor==3.4.0 | pymongo debe ser 4.6.3 (4.7+ incompatible con motor 3.4) |
| Host access | kubectl port-forward | NodePorts no accesibles desde macOS en Kind; en OKE se usa LB IP |

---

## Casos de prueba

### Experimento A — ASR-1 Disponibilidad

| ID | Nombre | Mecanismo | Criterio | Resultado |
|---|---|---|---|---|
| CP-A1 | Happy path (SELF_TEST_OK) | VALCOH pasa los 3 checks | tipo=SELF_TEST_OK, t_self_test < 300 ms | ✅ 4 ms |
| CP-A2 | Stock negativo detectado | fault-inject stock_negativo | tipo=STOCK_NEGATIVO + evento corrector | ✅ 23.9 ms |
| CP-A3 | Concurrencia detectada | fault-inject estado_concurrente | tipo=ESTADO_CONCURRENTE detectado | ✅ 0.002 ms |
| CP-A4 | Divergencia de reservas | fault-inject divergencia_reservas | tipo=DIVERGENCIA_RESERVAS detectado | ✅ 0.001 ms |
| CP-A5 | Self-test fallido → failover | fault-inject self_test_failed | Monitor activa failover + INV-Standby responde | ✅ |

### Experimento B — ASR-2 Seguridad

| ID | Nombre | Patrón de ataque | Criterio | Resultado |
|---|---|---|---|---|
| CP-B1 | Happy path (sin ataque) | 5 requests normales | 0 falsos positivos, 5×200 | ✅ |
| CP-B2 | DDoS gradual detectado | 15 requests rápidos mismo SKU | any_429=true, actor bloqueado, t_deteccion < 300 ms | ✅ 0.011 ms |
| CP-B3 | DDoS con JWT válido (no bypass) | 15 requests con token válido | JWT no bypasea CEP; 429 igualmente | ✅ 0.009 ms |
| CP-B4 | Umbral de correlación exacto | 12 req (≥2 señales) vs 9 req (1 señal) | 12 → ataque; 9 → no falso positivo | ✅ 0.019 ms |

---

## Convenciones de código

- **Logging**: JSON estructurado via `log_event(event, **kwargs)` en todos los servicios; formato `{"service": ..., "nodo": ..., "event": ..., ...}`
- **MongoDB**: campo SKU en mayúsculas en documentos (`{"SKU": "COCA-COLA-350"}`); queries deben usar `{"SKU": sku}` (case-sensitive)
- **NATS consumers**: siempre `manual_ack=True`; usar `msg.ack()` también en errores para evitar re-delivery infinito
- **Pydantic models**: `model_config = {"extra": "allow"}` en todos los modelos de HeartBeat para compatibilidad entre versiones
- **Fault injection**: endpoint `POST /fault-inject` con body `{"tipo": "stock_negativo"|"divergencia_reservas"|"estado_concurrente"|"self_test_failed"|"none"}`
- **CEP reset**: endpoint `POST /reset` en validacion_cep para limpiar ventana entre casos de prueba
- **Diagramas de secuencia**: usan `autonumber` y bloques `Note over` para explicar lógica interna; tablas de arquitectura con columnas: Momento/Elemento, Decisión/Táctica, Razonamiento
- **Respuestas enmascaradas**: nunca exponer errores internos ni criterios de detección al cliente; usar mensajes genéricos

---

## Ejecución rápida

### Modo local (Kind)

```bash
# 1. Levantar infraestructura (una vez)
DEPLOY_TARGET=local bash infra/deploy.sh
# equivalente a: bash infra/setup.sh && bash infra/build-and-load.sh && kubectl apply -f k8s/

# 2. Correr todos los experimentos
bash scripts/run_experiments.sh
# Genera: scripts/final_report.json

# 3. Dashboard en tiempo real
python3 scripts/live_dashboard.py --no-portforward
python3 scripts/live_dashboard.py --demo  # inyecta fallos automáticamente
```

> **macOS + Kind**: los NodePorts no son accesibles desde el host. El script `run_experiments.sh` levanta `kubectl port-forward` automáticamente.

### Modo OCI (OKE)

```bash
# 1. Exportar credenciales OCI
export OCI_REGION="sa-bogota-1"
export OCI_TENANCY_NAMESPACE="<namespace>"
export OCI_COMPARTMENT_ID="ocid1.compartment.oc1..<id>"
export OCIR_USERNAME="<namespace>/oracleidentitycloudservice/<email>"
export OCIR_PASSWORD="<auth_token>"

# 2. Provisionar OKE, subir imágenes a OCIR y desplegar servicios
DEPLOY_TARGET=oci bash infra/deploy.sh
# equivalente a: setup_oke.sh → ocir_push.sh → deploy_services.sh

# 3. Verificar despliegue
bash infra/oci/verify_oci.sh

# 4. Correr experimentos (mismos scripts, vía port-forward)
bash scripts/run_experiments.sh

# 5. Teardown (elimina todos los recursos OCI)
bash infra/oci/teardown_oke.sh
```

> **Kustomize overlay**: `infra/deploy.sh` en modo OCI aplica `k8s/overlays/oci/` que sustituye imágenes por OCIR, cambia NodePort → LoadBalancer en INV y CEP, y elimina `nodeSelector`.
