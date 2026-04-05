# Diseño del Experimento — Validación de ASRs de Disponibilidad y Seguridad (CCP)

> Agente: `arquitecto-experimentos`
> Reto 2 — ARTI4109 Arquitectura de Software · Universidad de los Andes · MATI

---

## 1. ASRs que se validan

### ASR-1 · Disponibilidad — Detección de cualquier inconsistencia de inventario

| Campo | Valor |
|---|---|
| Actor | Vendedor / Tendero |
| Estímulo | Reserva de un producto |
| Ambiente | 7×24×365 · 5 países · 1.000 usuarios concurrentes · 25 pedidos/s |
| Artefacto | Módulo de Inventarios |
| Respuesta esperada | El sistema debe detectar cualquier inconsistencia que se presente en el inventario en cuanto a las cantidades de los productos se refiere |
| Medida | Detección < 300 ms |
| Impacto negativo | Latencia — las validaciones adicionales de coherencia de stock (self-test) pueden afectar la latencia del ciclo de HeartBeat |

### ASR-2 · Seguridad — Detección de ataque DoS de capa de negocio

| Campo | Valor |
|---|---|
| Actor | Tendero (o bot que lo suplanta con JWT válido) |
| Estímulo | Secuencia de solicitudes de pedido anómalas en ventana de 60 s |
| Ambiente | Ídem ASR-1 |
| Artefacto | Gestor de Pedidos / Inventarios |
| Respuesta esperada | El sistema identifica el patrón CEP y bloquea las órdenes fantasma antes de que afecten el stock |
| Medida | Identificación < 300 ms |

---

## 2. Hipótesis

### H1 — Detección de cualquier inconsistencia de inventario (ASR-1)

> **Si** el Módulo de Inventarios ejecuta su Validador de Coherencia (VALCOH) en cada ciclo de HeartBeat y detecta cualquiera de las cuatro clases de inconsistencia (stock negativo, divergencia de reservas, estado concurrente divergente, o fallo estructural de self-test), **entonces** publica un HeartBeat clasificado al topic NATS correspondiente y el Monitor lo consume, clasifica el tipo y activa la respuesta correcta en menos de 300 ms desde la publicación.

**Clases de inconsistencia cubiertas:**

| Tipo | Topic NATS | Respuesta del Monitor |
|---|---|---|
| `STOCK_NEGATIVO` | `heartbeat.inventario.stock_negativo` | Rollback vía Corrector |
| `DIVERGENCIA_RESERVAS` | `heartbeat.inventario.divergencia_reservas` | Reconciliación vía Corrector |
| `ESTADO_CONCURRENTE` | `heartbeat.inventario.estado_concurrente` | Resolución de conflicto (timestamp menor gana) |
| `SELF_TEST_FAILED` | `heartbeat.inventario.self_test_failed` | Failover a INV-Standby |

### H2 — Detección de DDoS de negocio (ASR-2)

> **Si** un actor genera ≥ 2 de las 3 señales CEP (rate anómalo, concentración de SKU, tasa de cancelación histórica) dentro de una ventana deslizante de 60 s, **entonces** el sistema identifica el patrón como ataque, bloquea la orden en la Validación CEP en menos de 300 ms — sin que ninguna orden del atacante llegue a Inventarios ni al Gestor de Pedidos.

---

## 3. Arquitectura del harness de simulación

El harness replica la arquitectura real del sistema CCP con Docker Compose en lugar de Kubernetes. Cada servicio corre como contenedor independiente con los mismos protocolos de producción (HTTP interno, NATS para eventos, MongoDB para persistencia).

### 3.1 Diagrama del harness

