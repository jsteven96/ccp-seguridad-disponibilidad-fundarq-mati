# Reto 2 - Disponibilidad y Seguridad

## Objetivo
Diseñar una arquitectura de software que cubra dos atributos de calidad primordiales:
- **Disponibilidad**
- **Seguridad**

El entregable principal es un **diagrama de despliegue** que refleje los componentes, técnicas y tácticas adoptadas por el equipo.

---

## Contexto del sistema — CCP (Compañía Comercializadora de Productos)

CCP es una empresa de distribución y venta de productos de consumo masivo con presencia en **5 países**, cada uno con ~6 grandes bodegas distribuidas geográficamente.

Sus tres áreas de operación:
1. **Compra y almacenamiento** de productos a fabricantes
2. **Venta** a grandes superficies, supermercados, autoservicios y tiendas
3. **Entrega** de productos a los establecimientos

### Nuevo sistema (alcance del reto)
Aplicación móvil para la fuerza de ventas y módulo de autoservicio para tenderos:

| Actor | Funcionalidades |
|---|---|
| **Vendedor** | Consulta de productos e inventario en tiempo real · creación de pedidos (reserva cantidades) · registro de visitas · ruta diaria optimizada con tiempos de desplazamiento |
| **Tendero** | Pedidos por cuenta propia sin vendedor · seguimiento de estado del pedido · tracking del camión de entrega |

**Requerimientos no funcionales del enunciado:**
- Disponibilidad **7×24×365** (operación en múltiples países y zonas horarias)
- **Seguridad**: aislamiento de datos por tendero/vendedor · no impersonación · JWT
- **Throughput objetivo:** 25 pedidos/segundo con validación de inventario en tiempo real
- **Concurrencia:** 1.000 usuarios activos simultáneos (Vendedores + Tenderos)

---

## Escenarios Arquitecturalmente Significativos (ASRs)

### ASR 1 — Happy Path (referencia de flujo exitoso)
Orden válida, inventario suficiente, validación de seguridad aprobada. Monitor y Corrector activos pero sin eventos que procesar. Ver: `Diagramas de secuencia/ASR_escenario1_happy_path.md`

### ASR 2 — Disponibilidad: Detección de stock negativo via HeartBeat ⭐
| Campo | Valor |
|---|---|
| **Actor** | Vendedor |
| **Estímulo** | Reserva de un producto |
| **Artefacto** | Módulo de Inventarios |
| **Ambiente** | Operación normal · 5 países · 7×24×365 · 1.000 usuarios activos · 25 pedidos/s |
| **Respuesta esperada** | El sistema detecta en **< 300 ms** que se permitió reservar un producto agotado |
| **Trade-off** | Latencia (prioridad alta) — las validaciones adicionales de coherencia de stock pueden afectarla |

**Táctica:** HeartBeat. Inventario publica stock negativo → Monitor detecta → Corrector hace rollback paralelo → tendero notificado con mensaje accionable.
Ver: `Diagramas de secuencia/ASR_escenario2_heartbeat_negativo.md`

### ASR 3 — Seguridad: Detección de DDoS de capa de negocio via CEP ⭐
| Campo | Valor |
|---|---|
| **Actor** | Tendero (suplantado / bot) |
| **Estímulo** | Solicitudes masivas de pedidos o inventario |
| **Artefacto** | Gestor de Pedidos / Módulo de Inventarios |
| **Ambiente** | Operación normal · 5 países · 7×24×365 · 1.000 usuarios activos · 25 pedidos/s |
| **Respuesta esperada** | El sistema identifica el patrón como DDoS de capa de negocio en **< 300 ms** · bloquea reservas falsas · protege el inventario para tenderos legítimos |
| **Trade-off** | Disponibilidad (prioridad alta) — el sistema de inventarios puede aislarse temporalmente para mitigar la saturación |

**Táctica:** Analizador CEP con ventana deslizante de 60 s; ≥2 señales (rate, concentración SKU, tasa de cancelación) → bloqueo + JWT revocado + IP bloqueada + alerta forense.
Ver: `Diagramas de secuencia/ASR_escenario3_ddos_detectado.md`

---

## Atributos de Calidad

