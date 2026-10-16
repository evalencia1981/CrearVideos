---
name: analizar-youtube
description: Analiza un video o canal de YouTube con el módulo Estudio de CrearVideos (outliers, gancho, estructura, fórmula de título, miniatura, ideas) y lo profundiza. Úsala cuando el usuario pegue una URL de YouTube o un @canal y quiera saber por qué funciona, sacar su ADN o ideas para su propio canal.
---

# Analizar YouTube (módulo Estudio)

1. Ejecuta el analizador. Detecta solo si es video o canal:

   ```powershell
   $env:PYTHONIOENCODING="utf-8"; .venv\Scripts\python -m crearvideos analizar "<url o @canal>"
   ```

   Opciones de canal: `--videos 30` (cuántos leer por pestaña), `--profundidad 3`
   (cuántos outliers se leen a fondo). `--sin-ia` salta el análisis de Pollinations
   si solo quieres datos + transcripción para analizar tú.

2. Lee lo que dejó en `estudio/videos/<id>/` o `estudio/canales/<handle>/`:
   `informe.md`, `informe.json`, `transcripcion.txt` (o `transcripciones/`) y la
   miniatura (`miniatura.jpg` o `miniaturas/`). Mira la miniatura con Read.

3. Revisa el informe contra la transcripción y la miniatura, y corrige lo que la IA
   se haya inventado. Las métricas (vistas, múltiplo, palabras/min) están medidas:
   no las cambies. Los ingresos son una estimación por RPM de nicho: dilo siempre.

4. Responde al usuario en español con lo accionable: por qué funcionó (con citas y
   minutos), la fórmula de título, el patrón de gancho y estructura, y ideas
   originales. Si pide un guion, escríbelo en el formato del módulo de producción
   (cabecera `estilo/paleta/evitar` del informe + escenas separadas por `---` con
   `[imagen: ...]`) y guárdalo en `guiones/`.

Replica patrones, nunca el guion ni las frases del video analizado: YouTube
desmonetiza el contenido reutilizado.
