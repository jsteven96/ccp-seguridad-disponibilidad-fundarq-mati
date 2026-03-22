# ASR — Escenario 3: Validación de seguridad detecta ataque DDoS

**Contexto:** Un actor (bot o tendero suplantado) genera una orden que llega al Gestor de Órdenes. Durante la validación de seguridad, el analizador CEP detecta patrones anómalos que clasifican la solicitud como un ataque DDoS de capa de negocio — órdenes fantasma para saturar el inventario. La orden **nunca llega** al módulo de inventarios ni al Gestor de Pedidos. En su lugar, la Validación de Seguridad publica el evento de ataque en NATS/JetStream, desde donde el Módulo de Seguridad y el Log de Auditoría lo consumen de forma independiente.

**Tácticas activas:**
- Seguridad → **Detectar ataques**: Analizador CEP — detecta denegación de servicio a nivel de negocio
- Seguridad → **Reaccionar — Revocar acceso**: Módulo de Seguridad bloquea al actor
- Seguridad → **Reaccionar — Informar a los actores**: Notificación al equipo de seguridad con contexto forense
- Seguridad → **Recuperarse — Manejo de log de eventos**: Registro del evento para análisis posterior
- Disponibilidad → **Prevención**: El módulo de inventarios nunca recibe la carga del atacante

---

## Diagrama de secuencia

```mermaid
sequenceDiagram
    autonumber

    actor Atacante as Atacante (bot / actor suplantado)
    participant GO as Gestor de Órdenes
    participant VS as Validación de Seguridad
    participant NATS as NATS JetStream
    participant INV as Módulo de Inventarios
    participant GP as Gestor de Pedidos
    participant SEG as Módulo de Seguridad
    participant LOG as Log de Auditoría
    participant EQ as Equipo de Seguridad

    Note over Atacante,EQ: ── RECEPCIÓN DE ORDEN SOSPECHOSA ───────────────────────────

    Atacante->>GO: Enviar orden {SKU: "COCA-COLA-350", cantidad: 500}
    Note over GO: La orden llega con JWT válido.<br/>No es detectable a nivel de red ni de API Gateway.<br/>Se pasa al validador de seguridad.
    GO->>VS: Validar orden (REST síncrono)

    Note over VS: ── ANÁLISIS CEP — ventana deslizante 60 s ───────────────────

    VS->>VS: Evaluar Señal 1 — rate de órdenes del actor
    Note over VS: Actor generó 47 órdenes en los últimos 60 s.<br/>Umbral normal: 3-5 órdenes / min.<br/>⚠ Señal 1 ACTIVA

    VS->>VS: Evaluar Señal 2 — concentración de SKU
    Note over VS: 43 de las 47 órdenes apuntan al mismo SKU.<br/>Patrón de agotamiento de stock específico.<br/>⚠ Señal 2 ACTIVA

    VS->>VS: Evaluar Señal 3 — tasa de cancelación histórica del actor
    Note over VS: Actor tiene 89 % de cancelaciones<br/>en las últimas 2 horas.<br/>⚠ Señal 3 ACTIVA

    VS->>VS: Motor de correlación — ≥ 2 señales activas = ataque confirmado
    Note over VS: 3/3 señales activas.<br/>Clasificación: DDoS de capa de negocio.<br/>Orden BLOQUEADA — no se reenvía a inventario ni pedidos.

    Note over INV,GP: INV y GP nunca reciben esta solicitud.<br/>El módulo de inventarios queda protegido<br/>de la carga del atacante.

    Note over VS,LOG: ── PUBLICACIÓN DEL EVENTO DE ATAQUE ────────────────────────

    VS->>NATS: Publicar seguridad.alertas {<br/>  actor_id: "tendero_8821",<br/>  ip: "190.24.113.45",<br/>  timestamp: "2025-03-19T14:32:07Z",<br/>  jwt_token: "eyJ...",<br/>  orden: {SKU: "COCA-COLA-350", cantidad: 500},<br/>  señales_activas: [rate, concentración_SKU, cancelaciones],<br/>  score_riesgo: 0.97<br/>}

    par Distribución del evento de ataque (NATS fan-out)
        NATS->>SEG: seguridad.alertas {payload forense}
    and
        NATS->>LOG: seguridad.alertas {payload forense + traza CEP}
    end

    Note over SEG: ── ACCIONES DEL MÓDULO DE SEGURIDAD ────────────────────────

    SEG->>SEG: Revocar JWT del actor · sesión invalidada
    Note over SEG: El actor no puede generar nuevas solicitudes<br/>con el token actual.

    SEG->>SEG: Bloquear IP 190.24.113.45 · añadir a lista de bloqueo temporal
    Note over SEG: Bloqueo temporal con revisión en 24 h.<br/>Previene reintento inmediato desde misma IP.

    SEG->>EQ: Alerta a equipo de seguridad {<br/>  resumen: "DDoS capa negocio detectado",<br/>  actor_id, ip, timestamp,<br/>  SKU objetivo: COCA-COLA-350,<br/>  órdenes_en_ventana: 47,<br/>  acción_tomada: "JWT revocado · IP bloqueada"<br/>}

    EQ-->>SEG: Confirmación de recepción · inicio de investigación

    LOG->>LOG: Persistir evento en Object Storage (MinIO / OCI)

    Note over GO,Atacante: ── RESPUESTA AL ATACANTE ────────────────────────────────────

    VS-->>GO: ✗ Orden rechazada · actor bloqueado
    GO-->>Atacante: 429 Too Many Requests — tu sesión ha sido suspendida
    Note over Atacante: El atacante recibe un error genérico.<br/>No se expone información sobre los criterios<br/>de detección ni las señales activadas.
```

