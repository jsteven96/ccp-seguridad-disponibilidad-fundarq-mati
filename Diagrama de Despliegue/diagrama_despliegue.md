# Diagrama de Despliegue — CCP · Reto 2

> Renderizable en VS Code con la extensión **Markdown Preview Mermaid Support**, o en [mermaid.live](https://mermaid.live).

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
