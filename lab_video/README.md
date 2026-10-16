# Laboratorio de imagen a video

Compara modelos de imagen a video con las mismas entradas antes de integrar
ninguno al pipeline. No toca `crearvideos/`: tiene su propio entorno y CLI.

## Preparar (una vez)

```
uv venv lab_video\.venv --python 3.12
uv pip install --python lab_video\.venv\Scripts\python.exe google-genai numpy opencv-python-headless python-dotenv
```

Usa `GEMINI_API_KEY` del `.env` (o `LAB_VIDEO_GEMINI_API_KEY`). Veo y Omni no
tienen tier gratis: el proyecto de la clave necesita facturación.

## Usar (desde la raíz del proyecto)

```
lab_video\.venv\Scripts\python.exe -m lab_video generar --solo-ver
lab_video\.venv\Scripts\python.exe -m lab_video generar --modelos veo-3.1-fast --imagenes p000
lab_video\.venv\Scripts\python.exe -m lab_video comparar
lab_video\.venv\Scripts\python.exe -m lab_video medir algun_clip.mp4
```

- Entradas (imagen + prompt de movimiento): `entradas.json`.
- Modelos y precios (con la fecha en que se verificaron): `config.py`.
- Salidas: `resultados/<fecha>/` con los clips, `resultados.json` y `comparar.html`.

## Control de gasto

`--solo-ver` no gasta. `--max-usd` (5 por defecto) corta antes de empezar si
el estimado lo supera. Siempre pide confirmación (o `--si`). Un clip ya
generado con la misma imagen, prompt, modelo, duración, aspecto y resolución
no se vuelve a pedir. Máximo dos generaciones a la vez.

## Costo "real"

- **Omni Flash**: la API devuelve los tokens usados; el costo real es tokens ×
  precio publicado.
- **Veo**: la API no informa costo. Se guarda la tarifa aplicada (precio/s ×
  segundos pedidos), que es lo que Google cobra si el video se genera. Para
  confirmarlo al céntimo, mirar la facturación del proyecto en Google Cloud.

## Índice de movimiento local

Residuo del flujo óptico tras ajustar un afín global (zoom/paneo), sobre el
flujo total, con fotogramas separados 1/8 s. Calibrado: control sintético con
solo un objeto moviéndose = 1,00; clips Ken Burns de CrearVideos = 0,05–0,38
(media ~0,25). Medir siempre clips sin subtítulos.

## Sumar otro proveedor

Una clase que herede de `proveedores.base.VideoProvider`, registrada en
`proveedores/__init__.py`, y sus modelos en `config.MODELOS`.
