# Spec: Metricas, Observabilidad y Validacion Final de ASRs

## Objetivo

Implementar el script `validate_asrs.py` que orquesta la ejecucion completa de los 9 casos de prueba (5 del Experimento A + 4 del Experimento B), recoge metricas de los logs JSON de cada servicio, imprime una tabla consolidada de resultados, y emite el veredicto final sobre las hipotesis H1 y H2.

## Alcance

**En scope:**
- Script `validate_asrs.py` como punto de entrada unico para ejecutar ambos experimentos
- Recoleccion de metricas de logs JSON de cada servicio
- Tabla consolidada de resultados con checkmarks por criterio
- Calculo de percentiles p50/p95/p99 de latencias
- Verificacion de invariantes de inventario
- Emision de veredicto por hipotesis (H1/H2) y por ASR (ASR-1/ASR-2)
- Generacion de reporte `resultados_experimento.md` con formato Markdown
- Verificacion de trade-offs observables

**Fuera de scope:**
- Implementacion de los servicios (otras specs)
- Implementacion de los scripts de caso de prueba individuales (specs de harness)

## Criterios de Aceptacion

- [ ] `python validate_asrs.py` ejecuta los 9 casos de prueba en secuencia
- [ ] Entre cada caso, reinicia estado (stock, ventana CEP, blocklist)
- [ ] Recoge metricas de cada caso: t_self_test, t_clasificacion_monitor, t_deteccion, t_total
- [ ] Imprime tabla con formato:
  ```
  | Caso  | Veredicto | t_deteccion (ms) | Criterio      | Resultado |
  |-------|-----------|------------------|---------------|-----------|
  | CP-A1 | PASS      | N/A              | Control       | OK        |
  | CP-A2 | PASS      | 45.2             | < 300 ms      | PASS      |
  ...
  ```
- [ ] Calcula y muestra percentiles p50/p95/p99 de latencias para Exp A y Exp B
- [ ] Emite veredicto final:
  - H1 CONFIRMADA si CP-A2 a CP-A5 todos PASS
  - H2 CONFIRMADA si CP-B2, CP-B3, CP-B4 todos PASS
- [ ] Genera archivo `resultados_experimento.md` con el reporte completo
- [ ] El reporte incluye seccion de trade-offs observados con datos reales
- [ ] Exit code 0 si ambos ASRs cumplen, 1 si alguno falla

## Inputs Requeridos

- Todos los servicios desplegados y operativos
- Scripts de Experimento A: `experiments/experiment_a/run_experiment_a.py`
- Scripts de Experimento B: `experiments/experiment_b/run_experiment_b.py`
- Archivos de resultados JSON generados por cada orquestador

## Outputs Esperados

| Archivo | Descripcion |
|---|---|
| `scripts/validate_asrs.py` | Script principal de validacion |
| `scripts/metrics_collector.py` | Recolector de metricas de logs de servicios |
| `scripts/report_generator.py` | Generador de reporte Markdown |
| `resultados_experimento.md` | Reporte final del experimento (generado al ejecutar) |
| `resultados_metricas.json` | Metricas crudas en JSON (generado al ejecutar) |

## Agente Responsable

`metrics-validator`

## Convenciones a Respetar

- HeartBeat < 300 ms como criterio principal de ASR-1
- Deteccion CEP < 300 ms como criterio principal de ASR-2
- Tabla de resultados sigue formato con columnas: Caso, Veredicto, t_deteccion, Criterio, Resultado
- El reporte final debe ser autocontenido: un lector sin contexto previo puede entender que se probo y que resulto
- Los trade-offs documentados deben coincidir con los definidos en seccion 7 del diseno del experimento
- Criterios finales segun seccion 9 del diseno (7 condiciones)

## Pasos de Ejecucion

1. **Crear estructura de directorios:**
   ```
   scripts/
   ```