### Disponibilidad
| Táctica | Componente | Detalle |
|---|---|---|
| **Detectar — HeartBeat** | Módulo de Inventarios | Publica continuamente su estado; emite evento de stock negativo al detectarlo internamente |
| **Detectar — Monitor** | Monitor | Suscrito al bus de eventos (Kafka); escucha HeartBeat del inventario de forma pasiva |
| **Corregir — Rollback coordinado** | Corrector | Revierte reserva en Inventarios y cancela pedido en Gestor de Pedidos en paralelo |
| **Enmascarar — Respuesta controlada** | Servicio de Notificaciones | El tendero recibe mensaje accionable con cantidad disponible; nunca ve trazas internas |
| **Prevenir — Bloqueo temprano** | Validación de Seguridad | Al detectar ataque DDoS, el Módulo de Inventarios nunca recibe la carga artificial |

### Seguridad
| Táctica | Componente | Detalle |
|---|---|---|
| **Detectar — Analizador CEP** | Validación de Seguridad | Ventana deslizante 60 s; correlaciona ≥2 señales: rate de órdenes, concentración SKU, tasa de cancelación |
| **Resistir — Perímetro lógico** | Validación de Seguridad | Ninguna orden llega a Inventarios ni Pedidos sin pasar la validación |
| **Reaccionar — Revocar acceso** | Módulo de Seguridad | Revoca JWT del actor; bloquea IP temporalmente (24 h con revisión) |
| **Reaccionar — Informar actores** | Módulo de Seguridad → Equipo de Seguridad | Alerta con payload forense (actor_id, IP, timestamp, SKU objetivo, score de riesgo) |
| **Recuperarse — Log de eventos** | Log de Auditoría | Independiente del Módulo de Seguridad; persiste aunque el módulo falle; almacenado en object storage |
| **Limitar exposición** | Gestor de Órdenes | Respuesta genérica 429 al atacante; no se revelan criterios del CEP |

---

## Stack Tecnológico Definido

| Capa | Tecnología | Justificación |
|---|---|---|
| **Orquestación** | Kubernetes (local: minikube / k3d → nube: OKE) | PoC local portátil a Oracle Kubernetes Engine |
| **Service Bus** | **NATS / JetStream** | Open source Apache 2.0; usado exclusivamente para el flujo de alertas de seguridad (CEP); JetStream provee persistencia para la ventana deslizante; mucho más liviano que Kafka |
| **Base de datos** | **MongoDB** | Flexibilidad de schema para órdenes y pedidos; escalado horizontal |
| **Log de Auditoría** | **Object Storage** (Oracle Object Storage en nube / MinIO en local) | Almacenamiento barato e inmutable para eventos forenses |
| **API Gateway** | Propio (desplegado en K8s) | Inicialmente custom; migrable a OCI API Gateway en producción |
| **WAF** | Integrado en API Gateway (local) / OCI WAF (nube) | Protección de capa HTTP/red |

---

## Componentes de la Arquitectura

### Servicios de Negocio

| Componente | Responsabilidad | Réplicas |
|---|---|---|
| **Gestor de Órdenes** | Punto de entrada para órdenes; orquesta el flujo (valida → reserva → pedido); registra log de correcciones | 2–5 |
| **Validación de Seguridad** | Evalúa cada orden con el motor CEP (REST síncrono); publica alertas de ataque a NATS | 2–5 |
| **Módulo de Inventarios** | Reserva stock; detecta negativos internamente; emite HeartBeat webhook al Monitor | 2–5 |
| **Gestor de Pedidos** | Registra pedidos, asigna ID, programa despacho; emite evento de corrección al Corrector | 2–5 |

### Tácticas de Disponibilidad

| Componente | Responsabilidad | Réplicas |
|---|---|---|
| **Monitor** | Suscrito a Kafka (topic: heartbeat); correlaciona SKU afectado con orden; notifica al Gestor de Pedidos | 2 |
| **Corrector** | Recibe evento del Gestor de Pedidos; ejecuta rollback paralelo en Inventarios y Pedidos | 2 |

### Tácticas de Seguridad

| Componente | Responsabilidad | Réplicas |
|---|---|---|
| **Módulo de Seguridad** | Revoca JWT; gestiona lista de bloqueo de IPs; alerta al equipo de seguridad | 2 |
| **Log de Auditoría** | Servicio que persiste eventos de seguridad y correcciones en Object Storage | 2 |

### Infraestructura y Soporte

| Componente | Tecnología | Responsabilidad |
|---|---|---|
| **API Gateway** | Custom (K8s) | Autenticación JWT, rate limiting HTTP, punto de entrada externo |
| **WAF** | Sidecar / OCI WAF | Protección DDoS volumétrico de capa de red |
| **Apache Kafka** | StatefulSet (3 brokers) | Bus de eventos: HeartBeat, correcciones, eventos de seguridad |
| **MongoDB** | StatefulSet (replica set 3 nodos) | Persistencia de órdenes, pedidos e inventario |
| **MinIO** | StatefulSet (local) / OCI Object Storage (nube) | Log de Auditoría inmutable |
| **Servicio de Notificaciones** | Deployment | Notifica al tendero en dos momentos: orden recibida y resultado final |

