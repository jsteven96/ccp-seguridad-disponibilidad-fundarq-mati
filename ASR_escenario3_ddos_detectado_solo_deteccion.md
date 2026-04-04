# ASR 2 — Escenario 3 (Solo Detección): Validación CEP detecta ataque DDoS de negocio

**Contexto:** Un actor (bot o tendero suplantado) envía una orden con JWT válido que supera la Capa de Acceso sin ser detectada a nivel de red. Este diagrama muestra únicamente el mecanismo de detección: el analizador CEP evalúa las 3 señales de comportamiento del actor dentro de una ventana deslizante de 60 s, confirma el patrón de ataque y bloquea la orden — antes de que llegue a Inventarios o al Gestor de Pedidos. No se representa ninguna acción de revocación de acceso ni de recuperación.

**Tácticas de detección activas:**
- Seguridad → **Detección**: Analizador CEP — ventana deslizante 60 s · correlación de 3 señales · umbral ≥ 2 señales = ataque confirmado
- Disponibilidad → **Prevención**: Inventarios y Gestor de Pedidos nunca reciben la carga del atacante

---

## Diagrama de secuencia — Solo detección

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

    Note over Atacante,GP: ── RECEPCIÓN DE ORDEN SOSPECHOSA ───────────────────────────

    Atacante->>GW: HTTPS — Enviar orden {SKU: "COCA-COLA-350", cantidad: 500}

    Note over GW,AS: ── CAPA DE ACCESO — no detecta el ataque ───────────────────
    GW->>JWT: HTTP — Validar token JWT
    Note over JWT: Token JWT verificado como válido.<br/>Las credenciales son auténticas a nivel de red.<br/>No es detectable a nivel de identidad.
    JWT-->>GW: HTTP ✓ JWT válido

    GW->>AS: HTTP — Verificar anti-spoofing
    Note over AS: Patrón de red dentro de los límites normales.<br/>El ataque es semántico — no detectable a nivel de acceso.
    AS-->>GW: HTTP ✓ Sin anomalías de red

    GW->>GO: HTTP — Reenviar orden autenticada
    Note over GO: La orden llega con JWT válido y sin anomalías de red.<br/>La Capa de Acceso no puede detectar este tipo de ataque.<br/>Pasa al analizador CEP.
    GO->>VS: HTTP — Solicitar validación CEP

    Note over VS: ── DETECCIÓN CEP — ventana deslizante 60 s ──────────────────

    VS->>VS: Evaluar Señal 1 — rate de órdenes del actor
    Note over VS: Actor generó 47 órdenes en los últimos 60 s.<br/>Umbral normal: 3-5 órdenes / min.<br/>⚠ Señal 1 ACTIVA

    VS->>VS: Evaluar Señal 2 — concentración de SKU
    Note over VS: 43 de las 47 órdenes apuntan al mismo SKU.<br/>Patrón de agotamiento de stock específico.<br/>⚠ Señal 2 ACTIVA

    VS->>VS: Evaluar Señal 3 — tasa de cancelación histórica del actor
    Note over VS: Actor tiene 89 % de cancelaciones<br/>en las últimas 2 horas.<br/>⚠ Señal 3 ACTIVA

    VS->>VS: Motor de correlación — ≥ 2 señales activas = ataque confirmado
    Note over VS: 3/3 señales activas.<br/>Score de riesgo: 0.97<br/>Clasificación: DDoS de capa de negocio.<br/>⛔ Orden BLOQUEADA

    Note over INV,GP: Inventarios y Gestor de Pedidos<br/>nunca reciben esta solicitud.<br/>El inventario queda protegido de la carga del atacante.

    VS-->>GO: HTTP ✗ Orden rechazada · ataque detectado
    GO-->>GW: HTTP 429 — Too Many Requests
    GW-->>Atacante: HTTPS 429 — Tu sesión ha sido suspendida
    Note over Atacante: El atacante recibe un error genérico.<br/>No se expone información sobre las señales<br/>activadas ni los criterios de detección.
```

---

## Notas de arquitectura — Detección

| Momento | Táctica | Detalle |
|---|---|---|
| JWT válido + Anti-Spoofing no detectan el ataque | La detección es semántica, no de red | El atacante tiene credenciales auténticas y no presenta anomalías de red; la Capa de Acceso no puede detectar este tipo de ataque — la responsabilidad recae en el CEP |
| CEP evalúa 3 señales en ventana de 60 s | Detectar ataques — Complex Event Processing | El motor acumula el historial de comportamiento del actor; una sola señal puede ser un falso positivo, la correlación de múltiples señales reduce los falsos positivos |
| Umbral de correlación ≥ 2 señales | Balance precisión vs. sensibilidad | Con umbral de 1 señal se producirían demasiados falsos positivos; con 3 se perdería el ataque con 2 señales; el umbral de 2 es el punto de balance |
| Orden bloqueada antes de llegar a Inventarios y Gestor de Pedidos | Prevención — perímetro lógico de negocio | La protección ocurre en la Validación CEP; Inventarios queda completamente aislado de la carga del atacante y permanece disponible para órdenes legítimas |
| Respuesta genérica 429 al atacante | Limitar la exposición | No revelar los criterios de detección evita que el atacante calibre su patrón para evadir el sistema |

> **Distinción respecto a un DDoS de red tradicional:** el WAF y el API Gateway manejan ataques de volumen a nivel de red/HTTP. Este escenario detecta ataques semánticos donde cada solicitud individual es técnicamente válida — solo el patrón de negocio acumulado en la ventana de 60 s revela el ataque.

> **Alcance de este diagrama:** se muestra únicamente la detección del patrón de ataque y el bloqueo de la orden. Las acciones posteriores (revocación de JWT, bloqueo de IP, alerta al equipo de seguridad) son decisiones de implementación separadas no contempladas en este experimento.