2. **Implementar `scripts/metrics_collector.py`:**
   ```python
   """Recolector de metricas de los logs JSON de cada servicio."""
   import json
   import statistics

   class MetricsCollector:
       def __init__(self):
           self.metricas = []

       def agregar(self, caso: str, resultado: dict):
           """Agrega resultado de un caso de prueba."""
           self.metricas.append({
               "caso": caso,
               "veredicto": resultado.get("veredicto"),
               "metricas": resultado.get("metricas", {}),
               "evidencias": resultado.get("evidencias", {}),
           })

       def obtener_latencias(self, prefijo: str) -> list[float]:
           """Obtiene latencias de deteccion para casos con prefijo dado."""
           latencias = []
           for m in self.metricas:
               if m["caso"].startswith(prefijo) and m["caso"] != f"{prefijo}1":
                   t = m["metricas"].get("t_deteccion_ms")
                   if t is not None and t != float("inf"):
                       latencias.append(t)
           return latencias

       def calcular_percentiles(self, latencias: list[float]) -> dict:
           if not latencias:
               return {"p50": None, "p95": None, "p99": None}
           sorted_l = sorted(latencias)
           n = len(sorted_l)
           return {
               "p50": sorted_l[int(n * 0.50)],
               "p95": sorted_l[int(n * 0.95)] if n >= 20 else sorted_l[-1],
               "p99": sorted_l[int(n * 0.99)] if n >= 100 else sorted_l[-1],
           }

       def verificar_condiciones_finales(self) -> dict:
           """Verifica las 7 condiciones de la seccion 9 del diseno."""
           resultados_por_caso = {m["caso"]: m for m in self.metricas}
           condiciones = {}

           # Condicion 1: t1-t0 < 300ms para CP-A2 a CP-A5
           c1_casos = ["CP-A2", "CP-A3", "CP-A4", "CP-A5"]
           c1 = all(
               resultados_por_caso.get(c, {}).get("metricas", {}).get("t_deteccion_ok", False)
               for c in c1_casos if c in resultados_por_caso
           )
           condiciones["C1_latencia_asr1"] = c1

           # Condicion 2: tipo_heartbeat correcto en todos los casos
           c2 = all(
               resultados_por_caso.get(c, {}).get("evidencias", {}).get("heartbeat_tipo") is not None
               for c in c1_casos if c in resultados_por_caso
           )
           condiciones["C2_clasificacion"] = c2

           # Condicion 3: stock_final correcto tras rollback
           c3_a2 = resultados_por_caso.get("CP-A2", {}).get("evidencias", {}).get("stock_restaurado", False)
           condiciones["C3_stock_restaurado"] = c3_a2

           # Condicion 4: failover completado
           c4 = resultados_por_caso.get("CP-A5", {}).get("evidencias", {}).get("standby_funcional", False)
           condiciones["C4_failover"] = c4

           # Condicion 5: t_deteccion CEP < 300ms en CP-B2
           c5 = resultados_por_caso.get("CP-B2", {}).get("metricas", {}).get("t_deteccion_ok", False)
           condiciones["C5_latencia_asr2"] = c5

           # Condicion 6: stock intacto en CP-B2
           c6 = resultados_por_caso.get("CP-B2", {}).get("evidencias", {}).get("stock_intacto", False)
           condiciones["C6_stock_intacto"] = c6

           # Condicion 7: CP-B3 no fue bloqueado
           c7 = resultados_por_caso.get("CP-B3", {}).get("veredicto") == "PASS"
           condiciones["C7_sin_falso_positivo"] = c7

           return condiciones
   ```