```mermaid
graph TD
    subgraph INYECTOR["🔧 Inyector de Fallas (script Python)"]
        GEN_A["Generador Escenario A\nordenes con cantidades > stock\ndivergencia de reservas\nself-test forzado a fallar"]
        GEN_B["Generador Escenario B\nbot 47 órdenes / 60 s"]
    end

    subgraph ACCESO["Capa de Acceso"]
        GW["API Gateway\n:8080"]
        JWT_C["Autenticación JWT\n:8081"]
        AS_C["Anti-Spoofing\n:8082"]
    end

    subgraph NEGOCIO["Lógica de Negocio"]
        GO_C["GestorOrdenes.jar\n:8083"]
        GP_C["GestorPedidos.jar\n:8084"]
        INV_C["ModuloInventario.jar (primary)\n:8085\n+ VALCOH interno"]
        INV_S_C["ModuloInventario-standby.jar\n:8095\nréplica pasiva"]
        MON_C["Monitor.jar (router de tipos)\n:8086"]
        CORR_C["Corrector.jar\n:8087"]
        NOTIF_C["GestorNotificaciones.jar\n:8088"]
    end

    subgraph SEG["Seguridad"]
        VS_C["ValidacionCEP.jar\n:8089"]
        SEG_C["ModuloSeguridad.jar\n:8090"]
        LOG_C["LogAuditoria.jar\n:8091"]
    end

    subgraph INFRA["Infraestructura"]
        NATS_C["NATS JetStream\n:4222\nStreams: heartbeat.inventario.*\ncorreccion.* · failover.*"]
        MONGO["MongoDB Replica Set\nPrimary :27017\nSecondary :27018"]
    end

    subgraph OBSERVADOR["📊 Colector de Métricas"]
        COL["metrics-collector\nregistra timestamps y eventos\npor tipo de HeartBeat"]
        DASH["Dashboard\nresultados del experimento"]
    end

    GEN_A -->|HTTPS| GW
    GEN_B -->|HTTPS| GW
    GW --> JWT_C --> AS_C --> GO_C
    GO_C --> VS_C
    VS_C -->|orden OK| INV_C
    VS_C -->|orden OK| GP_C
    VS_C -->|ataque| SEG_C
    VS_C -->|ataque| LOG_C

    INV_C -->|heartbeat.inventario.*| NATS_C
    INV_S_C -.->|réplica pasiva| MONGO
    INV_C --> MONGO

    NATS_C -->|suscripción| MON_C
    MON_C -->|STOCK_NEGATIVO / DIVERGENCIA / CONCURRENTE| CORR_C
    MON_C -->|SELF_TEST_FAILED / timeout| INV_S_C
    MON_C --> GO_C

    GP_C -->|correccion.*| NATS_C
    NATS_C -->|suscripción| CORR_C
    CORR_C --> INV_C
    CORR_C --> GP_C
    CORR_C --> MONGO

    GO_C --> NOTIF_C
    SEG_C --> JWT_C
    SEG_C --> GW
    LOG_C --> MONGO

    GO_C -.->|métricas| COL
    INV_C -.->|métricas| COL
    MON_C -.->|métricas| COL
    CORR_C -.->|métricas| COL
    VS_C -.->|métricas| COL
    SEG_C -.->|métricas| COL
    COL --> DASH

    style INV_S_C fill:#f0f0f0,stroke:#999,stroke-dasharray: 5 5
```

### 3.2 Diagrama de Componentes (arquitectura actualizada)

```mermaid
graph TD
    subgraph ACCESO["Capa de Acceso"]
        GW_C["API Gateway"]
        JWT_K["Autenticación JWT"]
        AS_K["Anti-Spoofing"]
    end

    subgraph NEGOCIO_K["Lógica de Negocio"]
        GO_K["Gestor de Órdenes"]
        GP_K["Gestor de Pedidos"]
        INV_K["Inventarios\n(nodo primario)"]
        VALCOH_K["VALCOH\nValidador de Coherencia\nself-test interno"]
        INV_SK["INV-Standby\nnodo pasivo"]
        MON_K["Monitor\nrouter de inconsistencias"]
        CORR_K["Corrector\nrollback · reconciliación · failover"]
        NOTIF_K["Gestor de Notificaciones"]
    end

    subgraph SEG_K["Seguridad"]
        VS_K["Validación CEP\nDDoS de negocio"]
        SEG_K2["Módulo de Seguridad"]
        LOG_K["Log de Auditoría"]
    end

    subgraph INFRA_K["Infraestructura"]
        NATS_K["NATS JetStream\nheartbeat.inventario.*\ncorreccion.* · failover.*"]
        MONGO_K["MongoDB\nReplica Set"]
    end

    GW_C --> JWT_K --> AS_K --> GO_K
    GO_K --> VS_K
    VS_K -->|orden válida| INV_K
    VS_K -->|orden válida| GP_K
    VS_K -->|ataque detectado| SEG_K2
    VS_K -->|ataque detectado| LOG_K

    INV_K --> VALCOH_K
    VALCOH_K -->|resultado self-test| INV_K
    INV_K -->|HeartBeat clasificado| NATS_K
    INV_K --> MONGO_K
    INV_SK -.->|réplica pasiva| MONGO_K

    NATS_K --> MON_K
    MON_K -->|rollback / reconciliación| CORR_K
    MON_K -->|failover signal| INV_SK
    MON_K --> GO_K

    GP_K -->|evento corrección| NATS_K
    NATS_K --> CORR_K
    CORR_K --> INV_K
    CORR_K --> GP_K

    GO_K --> NOTIF_K
    SEG_K2 --> JWT_K
    SEG_K2 --> GW_C
    LOG_K --> MONGO_K

    style INV_SK fill:#f0f0f0,stroke:#999,stroke-dasharray: 5 5
    style VALCOH_K fill:#e8f4e8,stroke:#4a8
```