---

## Propuesta de Namespaces en Kubernetes

```
cluster/
├── namespace: ingress
│   ├── api-gateway              (Deployment 2–3 réplicas + Service LoadBalancer + Ingress)
│   └── waf                      (sidecar del api-gateway o DaemonSet)
│
├── namespace: business
│   ├── gestor-ordenes           (Deployment 2–5 réplicas)
│   ├── modulo-inventarios       (Deployment 2–5 réplicas)
│   └── gestor-pedidos           (Deployment 2–5 réplicas)
│
├── namespace: security
│   ├── validacion-seguridad     (Deployment 2–5 réplicas — motor CEP incluido)
│   └── modulo-seguridad         (Deployment 2 réplicas — JWT revocation, IP blocklist)
│
├── namespace: availability
│   ├── monitor                  (Deployment 2 réplicas — consumer Kafka heartbeat topic)
│   └── corrector                (Deployment 2 réplicas — consumer Kafka correction topic)
│
├── namespace: messaging
│   └── nats                     (StatefulSet 1-3 nodos · JetStream habilitado · topic: seguridad.alertas)
│
├── namespace: notifications
│   └── servicio-notificaciones  (Deployment 2 réplicas)
│
├── namespace: data
│   ├── mongodb                  (StatefulSet replica set 3 nodos)
│   └── minio                    (StatefulSet — solo en local; reemplazado por OCI OS en nube)
│
└── namespace: observability
    └── log-auditoria            (Deployment 2 réplicas — escribe a MinIO / OCI Object Storage)
```

### Flujo de interacciones entre namespaces

```
[Cliente Móvil]
    └──► [ingress/api-gateway]  (JWT auth · rate limiting HTTP)
              └──► [business/gestor-ordenes]  ←── Orquestador de todo el flujo
                        │
                        ├── (1) REST síncrono ──► [security/validacion-seguridad]
                        │         │
                        │         ├── (ataque detectado) ──► publica a [messaging/nats · seguridad.alertas]
                        │         │                                │
                        │         │                       ┌────────┴────────┐
                        │         │                       ▼                 ▼
                        │         │          [security/modulo-seguridad]  [observability/log-auditoria]
                        │         │          (revoca JWT, bloquea IP,     (payload forense → Object Storage)
                        │         │           alerta equipo)
                        │         │
                        │         └── (orden válida) ──► respuesta a gestor-ordenes
                        │
                        ├── (2) REST ──► [business/modulo-inventarios]  (reservar stock)
                        │                      │
                        │                      └── webhook HeartBeat ──► [availability/monitor]
                        │                                                        │
                        │                                          (stock negativo detectado)
                        │                                                        │
                        │                                    ┌───────────────────┘
                        │                                    ▼
                        │                         [business/gestor-ordenes] (consulta orden)
                        │                         [business/gestor-pedidos] (marca problemática)
                        │                                    │
                        │                                    ▼
                        │                         [availability/corrector]
                        │                         ├──► [business/modulo-inventarios] (rollback)
                        │                         └──► [business/gestor-pedidos] (cancelar)
                        │
                        ├── (3) REST ──► [business/gestor-pedidos]  (crear pedido)
                        │
                        └── (4) REST ──► [notifications/servicio-notificaciones] ──► Tendero / Vendedor
```

### NATS Topics definidos

| Topic | Productor | Consumidor | Propósito |
|---|---|---|---|
| `seguridad.alertas` | Validación de Seguridad | Módulo de Seguridad, Log de Auditoría | Evento de ataque DDoS detectado con payload forense; fan-out asíncrono |

> Los demás flujos (reserva de inventario, creación de pedido, HeartBeat, notificaciones) usan REST síncrono o webhook HTTP directo — no pasan por NATS.

---

## Decisiones de Arquitectura (ADRs)

### ADR-01: NATS/JetStream como Service Bus de alcance limitado
- **Decisión:** NATS/JetStream exclusivamente para el flujo de alertas de seguridad (topic: `seguridad.alertas`). El flujo de negocio principal usa REST síncrono y el HeartBeat usa webhook HTTP directo.
- **Razón:** Kafka añade complejidad operacional innecesaria (Zookeeper/KRaft, StatefulSet de 3 brokers) para un PoC académico. NATS es open source (Apache 2.0), sin vendor lock-in, más liviano y suficiente para el fan-out asíncrono de alertas de seguridad hacia Módulo de Seguridad y Log de Auditoría.

