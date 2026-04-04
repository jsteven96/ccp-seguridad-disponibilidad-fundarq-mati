# ASR 2 — Escenario 3: Validación CEP detecta ataque DDoS

**Contexto:** Un actor (bot o tendero suplantado) genera una orden que llega al Gestor de Órdenes habiendo superado la Capa de Acceso con un JWT válido. Durante la validación de seguridad, el analizador CEP detecta patrones anómalos que clasifican la solicitud como un ataque DDoS de capa de negocio — órdenes fantasma para saturar el inventario. La orden **nunca llega** a Inventarios ni al Gestor de Pedidos. En su lugar, se notifica al Módulo de Seguridad con el contexto completo del evento para que tome las acciones correspondientes, incluyendo la revocación del JWT en el componente de Autenticación JWT y el bloqueo de IP en el API Gateway.

**Tácticas activas:**
- Seguridad → **Detectar ataques**: Analizador CEP — detecta denegación de servicio a nivel de negocio
- Seguridad → **Reaccionar — Revocar acceso**: Módulo de Seguridad revoca JWT en Autenticación JWT y bloquea IP en API Gateway
- Seguridad → **Reaccionar — Informar a los actores**: Notificación al equipo de seguridad con contexto forense
- Seguridad → **Recuperarse — Manejo de log de eventos**: Registro del evento en Log de Auditoría independiente
- Disponibilidad → **Prevención**: Inventarios nunca recibe la carga del atacante

---

## Diagrama de secuencia

```mermaid
sequenceDiagram
    autonumber

    actor Atacante as Atacante (bot / actor suplantado)
    participant GW as API Gateway
    participant JWT as Autenticación JWT
    participant AS as Anti-Spoofing
    participant GO as Gestor de Órdenes
    participant VS as Validación CEP
    participant INV as Inventarios
    participant GP as Gestor de Pedidos
    participant SEG as Módulo de Seguridad
    participant LOG as Log de Auditoría
    participant EQ as Equipo de Seguridad

    Note over Atacante,EQ: ── RECEPCIÓN DE ORDEN SOSPECHOSA ───────────────────────────

    Atacante->>GW: HTTPS — Enviar orden {SKU: "COCA-COLA-350", cantidad: 500}
    GW->>JWT: HTTP — Validar token JWT
    Note over JWT: Token JWT verificado como válido.<br/>Las credenciales son auténticas a nivel de red.
    JWT-->>GW: HTTP ✓ JWT válido
    GW->>AS: HTTP — Verificar anti-spoofing
    Note over AS: Cabeceras, IP de origen y huella de red<br/>no presentan anomalías detectables.<br/>El ataque es semántico, no de red.
    AS-->>GW: HTTP ✓ Anti-spoofing superado
    GW->>GO: HTTP — Reenviar orden
    Note over GO: La orden llega con JWT válido y sin anomalías de red.<br/>No es detectable a nivel de Capa de Acceso.<br/>Pasa al validador CEP.
    GO->>VS: HTTP — Remitir orden para validación de seguridad

    Note over VS: ── ANÁLISIS CEP — ventana deslizante 60 s ───────────────────

    VS->>VS: Evaluar Señal 1 — rate de órdenes del actor
    Note over VS: Actor generó 47 órdenes en los últimos 60 s.<br/>Umbral normal: 3-5 órdenes / min.<br/>⚠ Señal 1 ACTIVA

    VS->>VS: Evaluar Señal 2 — concentración de SKU
    Note over VS: 43 de las 47 órdenes apuntan al mismo SKU.<br/>Patrón de agotamiento de stock específico.<br/>⚠ Señal 2 ACTIVA

    VS->>VS: Evaluar Señal 3 — tasa de cancelación histórica del actor
    Note over VS: Actor tiene 89 % de cancelaciones<br/>en las últimas 2 horas.<br/>⚠ Señal 3 ACTIVA

    VS->>VS: Motor de correlación — ≥ 2 señales activas = ataque confirmado
    Note over VS: 3/3 señales activas.<br/>Clasificación: DDoS de capa de negocio.<br/>Orden BLOQUEADA — no se reenvía a Inventarios ni a Gestor de Pedidos.

    Note over INV,GP: INV y GP nunca reciben esta solicitud.<br/>Inventarios queda protegido<br/>de la carga del atacante.

    Note over VS,LOG: ── NOTIFICACIÓN Y REGISTRO ──────────────────────────────────

    VS->>SEG: HTTP — Evento de ataque detectado {<br/>  actor_id: "tendero_8821",<br/>  ip: "190.24.113.45",<br/>  timestamp: "2025-03-19T14:32:07Z",<br/>  jwt_token: "eyJ...",<br/>  orden: {SKU: "COCA-COLA-350", cantidad: 500},<br/>  señales_activas: [rate, concentración_SKU, cancelaciones],<br/>  score_riesgo: 0.97<br/>}

    VS->>LOG: HTTP — Registrar evento de seguridad {mismo payload + traza CEP}

    Note over SEG: ── ACCIONES DEL MÓDULO DE SEGURIDAD ────────────────────────

    SEG->>JWT: HTTP — Revocar JWT del actor · sesión invalidada
    Note over JWT: El actor no puede generar nuevas solicitudes<br/>con el token actual.<br/>Cualquier intento posterior será rechazado en la Capa de Acceso.

    SEG->>GW: HTTP — Bloquear IP 190.24.113.45 · añadir a lista de bloqueo temporal
    Note over GW: Bloqueo temporal con revisión en 24 h.<br/>Previene reintento inmediato desde misma IP.

    SEG->>EQ: HTTP — Alerta a equipo de seguridad {<br/>  resumen: "DDoS capa negocio detectado",<br/>  actor_id, ip, timestamp,<br/>  SKU objetivo: COCA-COLA-350,<br/>  órdenes_en_ventana: 47,<br/>  acción_tomada: "JWT revocado · IP bloqueada"<br/>}

    EQ-->>SEG: HTTP — Confirmación de recepción · inicio de investigación

    Note over GO,Atacante: ── RESPUESTA AL ATACANTE ────────────────────────────────────

    VS-->>GO: HTTP ✗ Orden rechazada · actor bloqueado
    GO-->>GW: HTTP 429 — Too Many Requests
    GW-->>Atacante: HTTPS 429 — Tu sesión ha sido suspendida
    Note over Atacante: El atacante recibe un error genérico.<br/>No se expone información sobre los criterios<br/>de detección ni las señales activadas.
```