### 3.3 Diagrama de Despliegue (infraestructura actualizada)

```mermaid
graph TD
    subgraph INTERNET_K["Internet / Red externa"]
        TENDERO_D["Vendedor / Tendero\napp móvil / browser"]
        ATACANTE_D["Atacante\nbot con JWT válido"]
    end

    subgraph K8S["Cluster Kubernetes — CCP"]

        subgraph NODO_ACCESO["Nodo de Acceso"]
            GW_D["API Gateway\nPod :8080"]
            JWT_D["Auth JWT\nPod :8081"]
            AS_D["Anti-Spoofing\nPod :8082"]
        end

        subgraph NODO_NEG["Nodo de Lógica de Negocio"]
            GO_D["GestorOrdenes.jar\nPod :8083"]
            GP_D["GestorPedidos.jar\nPod :8084"]
            INV_D["ModuloInventario.jar primary\nPod :8085\n+ VALCOH interno"]
            INV_SD["ModuloInventario-standby.jar\nPod :8095\nréplica pasiva"]
            MON_D["Monitor.jar\nPod :8086\nrouter de tipos"]
            CORR_D["Corrector.jar\nPod :8087"]
            NOTIF_D["GestorNotificaciones.jar\nPod :8088"]
        end

        subgraph NODO_SEG["Nodo de Seguridad"]
            VS_D["ValidacionCEP.jar\nPod :8089"]
            SEG_D["ModuloSeguridad.jar\nPod :8090"]
            LOG_D["LogAuditoria.jar\nPod :8091"]
        end

        subgraph NODO_INFRA["Nodo de Infraestructura"]
            NATS_D["NATS JetStream\n:4222\nStreams:\nheartbeat.inventario.*\ncorreccion.*\nfailover.*"]
            MONGO_D["MongoDB Replica Set\nPrimary :27017\nSecondary :27018"]
            MINIO_D["MinIO\n:9000"]
        end

    end

    TENDERO_D -->|HTTPS| GW_D
    ATACANTE_D -->|HTTPS| GW_D
    GW_D --> JWT_D --> AS_D --> GO_D
    GO_D --> VS_D
    VS_D --> INV_D
    VS_D --> GP_D
    VS_D --> SEG_D
    VS_D --> LOG_D

    INV_D -->|heartbeat.inventario.*| NATS_D
    INV_D --> MONGO_D
    INV_SD -.->|réplica pasiva| MONGO_D

    NATS_D --> MON_D
    MON_D -->|failover signal| INV_SD
    MON_D --> CORR_D
    MON_D --> GO_D

    GP_D --> NATS_D
    NATS_D --> CORR_D
    CORR_D --> INV_D
    CORR_D --> GP_D
    CORR_D --> MONGO_D

    GO_D --> NOTIF_D
    SEG_D --> JWT_D
    SEG_D --> GW_D
    LOG_D --> MONGO_D

    style INV_SD fill:#f0f0f0,stroke:#999,stroke-dasharray: 5 5
```

### 3.4 Estado inicial del inventario

| SKU | Stock inicial | Propósito |
|---|---|---|
| `COCA-COLA-350` | 9 unidades | SKU objetivo de los experimentos de inconsistencia |
| `AGUA-500` | 100 unidades | SKU de control (órdenes legítimas) |
| `ARROZ-1KG` | 50 unidades | SKU de control |

### 3.5 Esquema del HeartBeat expandido

```json
{
  "tipo": "STOCK_NEGATIVO | DIVERGENCIA_RESERVAS | ESTADO_CONCURRENTE | SELF_TEST_OK | SELF_TEST_FAILED",
  "timestamp_ms": 1742820000123,
  "nodo": "inv-primary",
  "inconsistencias": [
    {
      "SKU": "COCA-COLA-350",
      "stock_real": -1,
      "stock_esperado": 9,
      "delta": -10
    }
  ],
  "self_test": {
    "resultado": "OK | FAILED",
    "checks_ejecutados": ["stock_negativo", "suma_reservas", "reservas_huerfanas"],
    "check_fallido": null
  }
}
```