3. **Implementar `scripts/report_generator.py`:**
   ```python
   """Genera reporte Markdown con resultados del experimento."""
   import json
   from datetime import datetime

   def generar_reporte(collector, output_path: str = "resultados_experimento.md"):
       condiciones = collector.verificar_condiciones_finales()
       h1 = all(condiciones[k] for k in ["C1_latencia_asr1", "C2_clasificacion",
                                            "C3_stock_restaurado", "C4_failover"])
       h2 = all(condiciones[k] for k in ["C5_latencia_asr2", "C6_stock_intacto",
                                            "C7_sin_falso_positivo"])

       latencias_a = collector.obtener_latencias("CP-A")
       latencias_b = collector.obtener_latencias("CP-B")
       perc_a = collector.calcular_percentiles(latencias_a)
       perc_b = collector.calcular_percentiles(latencias_b)

       reporte = f"""# Resultados del Experimento -- Validacion de ASRs CCP

> Fecha: {datetime.now().isoformat()}
> Cluster: Kind (1 control-plane + 2 workers)

---

## Veredicto Final

| ASR | Hipotesis | Veredicto |
|-----|-----------|-----------|
| ASR-1 (Disponibilidad) | H1: Deteccion de inconsistencia < 300 ms | **{'CONFIRMADA' if h1 else 'REFUTADA'}** |
| ASR-2 (Seguridad) | H2: Deteccion de DDoS < 300 ms | **{'CONFIRMADA' if h2 else 'REFUTADA'}** |

---

## Resultados por Caso de Prueba

### Experimento A -- Inconsistencias de Inventario (ASR-1)

| Caso | Veredicto | t_deteccion (ms) | Criterio | Resultado |
|------|-----------|-------------------|----------|-----------|
"""
       for m in collector.metricas:
           if m["caso"].startswith("CP-A"):
               t = m["metricas"].get("t_deteccion_ms", "N/A")
               t_str = f"{t:.1f}" if isinstance(t, float) else str(t)
               criterio = "Control" if m["caso"] == "CP-A1" else "< 300 ms"
               check = "PASS" if m["veredicto"] == "PASS" else "FAIL"
               reporte += f"| {m['caso']} | {m['veredicto']} | {t_str} | {criterio} | {check} |\n"

       reporte += f"""
**Percentiles de latencia (Exp A):** p50={perc_a['p50']}, p95={perc_a['p95']}, p99={perc_a['p99']}

### Experimento B -- DDoS de Negocio (ASR-2)

| Caso | Veredicto | t_deteccion (ms) | Criterio | Resultado |
|------|-----------|-------------------|----------|-----------|
"""
       for m in collector.metricas:
           if m["caso"].startswith("CP-B"):
               t = m["metricas"].get("t_deteccion_ms", "N/A")
               t_str = f"{t:.1f}" if isinstance(t, float) else str(t)
               criterio = "Control" if m["caso"] == "CP-B1" else "< 300 ms"
               if m["caso"] == "CP-B3":
                   criterio = "No bloquear"
               check = "PASS" if m["veredicto"] == "PASS" else "FAIL"
               reporte += f"| {m['caso']} | {m['veredicto']} | {t_str} | {criterio} | {check} |\n"

       reporte += f"""
**Percentiles de latencia (Exp B):** p50={perc_b['p50']}, p95={perc_b['p95']}, p99={perc_b['p99']}

---

## Condiciones de Validacion (Seccion 9 del Diseno)

| # | Condicion | ASR | Resultado |
|---|-----------|-----|-----------|
| 1 | t1-t0 < 300 ms para 4 tipos de inconsistencia | ASR-1 | {'CUMPLIDO' if condiciones['C1_latencia_asr1'] else 'NO CUMPLIDO'} |
| 2 | tipo_heartbeat == tipo_falla_inyectada | ASR-1 | {'CUMPLIDO' if condiciones['C2_clasificacion'] else 'NO CUMPLIDO'} |
| 3 | stock_final == stock_pre_falla tras rollback | ASR-1 | {'CUMPLIDO' if condiciones['C3_stock_restaurado'] else 'NO CUMPLIDO'} |
| 4 | Failover a INV-Standby completado | ASR-1 | {'CUMPLIDO' if condiciones['C4_failover'] else 'NO CUMPLIDO'} |
| 5 | t_deteccion CEP < 300 ms | ASR-2 | {'CUMPLIDO' if condiciones['C5_latencia_asr2'] else 'NO CUMPLIDO'} |
| 6 | ordenes_en_inventario == 0, stock_delta == 0 | ASR-2 | {'CUMPLIDO' if condiciones['C6_stock_intacto'] else 'NO CUMPLIDO'} |
| 7 | CP-B3 no fue bloqueado (falso positivo) | ASR-2 | {'ACEPTABLE' if condiciones['C7_sin_falso_positivo'] else 'NO ACEPTABLE'} |

---

## Trade-offs Observados

| Trade-off | Medicion | Impacto |
|-----------|----------|---------|
| Self-test anade computo local | t_self_test en cada ciclo | [Completar con datos reales] |
| HeartBeat expandido en NATS | Tamano payload | [Completar con datos reales] |
| Router del Monitor | t_clasificacion_monitor | [Completar con datos reales] |
| INV-Standby idle | Recursos consumidos | [Completar con datos reales] |
| CEP siempre activo | t_deteccion en CP-B1 | [Completar con datos reales] |
"""
       with open(output_path, "w") as f:
           f.write(reporte)

       return reporte
   ```

