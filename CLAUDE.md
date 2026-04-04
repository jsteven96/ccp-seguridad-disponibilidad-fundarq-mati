# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Propósito del repositorio

Este repositorio contiene documentación de arquitectura de software para el CCP (Centro de Control de Pedidos), específicamente los escenarios de los Atributos de Calidad de Disponibilidad y Seguridad (ASR). Es un proyecto académico de maestría en arquitectura de software.

Los documentos describen escenarios de calidad con diagramas de secuencia Mermaid y tablas de decisiones arquitectónicas.

## Estructura del contenido

Cada archivo `ASR_escenario*.md` sigue el mismo patrón:
1. **Contexto** — descripción del escenario y tácticas activas
2. **Diagrama de secuencia** — en formato Mermaid (bloque ` ```mermaid `)
3. **Notas de arquitectura** — tabla con decisiones y razonamientos

## Escenarios documentados

| Archivo | Escenario | Tácticas |
|---|---|---|
| `ASR_escenario1_happy_path.md` | Flujo exitoso (HeartBeat OK) | Detección HeartBeat, Validación de Seguridad, procesamiento paralelo |
| `ASR_escenario2_heartbeat_negativo.md` | HeartBeat detecta inventario negativo → rollback | Detección HeartBeat, Corrector, enmascaramiento, log de auditoría |
| `ASR_escenario3_ddos_detectado.md` | Validación de seguridad detecta ataque DDoS | CEP con 3 señales, revocación de acceso, log independiente |

## Componentes del sistema

- **Gestor de Órdenes (GO)** — punto de entrada; recibe órdenes del tendero
- **Validación de Seguridad (VS)** — analizador CEP con ventana deslizante de 60 s; evalúa 3 señales (rate, concentración SKU, tasa de cancelación); umbral ≥ 2 señales = ataque confirmado
- **Módulo de Inventarios (INV)** — publica HeartBeat continuo; detecta stock negativo internamente
- **Gestor de Pedidos (GP)** — registra pedidos y dispara el Corrector
- **Monitor (MON)** — se suscribe pasivamente al HeartBeat; orquesta la reacción ante fallas
- **Corrector (CORR)** — ejecuta rollback coordinado (inventario + pedido) en paralelo; desacoplado del Monitor
- **Módulo de Seguridad (SEG)** — revoca JWT, bloquea IP temporalmente (revisión en 24 h), alerta al equipo
- **Log de Auditoría** — independiente del Módulo de Seguridad para garantizar persistencia forense

## Convenciones de los documentos

- Los diagramas usan `autonumber` y bloques `Note over` para explicar lógica interna
- Las tablas de "Notas de arquitectura" deben incluir columnas: Momento/Elemento, Decisión/Táctica, Razonamiento
- Los mensajes al tendero usan respuestas enmascaradas: nunca exponen errores internos ni criterios de detección
- El HeartBeat de baja latencia (< 300 ms) es un requisito explícito para minimizar la ventana de stock negativo