El Colector de Métricas suscribe a `metrics.*` y a `heartbeat.inventario.*` para capturar todos los eventos con sus timestamps.

---

## 4. Experimento A — Validación de H1 (ASR-1 · cualquier inconsistencia de inventario)

**Escenario de referencia:** `ASR_escenario2_heartbeat_negativo.md`

### 4.1 Cómo se simula la falla

Cada caso de prueba inyecta una clase distinta de inconsistencia. El inyector envía peticiones al API Gateway con JWT válido de un tendero legítimo — nada es sospechoso a nivel de seguridad. La falla es exclusivamente de inventario.

```mermaid
sequenceDiagram
    autonumber
    participant INY as 🔧 Inyector de Fallas
    participant GW as API Gateway
    participant GO as Gestor de Órdenes
    participant VS as Validación CEP
    participant INV as Inventarios + VALCOH
    participant NATS as NATS JetStream
    participant MON as Monitor
    participant GP as Gestor de Pedidos
    participant CORR as Corrector
    participant COL as 📊 Colector Métricas

    Note over INY: PREPARACIÓN<br/>Stock COCA-COLA-350 = 9<br/>Tipo de falla según caso de prueba

    INY->>GW: HTTPS POST /ordenes {cantidad > stock, jwt: válido}
    GW->>GO: HTTP — orden autenticada
    GO->>VS: HTTP — validar orden
    Note over VS: 0 señales activas — orden legítima ✓
    VS-->>GO: ✓ orden genuina

    GO->>INV: HTTP — reservar {SKU, cantidad}
    Note over INV: VALCOH ejecuta self-test<br/>Detecta inconsistencia → genera tipo clasificado

    INV->>NATS: [t0] Publicar HeartBeat clasificado<br/>Topic: heartbeat.inventario.{tipo}
    INV->>COL: 📊 t0_heartbeat · tipo de inconsistencia

    NATS-->>MON: [t1] HeartBeat recibido
    MON->>COL: 📊 t1_deteccion · tipo enrutado

    Note over MON: Router activa acción según tipo

    MON->>GP: HTTP — marcar orden
    GP->>NATS: [t2] evento corrección
    NATS-->>CORR: evento recibido

    par Rollback / corrección coordinada
        CORR->>INV: HTTP — revertir / reconciliar
        INV-->>CORR: ✓ estado restaurado
    and
        CORR->>GP: HTTP — cancelar / ajustar pedido
        GP-->>CORR: ✓ pedido actualizado
    end

    CORR->>COL: 📊 [t3] corrección completada
    Note over COL: ✅ t3 - t0 < 300 ms (detección)<br/>✅ stock_final correcto<br/>✅ tipo_heartbeat == tipo_falla_inyectada
```

### 4.2 Cómo se detecta que la falla fue manejada

| Evidencia | Qué se verifica | Cómo se obtiene |
|---|---|---|
| HeartBeat publicado al topic correcto | `topic == heartbeat.inventario.{tipo_esperado}` | Log del Colector / NATS monitor |
| Tipo del HeartBeat correcto | `payload.tipo == tipo_falla_inyectada` | Log del Colector |
| Stock restaurado | `stock_final == stock_pre_falla` | `GET /inventario/COCA-COLA-350` |
| Pedido cancelado / ajustado | Estado del pedido correcto | `GET /pedidos/{orden_id}` |
| Tiempo de detección < 300 ms | `t1 - t0 < 300` | Calculado por el Colector |
| Notificación enmascarada | Mensaje sin trazas internas | Log de GestorNotificaciones |

### 4.3 Casos de prueba

#### CP-A1 — Happy path (control negativo)

| Campo | Valor |
|---|---|
| Orden inyectada | `{SKU: COCA-COLA-350, cantidad: 5}` — cantidad ≤ stock |
| Resultado esperado | Reserva exitosa · VALCOH pasa todos los checks · HeartBeat tipo `SELF_TEST_OK` · Monitor sin acción |
| Evidencia de éxito | Topic `heartbeat.inventario.ok` recibido · ausencia de eventos de corrección |

#### CP-A2 — Stock negativo (clase: `STOCK_NEGATIVO`)