---

## Notas de arquitectura

| Momento | Decisión | Razonamiento |
|---|---|---|
| JWT válido + Anti-Spoofing no detectan el ataque | La detección es semántica, no de red | El atacante tiene credenciales auténticas y no presenta anomalías de red; la Capa de Acceso no puede detectar este tipo de ataque — la responsabilidad recae en el analizador CEP |
| Orden bloqueada en Validación CEP | Inventarios y Gestor de Pedidos nunca reciben la solicitud | La protección ocurre en el perímetro lógico de negocio; Inventarios queda completamente aislado de la carga del atacante |
| 3 señales del CEP correlacionadas | Motor de correlación ≥ 2 señales = ataque confirmado | Una sola señal puede ser un falso positivo; la correlación de múltiples señales reduce falsos positivos antes de bloquear |
| Revocación del JWT en componente Autenticación JWT | Revocar acceso — desactivación en Capa de Acceso | El Módulo de Seguridad instruye al componente JWT para que invalide el token; cualquier intento posterior del atacante es rechazado antes de llegar a la lógica de negocio |
| Bloqueo de IP en API Gateway | Revocar acceso — bloqueo perimetral | El API Gateway aplica el bloqueo de IP; el tráfico del atacante es descartado en el primer punto de entrada, sin consumir recursos internos |
| Payload completo enviado al Módulo de Seguridad | Informar actores — contexto forense | actor_id, IP, timestamp, token, SKU objetivo y score de riesgo permiten investigación posterior y correlación con otros eventos |
| Bloqueo de IP temporal con revisión | Revocar acceso — sin bloqueo permanente | Un bloqueo permanente automatizado puede generar falsos positivos irreversibles; la revisión en 24 h balancea seguridad y disponibilidad |
| Respuesta genérica 429 al atacante | Limitar la exposición | No revelar los criterios de detección evita que el atacante ajuste su patrón para evadir el sistema |
| Log de Auditoría independiente del Módulo de Seguridad | Recuperarse — Manejo de log de eventos | El log persiste incluso si el Módulo de Seguridad falla; permite análisis forense posterior desacoplado |

> **Relación con el ASR de disponibilidad:** al bloquear la orden antes de que llegue a Inventarios, este escenario es también una táctica de disponibilidad — el módulo nunca recibe la carga artificial del atacante y permanece disponible para órdenes legítimas.

> **Distinción respecto a un DDoS de red tradicional:** el WAF y el API Gateway manejan ataques de volumen a nivel de red/HTTP. Este escenario detecta ataques semánticos donde cada solicitud individual es válida técnicamente — solo el patrón de negocio revela el ataque.
