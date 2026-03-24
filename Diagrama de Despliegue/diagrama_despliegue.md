# Diagrama de Despliegue — CCP · Reto 2

> Renderizable en VS Code con la extensión **Markdown Preview Mermaid Support**, o en [mermaid.live](https://mermaid.live).

---

## Diagrama de infraestructura — Ambiente PoC

> 3 Worker Nodes `VM.Standard.A1.Flex` (ARM · 2 OCPU · 12 GB RAM c/u). WAF y API Gateway como servicios OCI gestionados en el perímetro. MinIO auto-gestionado en el clúster para el Log de Auditoría.

```mermaid
graph TD
    %% ─────────────────────────────────────────
    %% ACTORES EXTERNOS
    %% ─────────────────────────────────────────
    APP["📱 App Móvil\nTendero / Vendedor\n(HTTPS · JWT)"]
    ATK["🤖 Atacante\nbot / actor suplantado\n(JWT válido)"]
    EQ["👥 Equipo de Seguridad\n(alerta forense)"]

    subgraph OCI_POC["☁  Oracle Cloud Infrastructure — PoC"]

        OCI_WAF_POC["OCI WAF\nreglas básicas\n(IP blocking · geo-blocking)"]
        OCI_LB_POC["OCI Load Balancer\nFlexible · 10 Mbps"]

        subgraph OKE_POC["☸  OKE Cluster — 3 × VM.Standard.A1.Flex · 2 OCPU · 12 GB RAM · ARM Ampere"]

            subgraph NODE1_POC["⬡ Worker Node 1 — ingress + security"]
                subgraph NS_ING_POC["namespace: ingress"]
                    WAF_POC["WAF sidecar"]
                    GW_POC["API Gateway · 2 réplicas\nJWT auth · rate limiting HTTP"]
                    WAF_POC --> GW_POC
                end
                subgraph NS_SEC_POC["namespace: security"]
                    VS_POC["Validación Seguridad + CEP · 2 réplicas\nventana 60 s · ≥2 señales"]
                    SEG_POC["Módulo de Seguridad · 2 réplicas\nJWT revocation · IP blocklist"]
                end
            end

            subgraph NODE2_POC["⬡ Worker Node 2 — business + availability + notifications"]
                subgraph NS_BUS_POC["namespace: business"]
                    GO_POC["Gestor de Órdenes · 2 réplicas\n[orquestador]"]
                    INV_POC["Módulo de Inventarios · 2 réplicas\n[HeartBeat producer]"]
                    GP_POC["Gestor de Pedidos · 2 réplicas"]
                end
                subgraph NS_AVL_POC["namespace: availability"]
                    MON_POC["Monitor · 2 réplicas\n[HeartBeat consumer]"]
                    CORR_POC["Corrector · 2 réplicas\n[Rollback coordinator]"]
                end
                subgraph NS_NOT_POC["namespace: notifications"]
                    NOTIF_POC["Servicio de Notificaciones · 2 réplicas"]
                end
            end

            subgraph NODE3_POC["⬡ Worker Node 3 — data + messaging (StatefulSets)"]
                subgraph NS_MSG_POC["namespace: messaging"]
                    NATS_POC["NATS / JetStream\nStatefulSet · 1 nodo\nseguridad.alertas"]
                end
                subgraph NS_DAT_POC["namespace: data"]
                    MONGO_POC["MongoDB\nStatefulSet · 1 nodo\n(sin réplicas en PoC)"]
                    MINIO_POC["MinIO\nStatefulSet · 1 nodo\n→ Object Storage local"]
                end
                subgraph NS_OBS_POC["namespace: observability"]
                    LOG_POC["Log de Auditoría · 2 réplicas"]
                end
            end

        end
    end

    %% ── FLUJOS ──────────────────────────────
    APP -->|HTTPS · JWT| OCI_WAF_POC
    ATK -->|HTTPS · JWT válido| OCI_WAF_POC
    OCI_WAF_POC --> OCI_LB_POC
    OCI_LB_POC --> WAF_POC
    GW_POC -->|orden autenticada| GO_POC

    GO_POC -->|REST: validar orden| VS_POC
    VS_POC -->|"✓ / ✗ resultado"| GO_POC
    GO_POC -->|REST: reservar stock| INV_POC
    GO_POC -->|REST: crear pedido| GP_POC

    INV_POC -->|"webhook HeartBeat\n< 300 ms"| MON_POC
    MON_POC -->|consulta orden| GO_POC
    MON_POC -->|marca problemática| GP_POC
    GP_POC -->|REST: ejecutar corrección| CORR_POC
    CORR_POC -->|rollback reserva| INV_POC
    CORR_POC -->|cancelar pedido| GP_POC

    VS_POC -->|seguridad.alertas| NATS_POC
    NATS_POC -->|seguridad.alertas| SEG_POC
    NATS_POC -->|seguridad.alertas| LOG_POC
    SEG_POC -->|alerta forense| EQ
    LOG_POC -->|persiste evento| MINIO_POC

    GO_POC -->|REST: notificar| NOTIF_POC
    NOTIF_POC -->|push / respuesta accionable| APP

    GO_POC -.-|órdenes DB| MONGO_POC
    INV_POC -.-|inventario DB| MONGO_POC
    GP_POC -.-|pedidos DB| MONGO_POC
    SEG_POC -.-|blocklist DB| MONGO_POC

    %% ── ESTILOS ─────────────────────────────
    classDef external fill:#f0f0f0,stroke:#999,color:#333
    classDef oci_svc fill:#fff8e1,stroke:#f9a825,color:#6d4c00
    classDef ingress fill:#e8f4f8,stroke:#2196F3,color:#0d47a1
    classDef security fill:#fce4ec,stroke:#e53935,color:#b71c1c
    classDef business fill:#e8f5e9,stroke:#43a047,color:#1b5e20
    classDef availability fill:#fff3e0,stroke:#fb8c00,color:#e65100
    classDef messaging fill:#f3e5f5,stroke:#8e24aa,color:#4a148c
    classDef data fill:#e3f2fd,stroke:#1e88e5,color:#0d47a1
    classDef observability fill:#fafafa,stroke:#757575,color:#212121

    class APP,ATK,EQ external
    class OCI_WAF_POC,OCI_LB_POC oci_svc
    class WAF_POC,GW_POC ingress
    class VS_POC,SEG_POC security
    class GO_POC,INV_POC,GP_POC business
    class MON_POC,CORR_POC availability
    class NOTIF_POC ingress
    class NATS_POC messaging
    class MONGO_POC,MINIO_POC data
    class LOG_POC observability
```

---

## Diagrama de infraestructura — Ambiente Producción

> 5 Worker Nodes `VM.Standard.E4.Flex` (x86 · 4 OCPU · 32 GB RAM c/u). OCI API Gateway y OCI WAF gestionados en perímetro. OCI Object Storage reemplaza MinIO. MongoDB en Replica Set de 3 nodos. NATS en StatefulSet de 3 nodos.

```mermaid
graph TD
    %% ─────────────────────────────────────────
    %% ACTORES EXTERNOS
    %% ─────────────────────────────────────────
    APP2["📱 App Móvil\nTendero / Vendedor\n(HTTPS · JWT)"]
    ATK2["🤖 Atacante\nbot / actor suplantado\n(JWT válido)"]
    EQ2["👥 Equipo de Seguridad\n(alerta forense)"]

    subgraph OCI_PROD["☁  Oracle Cloud Infrastructure — Producción"]

        OCI_WAF_PROD["OCI WAF\nreglas OWASP completas\n(IP blocking · geo · rate)"]
        OCI_APIGW["OCI API Gateway\nJWT auth · rate limiting\npolíticas OCI IAM"]
        OCI_OS["OCI Object Storage\nLog Auditoría inmutable\nStandard tier"]

        subgraph OKE_PROD["☸  OKE Cluster — 5 × VM.Standard.E4.Flex · 4 OCPU · 32 GB RAM · AMD EPYC"]

            subgraph NODE12_PROD["⬡ Worker Nodes 1 + 2 — ingress + security (dedicados · HA)"]
                subgraph NS_SEC_PROD["namespace: security"]
                    VS_PROD["Validación Seguridad + CEP · 3-5 réplicas\nventana 60 s · ≥2 señales"]
                    SEG_PROD["Módulo de Seguridad · 2 réplicas\nJWT revocation · IP blocklist"]
                end
            end

            subgraph NODE34_PROD["⬡ Worker Nodes 3 + 4 — business + availability + notifications"]
                subgraph NS_BUS_PROD["namespace: business"]
                    GO_PROD["Gestor de Órdenes · 3-5 réplicas\n[orquestador]"]
                    INV_PROD["Módulo de Inventarios · 3-5 réplicas\n[HeartBeat producer]"]
                    GP_PROD["Gestor de Pedidos · 3-5 réplicas"]
                end
                subgraph NS_AVL_PROD["namespace: availability"]
                    MON_PROD["Monitor · 2 réplicas\n[HeartBeat consumer]"]
                    CORR_PROD["Corrector · 2 réplicas\n[Rollback coordinator]"]
                end
                subgraph NS_NOT_PROD["namespace: notifications"]
                    NOTIF_PROD["Servicio de Notificaciones · 2 réplicas"]
                end
            end

            subgraph NODE5_PROD["⬡ Worker Node 5 — data + messaging (StatefulSets)"]
                subgraph NS_MSG_PROD["namespace: messaging"]
                    NATS_PROD["NATS / JetStream\nStatefulSet · 3 nodos\nseguridad.alertas · persistencia JetStream"]
                end
                subgraph NS_DAT_PROD["namespace: data"]
                    MONGO_PROD["MongoDB\nStatefulSet · Replica Set 3 nodos"]
                end
                subgraph NS_OBS_PROD["namespace: observability"]
                    LOG_PROD["Log de Auditoría · 2 réplicas"]
                end
            end

        end
    end

    %% ── FLUJOS ──────────────────────────────
    APP2 -->|HTTPS · JWT| OCI_WAF_PROD
    ATK2 -->|HTTPS · JWT válido| OCI_WAF_PROD
    OCI_WAF_PROD --> OCI_APIGW
    OCI_APIGW -->|orden autenticada| GO_PROD

    GO_PROD -->|REST: validar orden| VS_PROD
    VS_PROD -->|"✓ / ✗ resultado"| GO_PROD
    GO_PROD -->|REST: reservar stock| INV_PROD
    GO_PROD -->|REST: crear pedido| GP_PROD

    INV_PROD -->|"webhook HeartBeat\n< 300 ms"| MON_PROD
    MON_PROD -->|consulta orden| GO_PROD
    MON_PROD -->|marca problemática| GP_PROD
    GP_PROD -->|REST: ejecutar corrección| CORR_PROD
    CORR_PROD -->|rollback reserva| INV_PROD
    CORR_PROD -->|cancelar pedido| GP_PROD

    VS_PROD -->|seguridad.alertas| NATS_PROD
    NATS_PROD -->|seguridad.alertas| SEG_PROD
    NATS_PROD -->|seguridad.alertas| LOG_PROD
    SEG_PROD -->|alerta forense| EQ2
    LOG_PROD -->|persiste evento| OCI_OS

    GO_PROD -->|REST: notificar| NOTIF_PROD
    NOTIF_PROD -->|push / respuesta accionable| APP2

    GO_PROD -.-|órdenes DB| MONGO_PROD
    INV_PROD -.-|inventario DB| MONGO_PROD
    GP_PROD -.-|pedidos DB| MONGO_PROD
    SEG_PROD -.-|blocklist DB| MONGO_PROD

    %% ── ESTILOS ─────────────────────────────
    classDef external fill:#f0f0f0,stroke:#999,color:#333
    classDef oci_svc fill:#fff8e1,stroke:#f9a825,color:#6d4c00
    classDef security fill:#fce4ec,stroke:#e53935,color:#b71c1c
    classDef business fill:#e8f5e9,stroke:#43a047,color:#1b5e20
    classDef availability fill:#fff3e0,stroke:#fb8c00,color:#e65100
    classDef messaging fill:#f3e5f5,stroke:#8e24aa,color:#4a148c
    classDef data fill:#e3f2fd,stroke:#1e88e5,color:#0d47a1
    classDef observability fill:#fafafa,stroke:#757575,color:#212121

    class APP2,ATK2,EQ2 external
    class OCI_WAF_PROD,OCI_APIGW,OCI_OS oci_svc
    class VS_PROD,SEG_PROD security
    class GO_PROD,INV_PROD,GP_PROD business
    class MON_PROD,CORR_PROD availability
    class NOTIF_PROD ingress
    class NATS_PROD messaging
    class MONGO_PROD data
    class LOG_PROD observability
```

---

## Diferencias clave entre ambientes

| Aspecto | PoC | Producción |
|---|---|---|
| **Shape** | `A1.Flex` ARM · 2 OCPU · 12 GB | `E4.Flex` x86 · 4 OCPU · 32 GB |
| **Worker Nodes** | 3 (rol mixto) | 5 (nodos 1-2 dedicados a ingress+security) |
| **API Gateway** | Custom Deployment en OKE | **OCI API Gateway** gestionado |
| **WAF** | Sidecar en pod + OCI WAF básico | **OCI WAF** reglas OWASP completas |
| **Object Storage** | MinIO StatefulSet en Node 3 | **OCI Object Storage** gestionado |
| **MongoDB** | StatefulSet 1 nodo (sin HA) | StatefulSet **Replica Set 3 nodos** |
| **NATS** | StatefulSet 1 nodo | StatefulSet **3 nodos** (HA + JetStream) |
| **Réplicas por servicio** | 2 (mínimo) | 2–5 (con HPA activo) |

---

---

```mermaid
graph TD
    %% ─────────────────────────────────────────
    %% ACTORES EXTERNOS
    %% ─────────────────────────────────────────
    APP["📱 App Móvil\nTendero / Vendedor\n(HTTPS · JWT)"]
    ATK["🤖 Atacante\nbot / actor suplantado\n(JWT válido)"]
    EQ["👥 Equipo de Seguridad\n(alerta forense)"]

    %% ─────────────────────────────────────────
    %% CLUSTER KUBERNETES
    %% ─────────────────────────────────────────
    subgraph K8S["☸  Kubernetes Cluster — minikube / k3d  →  Oracle Kubernetes Engine (OKE)"]

        %% ── INGRESS ──────────────────────────
        subgraph NS_ING["namespace: ingress"]
            WAF["WAF\nsidecar"]
            GW["API Gateway\nDeployment · 2-3 réplicas\nJWT auth · rate limiting HTTP"]
            WAF --> GW
        end

        %% ── SECURITY ─────────────────────────
        subgraph NS_SEC["namespace: security"]
            VS["Validación de Seguridad\n+ Motor CEP\nDeployment · 2-5 réplicas\nventana 60 s · ≥2 señales"]
            SEG["Módulo de Seguridad\nDeployment · 2 réplicas\nJWT revocation · IP blocklist"]
        end

        %% ── BUSINESS ─────────────────────────
        subgraph NS_BUS["namespace: business"]
            GO["Gestor de Órdenes\nDeployment · 2-5 réplicas\n[orquestador]"]
            INV["Módulo de Inventarios\nDeployment · 2-5 réplicas\n[HeartBeat webhook producer]"]
            GP["Gestor de Pedidos\nDeployment · 2-5 réplicas"]
        end

        %% ── AVAILABILITY ─────────────────────
        subgraph NS_AVL["namespace: availability"]
            MON["Monitor\nDeployment · 2 réplicas\n[HeartBeat webhook consumer]"]
            CORR["Corrector\nDeployment · 2 réplicas\n[Rollback coordinator]"]
        end

        %% ── MESSAGING ────────────────────────
        subgraph NS_MSG["namespace: messaging"]
            NATS["NATS / JetStream\nStatefulSet · 1-3 nodos\n─────────────────\nseguridad.alertas"]
        end

        %% ── NOTIFICATIONS ────────────────────
        subgraph NS_NOT["namespace: notifications"]
            NOTIF["Servicio de Notificaciones\nDeployment · 2 réplicas"]
        end

        %% ── DATA ─────────────────────────────
        subgraph NS_DAT["namespace: data"]
            MONGO["MongoDB\nStatefulSet · Replica Set 3 nodos"]
            MINIO["MinIO  →  OCI Object Storage\nStatefulSet local / servicio OCI"]
        end

        %% ── OBSERVABILITY ────────────────────
        subgraph NS_OBS["namespace: observability"]
            LOG["Log de Auditoría\nDeployment · 2 réplicas"]
        end

    end

    %% ─────────────────────────────────────────
    %% FLUJOS — ENTRADA
    %% ─────────────────────────────────────────
    APP -->|HTTPS · JWT| WAF
    ATK -->|HTTPS · JWT válido| WAF
    GW -->|orden autenticada| GO

    %% ─────────────────────────────────────────
    %% FLUJO A — VALIDACIÓN Y PROCESAMIENTO (ASR 1 y 2)
    %% ─────────────────────────────────────────
    GO -->|"REST: validar orden"| VS
    VS -->|"✓ / ✗ resultado"| GO
    GO -->|"REST: reservar stock"| INV
    GO -->|"REST: crear pedido"| GP

    %% ─────────────────────────────────────────
    %% FLUJO B — HEARTBEAT STOCK NEGATIVO (ASR 2)
    %% ─────────────────────────────────────────
    INV -->|"webhook HeartBeat\nstock estado · < 300 ms"| MON
    MON -->|"consulta orden problemática"| GO
    MON -->|"marca orden problemática"| GP
    GP -->|"REST: ejecutar corrección"| CORR
    CORR -->|"rollback reserva"| INV
    CORR -->|"cancelar pedido"| GP

    %% ─────────────────────────────────────────
    %% FLUJO C — ATAQUE DETECTADO (ASR 3)
    %% ─────────────────────────────────────────
    VS -->|"seguridad.alertas [NATS]"| NATS
    NATS -->|seguridad.alertas| SEG
    NATS -->|seguridad.alertas| LOG
    SEG -->|alerta forense| EQ
    LOG -->|persiste evento| MINIO

    %% ─────────────────────────────────────────
    %% FLUJO D — NOTIFICACIÓN AL TENDERO
    %% ─────────────────────────────────────────
    GO -->|"REST: notificar"| NOTIF
    NOTIF -->|"push / respuesta accionable"| APP

    %% ─────────────────────────────────────────
    %% CAPA DE DATOS
    %% ─────────────────────────────────────────
    GO -.-|"órdenes DB"| MONGO
    INV -.-|"inventario DB"| MONGO
    GP -.-|"pedidos DB"| MONGO
    SEG -.-|"blocklist DB"| MONGO

    %% ─────────────────────────────────────────
    %% ESTILOS
    %% ─────────────────────────────────────────
    classDef external fill:#f0f0f0,stroke:#999,color:#333
    classDef ingress fill:#e8f4f8,stroke:#2196F3,color:#0d47a1
    classDef security fill:#fce4ec,stroke:#e53935,color:#b71c1c
    classDef business fill:#e8f5e9,stroke:#43a047,color:#1b5e20
    classDef availability fill:#fff3e0,stroke:#fb8c00,color:#e65100
    classDef messaging fill:#f3e5f5,stroke:#8e24aa,color:#4a148c
    classDef data fill:#e3f2fd,stroke:#1e88e5,color:#0d47a1
    classDef observability fill:#fafafa,stroke:#757575,color:#212121

    class APP,ATK,EQ external
    class WAF,GW ingress
    class VS,SEG security
    class GO,INV,GP business
    class MON,CORR availability
    class NATS messaging
    class NOTIF ingress
    class MONGO,MINIO data
    class LOG observability
```

---

## Leyenda de colores

| Color | Namespace |
|---|---|
| Gris | Actores externos |
| Azul claro | `ingress` — API Gateway · WAF · Notificaciones |
| Rojo claro | `security` — Validación CEP · Módulo de Seguridad |
| Verde claro | `business` — Gestor de Órdenes · Inventarios · Pedidos |
| Naranja claro | `availability` — Monitor · Corrector |
| Morado claro | `messaging` — NATS / JetStream (solo flujo de seguridad) |
| Azul medio | `data` — MongoDB · MinIO / OCI Object Storage |
| Gris claro | `observability` — Log de Auditoría |

---

## Flujos principales

| Flujo | ASR | Descripción |
|---|---|---|
| **A** | ASR 1 / 2 | Orden autenticada → GO orquesta → validación VS (sync REST) → reserva INV + pedido GP |
| **B** | ASR 2 | HeartBeat webhook stock negativo → Monitor → corrección → Corrector → rollback < 300 ms |
| **C** | ASR 3 | Ataque DDoS detectado por CEP → publicación NATS → bloqueo · alerta · log forense |
| **D** | ASR 1 / 2 | Notificación al tendero: confirmación o error accionable |

## Decisiones de transporte

| Interacción | Protocolo | Justificación |
|---|---|---|
| GO → VS, GO → INV, GO → GP | REST síncrono | Flujo de negocio principal; simplicidad y trazabilidad |
| INV → MON (HeartBeat) | Webhook HTTP POST | Baja latencia sin intermediario; < 300 ms requeridos |
| GP → CORR (corrección) | REST síncrono | Rollback coordinado; respuesta confirmada necesaria |
| VS → NATS → SEG, LOG | NATS/JetStream pub/sub | Fan-out asíncrono a múltiples consumidores; solo flujo de seguridad |

---

## Infraestructura y Costos — Oracle Cloud Infrastructure (OCI)

> Precios en USD/mes. Basados en tarifa pública OCI (región `us-ashburn-1`). A confirmar con el [OCI Cost Estimator](https://cloud.oracle.com/cost-estimator).

---

### Comparación de ambientes

| Dimensión | PoC | Producción |
|---|---|---|
| **Propósito** | Validación funcional del diseño | Operación real 7×24×365 |
| **Shape de nodo** | `VM.Standard.A1.Flex` (ARM Ampere) | `VM.Standard.E4.Flex` (AMD EPYC) |
| **Configuración por nodo** | 2 OCPU · 12 GB RAM | 4 OCPU · 32 GB RAM |
| **Nodos worker OKE** | 3 nodos | 5 nodos |
| **OCI API Gateway** | Gestionado OCI (bajo tráfico) | Gestionado OCI (alta disponibilidad) |
| **OCI WAF** | Habilitado (reglas básicas) | Habilitado (reglas avanzadas) |
| **NATS / JetStream** | 1 nodo StatefulSet en clúster | 3 nodos StatefulSet en clúster |
| **MongoDB** | StatefulSet 1 nodo (sin réplicas) | StatefulSet Replica Set 3 nodos |
| **OCI Object Storage** | Standard tier (~10 GB/mes) | Standard tier (~50 GB/mes) |
| **Load Balancer** | Flexible (10 Mbps) | Flexible (100 Mbps) |
| **Block Volumes** | 150 GB total | 500 GB total |

---

### Descripción de shapes seleccionados

| Shape | Arquitectura | OCPU | RAM | Precio OCPU/h | Precio GB RAM/h | Justificación |
|---|---|---|---|---|---|---|
| `VM.Standard.A1.Flex` | ARM Ampere | 1–80 | 1–512 GB | $0.010 | $0.0015 | Shape más barato de OCI; ideal para PoC; compatible con imágenes Docker ARM64 |
| `VM.Standard.E4.Flex` | AMD EPYC Milan | 1–64 | 1–1024 GB | $0.025 | $0.0015 | Buena relación precio/rendimiento x86_64; sin cambio de imagen respecto a ambientes locales (minikube) |

> `A1.Flex` también tiene **Always Free Tier**: 4 OCPU + 24 GB RAM total entre instancias — suficiente para un nodo PoC sin costo.

---

### Desglose de costos — PoC

| Componente | Configuración | Cálculo | Costo/mes |
|---|---|---|---|
| **OKE Worker Nodes** | 3 × `A1.Flex` (2 OCPU · 12 GB) | 3 × (2×$0.010 + 12×$0.0015) × 720 h | ~$82 |
| **OCI API Gateway** | ~500 K req/mes | Primero 1 M gratis | $0 |
| **OCI WAF** | Reglas básicas, tráfico bajo | ~$10 fijo + $0.0025/10K req | ~$12 |
| **OCI Object Storage** | 10 GB Standard | $0.0255/GB/mes | ~$1 |
| **Load Balancer Flexible** | 10 Mbps | Tarifa fija | ~$10 |
| **Block Volumes** | 150 GB (MongoDB + NATS) | $0.0255/GB/mes | ~$4 |
| **NATS / JetStream** | 1 Pod en clúster | Incluido en nodos | $0 |
| **MongoDB** | 1 Pod StatefulSet | Incluido en nodos + Block Volume | $0 |
| **Total estimado PoC** | | | **~$109/mes** |

---

### Desglose de costos — Producción

| Componente | Configuración | Cálculo | Costo/mes |
|---|---|---|---|
| **OKE Worker Nodes** | 5 × `E4.Flex` (4 OCPU · 32 GB) | 5 × (4×$0.025 + 32×$0.0015) × 720 h | ~$532 |
| **OCI API Gateway** | ~5 M req/mes | $3.00/M req (primero 1 M gratis) | ~$12 |
| **OCI WAF** | Reglas avanzadas, tráfico alto | ~$25 fijo + uso | ~$30 |
| **OCI Object Storage** | 50 GB Standard | $0.0255/GB/mes | ~$2 |
| **Load Balancer Flexible** | 100 Mbps | Tarifa flexible | ~$18 |
| **Block Volumes** | 500 GB (MongoDB 3 nodos + NATS 3 nodos) | $0.0255/GB/mes | ~$13 |
| **NATS / JetStream** | 3 Pods StatefulSet | Incluido en nodos | $0 |
| **MongoDB Replica Set** | 3 Pods StatefulSet | Incluido en nodos + Block Volume | $0 |
| **Total estimado Producción** | | | **~$607/mes** |

> ⚠️ Producción puede optimizarse con **Reserved Instances OCI** (compromiso 1 año): descuento ~36% sobre tarifa on-demand → ~$388/mes.

---

### Decisiones de infraestructura

| # | Decisión | Detalle | Justificación |
|---|---|---|---|
| **INF-01** | Shape ARM para PoC | `VM.Standard.A1.Flex` (2 OCPU / 12 GB) | Es el shape más económico de OCI; imágenes Docker multi-arch disponibles para todos los servicios del stack (NATS, MongoDB, servicios custom) |
| **INF-02** | Shape x86 para Producción | `VM.Standard.E4.Flex` (4 OCPU / 32 GB) | Compatibilidad garantizada con cualquier imagen OCI sin preocuparse por soporte ARM; mejor perfil CPU para cargas de validación CEP intensivas |
| **INF-03** | OCI API Gateway gestionado | Reemplaza el API Gateway custom en Producción | Elimina la operación del Deployment propio; integra nativamente con OCI WAF, IAM y políticas de rate limiting sin configuración adicional |
| **INF-04** | OCI WAF en perímetro | Habilitado en ambos ambientes | Protección DDoS volumétrica de capa HTTP/red antes de llegar al clúster; en PoC con reglas básicas (IP blocking, geo-blocking); en Producción con reglas OWASP completas |
| **INF-05** | OCI Object Storage para Log de Auditoría | Reemplaza MinIO en Producción | Almacenamiento inmutable gestionado, sin StatefulSet que operar; costo mínimo (~$0.025/GB/mes); cumple el requisito forense de persistencia independiente |
| **INF-06** | MongoDB auto-gestionado en OKE | StatefulSet con Block Volumes OCI | Evita el costo de OCI Database Service (~$200+/mes adicionales); suficiente para el volumen académico/PoC y escalable en Producción |
| **INF-07** | NATS auto-gestionado en OKE | StatefulSet, sin servicio OCI equivalente | OCI no ofrece NATS como servicio gestionado; JetStream en StatefulSet es suficiente para el único topic `seguridad.alertas` |
| **INF-08** | Reserved Instances en Producción | Compromiso 1 año sobre nodos E4.Flex | Reduce ~36% el costo de compute; aplicable cuando el diseño esté estabilizado post-PoC |
| **INF-09** | Portabilidad minikube → OKE | Mismos manifiestos K8s; swap de StorageClass y servicios OCI | La única diferencia entre PoC local y nube es: `StorageClass` (local-path → oci-bv), `MinIO` → OCI Object Storage, `WAF sidecar` → OCI WAF, `API GW custom` → OCI API Gateway |