| Campo | Valor |
|---|---|
| Orden inyectada | `{SKU: COCA-COLA-350, cantidad: 10}` — supera stock de 9 |
| Falla simulada | Stock queda en -1 · VALCOH check 1 falla |
| Topic NATS esperado | `heartbeat.inventario.stock_negativo` |
| Detección esperada | Monitor recibe HeartBeat < 300 ms · Corrector ejecuta rollback |
| Stock final esperado | 9 (restaurado) |

#### CP-A3 — Concurrencia: dos órdenes simultáneas (clase: `ESTADO_CONCURRENTE`)

| Campo | Valor |
|---|---|
| Orden 1 | `{COCA-COLA-350, cantidad: 6}` — hilo 1 |
| Orden 2 | `{COCA-COLA-350, cantidad: 6}` — hilo 2, mismo instante |
| Falla simulada | Ambas reservas pasan el check individual, pero juntas exceden el stock |
| Topic NATS esperado | `heartbeat.inventario.estado_concurrente` |
| Stock final esperado | 3 (solo la primera orden confirmada, la segunda revertida) |

#### CP-A4 — Divergencia de reservas (clase: `DIVERGENCIA_RESERVAS`)

| Campo | Valor |
|---|---|
| Falla simulada | Inyectar directamente en la BD: `reservas_activas(COCA-COLA-350) = 7` pero `stock_actual = 5` (diferencia de 2 unidades) sin ninguna transacción en curso |
| Mecanismo de detección | VALCOH check 2 detecta: `suma_reservas(7) ≠ stock_inicial(9) - stock_actual(5)` |
| Topic NATS esperado | `heartbeat.inventario.divergencia_reservas` |
| Acción del Monitor | Reconciliación: Corrector recalcula y ajusta el stock real |
| Propósito | Valida que el self-test detecta inconsistencias que no emergen de una transacción activa |

#### CP-A5 — Fallo estructural de self-test → failover (clase: `SELF_TEST_FAILED`)

| Campo | Valor |
|---|---|
| Falla simulada | Forzar fallo del VALCOH (ej. corrupción del contador de versión) mediante endpoint de inyección de fallas |
| Topic NATS esperado | `heartbeat.inventario.self_test_failed` |
| Acción del Monitor | Publicar señal de failover a `failover.inventario` → INV-Standby se promueve a primario |
| Evidencia de failover | Peticiones posteriores son atendidas por INV-Standby (puerto :8095) |
| Criterio de tiempo | Failover completado < 500 ms (fuera del presupuesto del ASR-1, pero medido) |

### 4.4 Métricas del Experimento A

| Métrica | Origen | Criterio |
|---|---|---|
| `t0_heartbeat` | Timestamp en INV al publicar a NATS | — (referencia) |
| `t1_deteccion` | Timestamp en Monitor al recibir de NATS | `t1 - t0 < 300 ms` ← **criterio ASR-1** |
| `t_self_test` | Duración del VALCOH dentro del ciclo INV | `< 50 ms` |
| `t_clasificacion_monitor` | Tiempo del router para despachar por tipo | `< 10 ms` |
| `t2_correccion` | Timestamp en GP al publicar evento de corrección | `t2 - t0 < 150 ms` |
| `t3_rollback` | Timestamp en Corrector al confirmar corrección | `t3 - t0 < 500 ms` |
| `t_failover` | Tiempo hasta que INV-Standby acepta escrituras (CP-A5) | `< 500 ms` |
| `tipo_heartbeat` | Campo `tipo` en el payload | Debe coincidir con el tipo de falla inyectada |
| `stock_final` | `GET /inventario/COCA-COLA-350` | == stock pre-falla |
| `mensaje_tendero` | Log de GestorNotificaciones | Sin trazas internas del sistema |

---

## 5. Experimento B — Validación de H2 (ASR-2 · DDoS de negocio)

**Escenario de referencia:** `ASR_escenario3_ddos_detectado.md`

### 5.1 Cómo se simula el ataque

El inyector actúa como un bot con **JWT válido** (generado por el mismo sistema). Envía 47 peticiones en 60 segundos concentradas en el mismo SKU. El ataque pasa la Capa de Acceso (JWT válido, IP normal) y solo es detectable por el patrón semántico en la Validación CEP.

