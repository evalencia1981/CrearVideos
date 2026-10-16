"""Módulo 2 — Estudio: investigar qué funciona en YouTube antes de producir.

El módulo 1 (el resto del paquete) convierte un guion en video. Este decide qué
video vale la pena hacer: analiza videos y canales que ya funcionan, mide si un
video rindió por encima de lo normal de su canal y extrae su "ADN" (gancho,
estructura, fórmula de título, miniatura, tono) para reutilizarlo sin copiar.

Sin YouTube Data API: todo sale de yt-dlp, que lee la página pública. Lo que se
pierde es la búsqueda por palabra clave a gran escala, no el análisis.
"""