---

## Notas de arquitectura

| Momento | Decisión | Razonamiento |
|---|---|---|
| Orden bloqueada en Validación de Seguridad | Inventario y Pedidos nunca reciben la solicitud | La protección ocurre en el perímetro lógico de negocio; el módulo de inventarios queda completamente aislado de la carga del atacante |
| NATS/JetStream exclusivo para seguridad | Bus de eventos de alcance limitado | Solo el flujo de alertas de seguridad usa mensajería asíncrona; el resto del sistema opera con REST síncrono |
| Fan-out NATS → SEG y LOG en paralelo | Módulo de Seguridad y Log de Auditoría desacoplados | Ambos consumen el mismo evento independientemente; si uno falla, el otro persiste el registro |
| 3 señales del CEP correlacionadas | Motor de correlación ≥ 2 señales = ataque confirmado | Una sola señal puede ser un falso positivo; la correlación reduce falsos positivos antes de bloquear |
| JWT válido no garantiza orden legítima | La detección es semántica, no de red | El atacante tiene credenciales válidas; el WAF y el API Gateway no detectan este tipo de ataque |
| Bloqueo de IP temporal con revisión | Revocar acceso — sin bloqueo permanente | Un bloqueo permanente automatizado puede generar falsos positivos irreversibles; la revisión en 24 h balancea seguridad y disponibilidad |
| Respuesta genérica 429 al atacante | Limitar la exposición | No revelar los criterios de detección evita que el atacante ajuste su patrón para evadir el sistema |
| Log de Auditoría independiente del Módulo de Seguridad | Recuperarse — Manejo de log de eventos | El log persiste incluso si el Módulo de Seguridad falla; permite análisis forense posterior desacoplado |

> **Relación con el ASR de disponibilidad:** al bloquear la orden antes de que llegue al inventario, este escenario es también una táctica de disponibilidad — el módulo de inventarios nunca recibe la carga artificial del atacante y permanece disponible para órdenes legítimas.

> **Distinción respecto a un DDoS de red tradicional:** el WAF y el API Gateway manejan ataques de volumen a nivel de red/HTTP. Este escenario detecta ataques semánticos donde cada solicitud individual es válida técnicamente — solo el patrón de negocio revela el ataque.