```mermaid
sequenceDiagram
    autonumber
    participant INY as 🔧 Inyector de Ataque
    participant GW as API Gateway
    participant JWT as Autenticación JWT
    participant AS as Anti-Spoofing
    participant GO as Gestor de Órdenes
    participant VS as Validación CEP
    participant INV as Inventarios
    participant SEG as Módulo de Seguridad
    participant LOG as Log de Auditoría
    participant COL as 📊 Colector Métricas

    Note over INY: PREPARACIÓN<br/>JWT válido · 47 peticiones en 60 s<br/>43/47 apuntan a COCA-COLA-350<br/>89% cancelaciones históricas

    loop 47 veces en 60 s
        INY->>GW: HTTPS POST /ordenes {SKU: COCA-COLA-350, cant: 500}
        GW->>JWT: validar token
        JWT-->>GW: ✓ JWT válido
        GW->>AS: anti-spoofing
        AS-->>GW: ✓ sin anomalías de red
        GW->>GO: HTTP — orden autenticada
        GO->>VS: HTTP — validar orden
    end

    Note over VS: [t0] Evaluación CEP<br/>Señal 1 rate: 47 órd/min ❌ ACTIVA<br/>Señal 2 concentración SKU: 91% ❌ ACTIVA<br/>Señal 3 cancelaciones: 89% ❌ ACTIVA<br/>Score: 3/3 → ATAQUE CONFIRMADO

    VS->>COL: 📊 [t0] inicio detección CEP
    VS->>SEG: HTTP — evento ataque {actor_id, ip, jwt, señales, score: 0.97}
    VS->>LOG: HTTP — registrar evento {payload + traza CEP}
    VS->>COL: 📊 [t1] detección completada

    Note over INV: Inventarios NUNCA recibe<br/>ninguna de las 47 órdenes ✓

    SEG->>JWT: HTTP — revocar JWT actor
    SEG->>GW: HTTP — bloquear IP temporal (24 h)
    SEG->>COL: 📊 [t2] acciones de bloqueo completadas

    VS-->>GO: ✗ orden rechazada
    GO-->>GW: HTTP 429
    GW-->>INY: HTTPS 429 — sesión suspendida

    Note over COL: ✅ t1 - t0 < 300 ms<br/>✅ ordenes_en_inventario = 0<br/>✅ stock_delta = 0<br/>✅ respuesta 429 sin revelar señales CEP
```

### 5.2 Cómo se detecta que el ataque fue bloqueado

| Evidencia | Qué se verifica | Cómo se obtiene |
|---|---|---|
| Evento en Log de Auditoría | Registro del ataque en MongoDB | `db.auditoria.findOne({actor_id: "bot_8821"})` |
| Inventario intacto | `stock(COCA-COLA-350) == stock_inicial` | `GET /inventario/COCA-COLA-350` |
| JWT revocado | Token inválido en peticiones posteriores | Nueva petición con mismo JWT → debe retornar 401 |
| IP bloqueada | IP en lista del API Gateway | `GET /gateway/blocklist` |
| Tiempo CEP < 300 ms | `t1 - t0 < 300` | Colector de métricas |

### 5.3 Casos de prueba

#### CP-B1 — Tendero legítimo (control negativo)

| Campo | Valor |
|---|---|
| Actor | `tendero_001` con JWT válido · 4 órdenes en 60 s · SKUs variados · 0% cancelaciones |
| Señales CEP | 0 activas |
| Resultado esperado | Sin bloqueo · órdenes procesadas · sin eventos de seguridad |

#### CP-B2 — Ataque completo: 3 señales activas (caso crítico)

| Campo | Valor |
|---|---|
| Actor | `bot_8821` · 47 órdenes en 60 s · 43 con `COCA-COLA-350` · 89% cancelaciones |
| Señales CEP | Rate ❌ · Concentración ❌ · Cancelaciones ❌ |
| Resultado esperado | Bloqueado < 300 ms · stock intacto · JWT revocado · IP bloqueada |

#### CP-B3 — Una señal activa (validación de falso positivo)

| Campo | Valor |
|---|---|
| Actor | `tendero_002` · rate alto (8 órd/min) · SKUs variados · 0% cancelaciones |
| Señales CEP | Solo rate ❌ (1/3) |
| Resultado esperado | Orden **NO bloqueada** — 1 señal no alcanza el umbral ≥ 2 |

#### CP-B4 — Dos señales activas (umbral mínimo)

| Campo | Valor |
|---|---|
| Actor | `bot_5555` · rate alto ❌ · concentración SKU ❌ · cancelaciones dentro del umbral ✓ |
| Señales CEP | 2/3 activas |
| Resultado esperado | Orden bloqueada — igual que CP-B2 |