4. **Implementar `scripts/validate_asrs.py`:**
   ```python
   #!/usr/bin/env python3
   """
   Validador completo de ASRs del CCP.

   Ejecuta los 9 casos de prueba en secuencia, recoge metricas,
   y emite veredicto final sobre H1 (ASR-1) y H2 (ASR-2).

   Uso: python validate_asrs.py
   """
   import asyncio
   import json
   import sys
   import subprocess
   from metrics_collector import MetricsCollector
   from report_generator import generar_reporte

   async def ejecutar_experimento(script_path: str) -> list[dict]:
       """Ejecuta un script de experimento y retorna los resultados."""
       result = subprocess.run(
           ["python", script_path],
           capture_output=True, text=True, timeout=600,
       )
       # Parsear resultados JSON del output
       resultados = []
       for line in result.stdout.split("\n"):
           line = line.strip()
           if line.startswith("{"):
               try:
                   resultados.append(json.loads(line))
               except json.JSONDecodeError:
                   pass
       return resultados

   async def main():
       collector = MetricsCollector()

       print("=" * 70)
       print("VALIDACION COMPLETA DE ASRs -- CCP")
       print("=" * 70)

       # Experimento A
       print("\n>>> EXPERIMENTO A: Inconsistencias de inventario (ASR-1)")
       resultados_a = await ejecutar_experimento(
           "experiments/experiment_a/run_experiment_a.py"
       )
       for r in resultados_a:
           collector.agregar(r["caso"], r)

       # Experimento B
       print("\n>>> EXPERIMENTO B: DDoS de negocio (ASR-2)")
       resultados_b = await ejecutar_experimento(
           "experiments/experiment_b/run_experiment_b.py"
       )
       for r in resultados_b:
           collector.agregar(r["caso"], r)

       # Tabla de resultados
       print("\n" + "=" * 70)
       print("TABLA DE RESULTADOS")
       print("=" * 70)
       print(f"{'Caso':<8} {'Veredicto':<10} {'t_deteccion (ms)':<18} {'Criterio':<15} {'Resultado'}")
       print("-" * 70)
       for m in collector.metricas:
           t = m["metricas"].get("t_deteccion_ms", "N/A")
           t_str = f"{t:.1f}" if isinstance(t, float) else str(t)
           criterio = "Control" if m["caso"].endswith("1") else "< 300 ms"
           if m["caso"] == "CP-B3":
               criterio = "No bloquear"
           check = "PASS" if m["veredicto"] == "PASS" else "FAIL"
           print(f"{m['caso']:<8} {m['veredicto']:<10} {t_str:<18} {criterio:<15} {check}")

       # Condiciones finales
       condiciones = collector.verificar_condiciones_finales()
       h1 = all(condiciones[k] for k in ["C1_latencia_asr1", "C2_clasificacion",
                                            "C3_stock_restaurado", "C4_failover"])
       h2 = all(condiciones[k] for k in ["C5_latencia_asr2", "C6_stock_intacto",
                                            "C7_sin_falso_positivo"])

       print("\n" + "=" * 70)
       print("VEREDICTO FINAL")
       print("=" * 70)
       print(f"  H1 (ASR-1 Disponibilidad): {'CONFIRMADA' if h1 else 'REFUTADA'}")
       print(f"  H2 (ASR-2 Seguridad):      {'CONFIRMADA' if h2 else 'REFUTADA'}")

       # Generar reporte
       generar_reporte(collector)
       print("\nReporte generado: resultados_experimento.md")

       # Guardar metricas crudas
       with open("resultados_metricas.json", "w") as f:
           json.dump(collector.metricas, f, indent=2)
       print("Metricas guardadas: resultados_metricas.json")

       # Exit code
       sys.exit(0 if (h1 and h2) else 1)

   if __name__ == "__main__":
       asyncio.run(main())
   ```

5. **Verificar:**
   - Ejecutar `python scripts/validate_asrs.py` con todos los servicios activos
   - Confirmar que la tabla se imprime correctamente
   - Confirmar que `resultados_experimento.md` se genera
   - Confirmar que `resultados_metricas.json` contiene datos de los 9 casos
   - Verificar exit code correcto

## Notas de Arquitectura

| Elemento | Decision | Razonamiento |
|---|---|---|
| Script orquestador unico | `validate_asrs.py` como entry point | Un solo comando para ejecutar todo el experimento; facil de reproducir |
| Subprocess para experimentos | Ejecuta cada orquestador como proceso hijo | Aislamiento: si un experimento falla, el otro aun se ejecuta |
| Percentiles calculados en Python | No usa herramientas externas | Suficiente para el alcance del experimento academico |
| Reporte Markdown autocontenido | Incluye contexto, resultados y veredicto | Un lector del reporte puede entender todo sin acceso al codigo |
| 7 condiciones de seccion 9 | Mapeadas directamente del diseno | Trazabilidad directa entre diseno y verificacion |