### ADR-02: Analizador CEP dentro de Validación de Seguridad
- **Decisión:** El motor de correlación CEP vive dentro del componente de Validación de Seguridad.
- **Razón:** Reduce latencia en el camino crítico; el Gestor de Órdenes llama a VS síncronamente y espera el resultado antes de continuar. Al detectar un ataque, VS publica a NATS `seguridad.alertas` y retorna rechazo a GO.

### ADR-03: Corrector desacoplado del Monitor
- **Decisión:** Monitor → Gestor de Pedidos → Corrector (no Monitor → Corrector directamente).
- **Razón:** El Corrector es reutilizable para otros escenarios de rollback sin depender del flujo HeartBeat.

### ADR-04: Log de Auditoría en Object Storage
- **Decisión:** Servicio independiente que escribe a MinIO (local) / OCI Object Storage (nube).
- **Razón:** Almacenamiento inmutable y barato; persiste aunque el Módulo de Seguridad falle; desacoplado para análisis forense posterior.

### ADR-05: Bloqueo de IP temporal (24 h con revisión)
- **Decisión:** Módulo de Seguridad aplica bloqueo temporal, no permanente.
- **Razón:** Bloqueos permanentes automatizados generan falsos positivos irreversibles.

### ADR-06: Respuesta genérica 429 al atacante
- **Decisión:** El atacante recibe error genérico; no se exponen criterios del CEP.
- **Razón:** Evitar que el atacante ajuste su patrón para evadir la detección.

### ADR-08: REST síncrono para el flujo principal de negocio
- **Decisión:** Las interacciones entre Gestor de Órdenes, Módulo de Inventarios y Gestor de Pedidos usan REST síncrono. El HeartBeat usa webhook HTTP POST directo (INV → Monitor).
- **Razón:** Simplifica la arquitectura eliminando un bus de mensajería en el camino crítico; el Gestor de Órdenes actúa como orquestador explícito y la trazabilidad es directa. El webhook HeartBeat cumple el requisito de < 300 ms sin intermediario.

### ADR-07: Portabilidad local → OKE
- **Decisión:** PoC en minikube/k3d; producción en Oracle Kubernetes Engine.
- **Razón:** Mismos manifiestos K8s; solo se reemplazan StorageClass, MinIO → OCI Object Storage, y WAF sidecar → OCI WAF.

---

## Estimación de Costos (OKE — referencia preliminar)

> Basado en Oracle Kubernetes Engine. Precios aproximados en USD/mes. A refinar con cotización real.

| Componente | Configuración estimada | Costo aprox./mes |
|---|---|---|
| **Nodos Worker OKE** | 3–5 nodos VM.Standard.E4.Flex (4 OCPU, 16 GB RAM) | $150–$250 |
| **MongoDB (OCI)** | Database Service o autogestionado en nodos | $100–$200 |
| **NATS/JetStream (autogestionado)** | 1-3 nodos en el clúster | incluido en nodos |
| **OCI Object Storage** | Log de Auditoría (~10 GB/mes estimado) | < $5 |
| **OCI API Gateway** | Por llamadas (si se migra) | variable |
| **OCI WAF** | Por reglas activas | ~$10–$30 |
| **Load Balancer** | 1 flexible | ~$10 |
| **Total estimado** | | **~$280–$500/mes** |

> ⚠️ Esta estimación es de referencia. Debe refinarse con el configurador de costos de Oracle Cloud.

---

## Diagrama de Despliegue
Primera aproximación en Mermaid: `Diagrama de Despliegue/diagrama_despliegue.md`

Renderizable en:
- VS Code + extensión **Markdown Preview Mermaid Support**
- [mermaid.live](https://mermaid.live) (pegar el bloque del archivo)

---

## Pendientes / Preguntas abiertas
- Confirmar número exacto de réplicas por servicio para el diagrama (rango definido: 2–5)
- Definir si el API Gateway custom usará Kong, Nginx o solución propia
- Confirmar política de HPA (Horizontal Pod Autoscaler) por servicio
- Precisar esquema de MongoDB por servicio (¿una DB compartida o una por microservicio?)
- Definir estrategia de red en OKE (VCN, subnets, security lists)
- Compartir tool preferida para el diagrama de despliegue (draw.io, Lucidchart, PlantUML, etc.)