### 5.4 Métricas del Experimento B

| Métrica | Origen | Criterio |
|---|---|---|
| `t0_inicio_cep` | Timestamp en VS al iniciar evaluación | — (referencia) |
| `t1_deteccion` | Timestamp en VS al confirmar ataque | `t1 - t0 < 300 ms` ← **criterio ASR-2** |
| `t2_bloqueo` | Timestamp en SEG al completar JWT + IP block | `t2 - t0 < 500 ms` |
| `ordenes_en_inventario` | Contador en Inventarios | `== 0` |
| `stock_delta` | Stock antes vs. después | `== 0` |
| `codigo_respuesta` | Respuesta al inyector | `== 429` |
| `cuerpo_respuesta` | Cuerpo del 429 | Sin "rate", "SKU", "CEP" ni umbrales |

---

## 6. Diagrama de flujo de decisión del experimento

```mermaid
flowchart TD
    START([Inicio]) --> INIT[Inicializar harness\nStock = 9 · CEP limpio\nINV-Standby activo en modo pasivo]

    INIT --> EXP_A[EXPERIMENTO A\nCP-A1 a CP-A5]

    EXP_A --> A_HB{HeartBeat clasificado\npublicado al topic correcto?}
    A_HB -->|No| A_FAIL_1[❌ VALCOH no detecta\nASR-1 no cumplido]
    A_HB -->|Sí| A_TIPO{tipo_heartbeat ==\ntipo_falla_inyectada?}
    A_TIPO -->|No| A_FAIL_2[❌ Clasificación incorrecta\nASR-1 no cumplido]
    A_TIPO -->|Sí| A_TIME{t1 - t0 < 300 ms?}
    A_TIME -->|No| A_FAIL_3[❌ Latencia excede 300 ms\nASR-1 no cumplido]
    A_TIME -->|Sí| A_STOCK{stock_final correcto?}
    A_STOCK -->|No| A_FAIL_4[❌ Corrección incompleta]
    A_STOCK -->|Sí| A_FAIL{CP-A5: failover\ncompletado?}
    A_FAIL -->|No| A_FAIL_5[❌ Failover no funciona\nRedundancia pasiva no cumplida]
    A_FAIL -->|Sí| A_OK[✅ H1 CONFIRMADA\nASR-1 cumplido]

    A_OK --> RESET[Reinicializar\nStock = 9 · CEP limpio]
    A_FAIL_1 & A_FAIL_2 & A_FAIL_3 & A_FAIL_4 & A_FAIL_5 --> RESET

    RESET --> EXP_B[EXPERIMENTO B\nCP-B1 a CP-B4]

    EXP_B --> B_CEP{CEP detecta ≥ 2 señales\nen < 300 ms?}
    B_CEP -->|No| B_FAIL_1[❌ CEP no detecta\nASR-2 no cumplido]
    B_CEP -->|Sí| B_INV{ordenes_en_inventario == 0?}
    B_INV -->|No| B_FAIL_2[❌ Inventario afectado\nASR-2 no cumplido]
    B_INV -->|Sí| B_JWT{JWT revocado\ne IP bloqueada?}
    B_JWT -->|No| B_FAIL_3[❌ Bloqueo no ejecutado]
    B_JWT -->|Sí| B_OK[✅ H2 CONFIRMADA\nASR-2 cumplido]

    B_OK & B_FAIL_1 & B_FAIL_2 & B_FAIL_3 --> END([Emitir veredicto final])
```

---

## 7. Trade-offs observables en el experimento

| Trade-off | Dónde se mide | Decisión arquitectónica |
|---|---|---|
| Self-test añade cómputo local (ASR-1) | `t_self_test` en cada ciclo | VALCOH opera en memoria (< 50 ms); no consulta MongoDB en el path crítico |
| HeartBeat expandido aumenta tráfico en NATS | Tamaño del payload en el Colector | El payload expandido es < 1 KB; NATS JetStream soporta hasta 1 MB; sin impacto práctico |
| Router del Monitor añade paso de clasificación | `t_clasificacion_monitor` | El switch por tipo es O(1); latencia adicional < 10 ms |
| INV-Standby consume recursos en idle | Pod idle en Kubernetes | Se implementa con requests/limits mínimos (no recibe tráfico en condiciones normales) |
| Redundancia activa rechazada | — | Exigiría consenso distribuido (Raft/Paxos) y añadiría latencia al path crítico de reserva, incompatible con < 300 ms del ASR-1 |
| Latencia del CEP (siempre activo, ASR-2) | `t1 - t0` en CP-B1 | El CEP evalúa cada orden; si supera 300 ms bajo carga de 25 req/s, se requiere escalar horizontalmente la Validación CEP |

---

## 8. Procedimiento de ejecución

```
1. SETUP DEL HARNESS
   a. docker-compose up (levanta todos los contenedores)
   b. Inicializar MongoDB: stock COCA-COLA-350 = 9, AGUA-500 = 100, ARROZ-1KG = 50
   c. Inicializar NATS: crear streams heartbeat.inventario.*, correccion.*, failover.*
   d. Verificar que INV-Standby está replicando (Secondary en el replica set)
   e. Verificar Colector suscrito a metrics.* y heartbeat.inventario.*
   f. GET /health en cada servicio → 200 OK

2. EXPERIMENTO A — Inconsistencias de inventario (ASR-1)
   a. CP-A1 (control) → verificar HeartBeat tipo SELF_TEST_OK, sin correcciones
   b. Reinicializar stock = 9
   c. CP-A2 (stock negativo) → registrar t0, t1 → verificar tipo STOCK_NEGATIVO, rollback, stock = 9
   d. Reinicializar stock = 9
   e. CP-A3 (concurrencia) → lanzar dos hilos paralelos → verificar ESTADO_CONCURRENTE, stock = 3
   f. Reinicializar stock = 9
   g. CP-A4 (divergencia) → inyectar divergencia en BD → verificar DIVERGENCIA_RESERVAS, reconciliación
   h. Reinicializar stock = 9
   i. CP-A5 (failover) → forzar SELF_TEST_FAILED → verificar failover a INV-Standby, t_failover < 500 ms
   j. Documentar: H1 CONFIRMADA o REFUTADA + métricas

3. EXPERIMENTO B — DDoS de negocio (ASR-2)
   a. CP-B1 (control) → verificar sin bloqueo
   b. Reinicializar ventana CEP
   c. CP-B2 (ataque 3 señales) → registrar t0, t1 → verificar bloqueo, stock intacto, JWT revocado
   d. Verificar JWT inválido: POST /ordenes con mismo token → 401
   e. Reinicializar CEP + unlock JWT + unlock IP
   f. CP-B3 (1 señal) → verificar que orden NO fue bloqueada
   g. Reinicializar CEP
   h. CP-B4 (2 señales) → verificar bloqueo igual que CP-B2
   i. Documentar: H2 CONFIRMADA o REFUTADA + métricas

4. ANÁLISIS DE RESULTADOS
   a. Exportar CSV con métricas del Colector
   b. Calcular percentiles p50/p95/p99 de t1-t0 para A y B
   c. Verificar invariantes de inventario en ambos experimentos
   d. Documentar trade-offs observados
   e. Emitir veredicto por ASR
```

---

## 9. Criterio final de validación de la arquitectura

La arquitectura propuesta **cumple los ASRs** si se cumplen todas las condiciones:

| # | Condición | ASR | Veredicto posible |
|---|---|---|---|
| 1 | `t1 - t0 < 300 ms` para los 4 tipos de inconsistencia (CP-A2 a CP-A5) | ASR-1 | CUMPLIDO / NO CUMPLIDO |
| 2 | `tipo_heartbeat == tipo_falla_inyectada` en todos los casos | ASR-1 (precisión) | CUMPLIDO / NO CUMPLIDO |
| 3 | `stock_final == stock_pre_falla` tras rollback / reconciliación | ASR-1 | CUMPLIDO / NO CUMPLIDO |
| 4 | Failover a INV-Standby completado en CP-A5 | ASR-1 (redundancia pasiva) | CUMPLIDO / NO CUMPLIDO |
| 5 | `t1 - t0 < 300 ms` en CP-B2 (detección CEP) | ASR-2 | CUMPLIDO / NO CUMPLIDO |
| 6 | `ordenes_en_inventario == 0` y `stock_delta == 0` en CP-B2 | ASR-2 | CUMPLIDO / NO CUMPLIDO |
| 7 | CP-B3 no fue bloqueado (falso positivo) | ASR-2 (precisión) | ACEPTABLE / NO ACEPTABLE |

Si alguna condición falla, el experimento identifica qué componente introduce la desviación y orienta el ajuste arquitectónico necesario.
