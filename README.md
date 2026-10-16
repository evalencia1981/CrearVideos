# CrearVideos

Convierte un guion de texto en un short vertical (1080x1920) con narración,
imágenes y subtítulos sincronizados palabra por palabra. Todo corre en tu PC.

Es una reimplementación local del flujo de ClipShort, sin suscripción: el modo
por defecto **no cuesta nada y no pide tarjeta**.

```
python -m crearvideos crear guiones\ejemplo.txt
```

## Qué hace

```
guion.txt
   |
   +-- se parte en escenas
   +-- cada escena -> voz (edge-tts)  -> audio + tiempo exacto de cada palabra
   +-- cada escena -> imagen          -> Pexels, local, Gemini u OpenAI
   +-- tiempos     -> subtitulos .ass -> resaltado palabra por palabra
   +-- ffmpeg      -> Ken Burns + corte seco + mezcla + subtitulos quemados
   |
video.mp4
```

La duración de cada escena no se adivina: se mide el audio generado y la
imagen dura exactamente lo que dura su narración.

## Repartir la app a otras personas

Para que otro la use sin instalar Python ni ffmpeg, se empaqueta en un solo
`.exe` que corre **en su PC**: sus videos, su ffmpeg, su GPU. No hay servidor,
no hay nube, nadie ve los archivos de nadie.

```powershell
# Entorno de compilación (Python 3.12: PyInstaller no va bien con 3.14)
uv venv --python 3.12 .venv-build
uv pip install --python .venv-build\Scripts\python.exe -r requirements.txt pyinstaller

# ffmpeg y ffprobe en bin\ (compilación "essentials", ~200 MB los dos)
# https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip

.venv-build\Scripts\python -m PyInstaller CrearVideos.spec --noconfirm --clean
```

Queda en `dist\CrearVideos.exe`. Se acompaña de [INSTALAR.md](INSTALAR.md), que
es lo que lee quien lo recibe.

**Qué cambia en la versión empaquetada** ([config.py](crearvideos/config.py)):
el programa no escribe donde está instalado, sino en
`Documentos\CrearVideos` — proyectos, guiones, música y claves. Dentro del
`.exe` solo viajan los archivos de solo lectura (la interfaz, ffmpeg, el script
de voz clonada). Se puede cambiar la carpeta de trabajo con `CREARVIDEOS_DATOS`.

**Qué no viaja dentro**: el entorno de voz clonada (5 GB de PyTorch y modelo).
Quien lo quiera lo instala aparte; el resto de la app funciona igual y avisa.

## Instalación

Necesitas Python 3.11+ y ffmpeg en el PATH.

```powershell
winget install Gyan.FFmpeg          # si no lo tienes
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

Y ya está: **no necesitas ninguna clave**. Por defecto las imágenes se generan
por IA con [Pollinations](https://pollinations.ai) (gratis, sin registro) y la
voz con edge-tts. Todo lo demás (Pexels, Gemini, OpenAI) es opcional.

## Revisión antes de generar

En la web, los botones de generar son **Revisar y crear** / **Revisar y montar**:
antes de arrancar se abre una pantalla con todo lo elegido —tipo de video,
formato, guion (escenas, palabras y duración estimada), voz, estilo, imágenes,
**costo estimado**, música, subtítulos— y dos botones: *Volver a ajustar* o
*Sí, generar*.

Desde ahí se puede **escuchar la voz** con un clic: genera una frase de muestra
con esa voz y ese efecto (2 s la primera vez, instantáneo después, se cachea en
`proyectos/_muestras/`). Es lo que evita esperar tres minutos para descubrir que
la voz era la de España o que el efecto no pegaba.

La revisión también sube las advertencias que suelen pasar desapercibidas, como
que *Música automática* no encontrará ninguna pista para ese estilo y el video
saldría mudo.

## Interfaz web (lo más cómodo)

Para no tocar la terminal: pega el guion, elige voz e imágenes, y mira el video
en el navegador.

```powershell
python -m crearvideos web --puerto 8090
```

Abre `http://127.0.0.1:8090` solo. Corre en tu PC, no se publica a internet.
(Sin `--puerto` usa el 8000; en esta máquina lo ocupa contascan.)
Es la forma más rápida de probar guiones, voces y estilos uno tras otro.

## Uso por línea de comandos

```powershell
# Ver cómo se partiría el guion, sin generar ni gastar nada
python -m crearvideos escenas guiones\ejemplo.txt

# Generar el video (gratis, imágenes de IA con Pollinations)
python -m crearvideos crear guiones\ejemplo.txt

# Fotos reales de stock (necesita PEXELS_API_KEY en .env)
python -m crearvideos crear guiones\ejemplo.txt --imagenes pexels

# Sin internet para las imágenes: degradados de color
python -m crearvideos crear guiones\ejemplo.txt --imagenes placeholder

# Elegir el modelo de imagen (ver: modelos)
python -m crearvideos modelos
python -m crearvideos crear guiones\ejemplo.txt --modelo gpt-image-1.5

# Con IA de pago (más estable)
python -m crearvideos crear guiones\ejemplo.txt --imagenes gemini

# Música de fondo: ver la biblioteca, elegirla sola, o dar una pista
python -m crearvideos musica
python -m crearvideos crear guiones\ejemplo.txt --estilo terror --musica auto
python -m crearvideos crear guiones\ejemplo.txt --musica assets\musica\general\lofi.mp3
python -m crearvideos crear guiones\ejemplo.txt --musica no

# Elegir otra voz
python -m crearvideos voces --idioma es
python -m crearvideos crear guiones\ejemplo.txt --voz es-ES-AlvaroNeural

# Borrar archivos intermedios, conservando el MP4
python -m crearvideos limpiar ejemplo
```

## Formato del guion

Texto corrido: se parte solo por oraciones.

```
El noventa y cinco por ciento del océano sigue sin explorar.
Conocemos mejor Marte que el fondo de nuestro planeta.
```

O escenas explícitas, para controlar exactamente qué imagen acompaña a qué frase:

```
estilo: fotografía cinematográfica submarina, iluminación tenue y azulada

---
El noventa y cinco por ciento del océano sigue sin explorar.
[imagen: vista aérea de un océano inmenso y oscuro al atardecer]

---
A once mil metros, la presión aplastaría un submarino.
[imagen: abismo submarino negro, un rayo de luz cayendo desde arriba]
```

- `estilo:` se concatena al prompt de **todas** las imágenes. Es lo que
  mantiene la coherencia visual entre escenas — el problema número uno de
  este tipo de video. Solo lo usan los proveedores de IA: como término de
  búsqueda en Pexels sería idéntico en todas las escenas, así que se ignora.
- Hay cuatro cabeceras más —`epoca:`, `protagonistas:`, `paleta:`, `evitar:`—
  que describen el mundo del video para el director. Son opcionales, no se
  narran, y en temas históricos o de ficción son lo que más cambia el
  resultado: ver [Director de imágenes](#director-de-imágenes-coherencia-entre-escenas).
- `[imagen: ...]` describe la imagen de esa escena. Sin él se usa el texto
  narrado, que funciona peor.

Cómo escribirlo depende de a quién le hablas:

- **Con IA (`pollinations`, `gemini`, `openai`)** se dibuja lo que pidas:
  cuanto más detalle, mejor. Aquí el `estilo:` sí cuenta.
- **Con `pexels`** se busca una foto, no se dibuja. Solo se usan las **tres
  primeras palabras con contenido, hasta la primera coma**: de
  `[imagen: abismo submarino profundo y negro, un rayo de luz]` sale la
  búsqueda `abismo submarino profundo`. Pon el sujeto concreto al principio y
  deja los matices para después de la coma. Si no encuentra nada, acorta solo
  (`abismo submarino` → `abismo`) antes de rendirse.

Es la misma línea para todos, así que puedes cambiar de proveedor sin reescribir
el guion.

## Director de imágenes (coherencia entre escenas)

Con los proveedores de IA, antes de generar cada imagen una IA de texto escribe
el prompt de cada escena. Trabaja en dos partes dentro de una misma llamada:

1. **La biblia visual.** Decide *una vez* para todo el video la época y el
   lugar, el mundo, la paleta, la luz, el medio, y el **reparto recurrente**:
   cada persona o criatura que sale en más de una escena, con su descripción
   física. También la lista de lo que **nunca** debe aparecer.
2. **Las escenas.** Cada prompt describe solo lo que *pasa* en ese momento.

Lo que de verdad da la coherencia no es pedírsela al modelo, sino que **el
ancla se concatena en Python**: el bloque de mundo y las descripciones del
reparto se pegan literalmente iguales en las 7 escenas. Pedirle "mantén todo
igual" era lo que se hacía antes, y derivaba igual — de "mujer con ropa humilde"
a "silueta con un mechón despeinado" en dos escenas.

También se acota en código el tamaño del prompt (`MAX_*` en `director.py`): como
mucho **3 personajes por escena** y un tope de palabras por bloque. Sin eso el
modelo mete al elenco entero y la acción de la escena queda sepultada bajo
seiscientas palabras de descripción física.

- Web: casilla **"Director de imágenes con IA"**.
- CLI: activo siempre; se apaga con `--sin-director`.
- Si la IA de texto falla, se cae al prompt simple y el video se genera igual,
  **pero ahora lo avisa** en el progreso. Callarlo era peor que el fallo: las
  imágenes salían de la narración cruda ("antes de los supermercados" → un
  supermercado) y nada lo delataba.
- Usa el token de Pollinations si está en `.env`. Dirigir un short entero son
  ~6000 tokens, unos **0.001 pollen**: nada al lado de las imágenes, y evita la
  cuota anónima, que hoy está agotada casi siempre.
- Los prompts se cachean en `images/_prompts.json`, así que rehacer el montaje
  no vuelve a llamar a la IA.
- Solo aplica a proveedores generativos; con `pexels`/`placeholder` se ignora.

### La cabecera del guion es lo que más ayuda

El director deduce la biblia del propio guion, pero adivina. Para temas
históricos o de ficción, dársela por escrito cambia el resultado por completo:

```text
estilo: documental
epoca: Paleolítico superior, Europa, 30.000 a.C. Última glaciación
protagonistas: una banda de seis cazadores-recolectores; seguimos a los mismos
paleta: ocre, hueso, pizarra, verde musgo apagado, luz fría de amanecer
evitar: metal, cerámica, agricultura, ropa moderna, edificios, texto
```

- `epoca:` acota qué puede aparecer en cuadro. Es la línea más rentable.
- `protagonistas:` fija a quién seguimos. Es la mayor palanca de coherencia:
  sin ella cada escena inventa gente nueva.
- `paleta:` unifica el color de todo el video.
- `evitar:` prohíbe los anacronismos que el modelo mete solo. Importa sobre todo
  cuando la narración *menciona* algo moderno para contrastar ("antes de los
  supermercados"): sin esta línea, el modelo dibuja el supermercado.

Ninguna es obligatoria y ninguna se narra: se quitan del texto antes de generar
la voz.

## Portada para TikTok / YouTube

Cada plataforma maneja la portada distinto: en **TikTok** la portada es un
*fotograma del propio video* (deslizas y eliges cuál), y en **YouTube** subes una
*imagen aparte* como miniatura. Por eso, al pasar una imagen con `--portada`:

- se **antepone al video** como fotograma de apertura (unos 2 s, con entrada
  suave), así en TikTok puedes elegirla de portada;
- se guarda además como **`portada.jpg`** en 1080×1920 dentro del proyecto, lista
  para subir de miniatura en YouTube.

```powershell
python -m crearvideos crear guion.txt --portada mi-portada.png
```

La imagen puede venir en cualquier proporción: se agranda y recorta a 9:16 sin
franjas negras. Anteponerla no descuadra nada —los subtítulos ya están quemados
en el contenido, que solo empieza unos segundos después—. En la web hay un campo
para subirla desde el navegador.

## Personaje de referencia (el mismo actor en todo el video)

Sube una o varias fotos de un personaje y todas las escenas se generan a partir
de ellas, así sale **el mismo** en el video entero — la coherencia es el problema
nº1 de estos shorts.

```powershell
# Una foto
python -m crearvideos crear guion.txt --referencia silbon.png

# Varias del mismo personaje: cara, cuerpo entero, perfil. Lo fijan mucho mejor.
python -m crearvideos crear guion.txt --referencia cara.jpg --referencia cuerpo.jpg
```

En la web tiene su propia sección, **Personaje de referencia**, con arrastrar y
soltar varias imágenes y una miniatura por cada una. (Antes iba colgado del campo
*Portada*, lo cual confundía las dos cosas; la casilla para reutilizar la portada
como referencia sigue ahí para cuando el personaje sale en ella.)

### Referencia y director, a la vez

Esto es lo que cambió. Antes, activar la referencia **apagaba el director**: había
que elegir entre fijar el personaje o tener la biblia visual. Ya no.

El motivo era que la edición iba siempre por **kontext**, que acepta una sola
referencia y cuya moderación rechaza las palabras de estilo. Los modelos actuales
aceptan varias referencias y tragan el prompt dirigido entero:

| Modelo | Referencias |
|---|---|
| `flux-klein` | 10 |
| `gpt-image-1.5` | 16 |
| `kontext` (respaldo) | 1 |
| `flux`, `phoenix` | ninguna |

Así que ahora **la referencia fija la cara y el director aporta la época, la
paleta y las prohibiciones**. Si eliges un modelo que no acepta referencias, se
cae a kontext con la primera imagen y la web te lo avisa antes de generar.

**Coste:** con `flux-klein` la edición vale lo mismo que generar, 0.005
pollen/imagen. Con kontext eran 0.03 — seis veces más.

### Cómo dar una buena referencia

- **Una sola persona por imagen**, recortada a ella. Medido: con una foto que
  tenía dos personas, el modelo las fusionó y sacó una figura con dos caras.
- Varias fotos del mismo personaje (frente, perfil, cuerpo entero) lo fijan mejor
  que una sola.
- Sirve cualquier formato que abra Pillow: se convierte a JPEG y se reduce a
  1024 px de lado antes de subirla, porque una foto de 12 MP no mejora el
  parecido y sí engorda cada petición.
- Si una escena se rechaza por moderación, reintenta solo (los fallos no gastan
  pollen).

## Velocidad del video

Se ajusta al generar o al descargar, **no en TikTok** (su control de velocidad es
para clips que grabas/editas dentro de la app; un MP4 terminado que subes conserva
su velocidad). Acelera o ralentiza imagen, voz y música por igual, como el control
de velocidad de un reproductor. La voz **mantiene su tono** (no queda de ardilla) y
los subtítulos, ya quemados en la imagen, se escalan solos sin descuadrarse.

```powershell
# Bakearla al generar
python -m crearvideos crear guion.txt --velocidad 1.25   # 25% más rápido
python -m crearvideos crear guion.txt --velocidad 0.9    # 10% más lento

# Reexportar un video YA hecho, sin regenerarlo (deja el original intacto)
python -m crearvideos velocidad mi-proyecto 1.25
```

En la web, bajo el video hay botones (0.75× … 1.5×): al pulsarlos se reexporta a
esa velocidad en unos segundos y se actualiza la descarga. El botón 1× vuelve al
original sin reexportar.

## Efectos de voz

La voz de edge-tts sale limpia y neutra. Con `--voz-fx` se le da carácter
—grave, de cripta, fantasmal— usando solo ffmpeg, sin descargar ni pagar nada:

```powershell
python -m crearvideos efectos                 # ver los efectos disponibles
python -m crearvideos crear guion.txt --voz-fx demonio
python -m crearvideos crear guion.txt --estilo terror   # 'auto' pone terror solo
python -m crearvideos crear guion.txt --voz-fx none      # voz limpia
```

| Clave | Efecto |
|---|---|
| `profunda` | Voz más grave, seria. Sirve para cualquier tema. |
| `terror` | Grave + reverb de cuarto de piedra + apagada. |
| `fantasma` | Muy grave, eco largo y temblor de ultratumba. |
| `demonio` | Tono muy bajo y amenazante. |
| `eco` | Eco cavernoso, sin cambiar el tono. |
| `radio` | Banda de teléfono: suena a transmisión vieja. |

Por defecto (`auto`) se aplica **terror** en los estilos de terror y suspenso,
**radio** en ciencia ficción, y nada en el resto —para no procesar la voz donde
no viene a cuento.

El efecto **no descuadra los subtítulos**. El punto es delicado: toda la
sincronía se apoya en los tiempos de palabra que reporta edge-tts. Por eso el
cambio de tono preserva la duración (se baja con `asetrate` y se compensa el
largo con `atempo`), y las colas de reverb/eco solo se añaden al final —el
pipeline vuelve a medir la duración real tras el efecto, así la cola nunca se
corta ni desplaza la imagen. Medido: video y audio quedan alineados al
milisegundo y el número de subtítulos no cambia.

## Música de fondo

La música **baja sola cuando entra la voz** y vuelve a subir en las pausas
(*ducking*). Eso es lo que permite que suene alta de verdad entre frases sin
tapar nunca la narración: con volumen fijo hay que dejarla tan baja que casi no
se oye. Medido en un video de prueba: **−74% mientras se habla, nivel completo
en los silencios**.

Las pistas van en `assets/musica/`, una carpeta por ambiente:

```text
assets/musica/
  general/      sirve para cualquier video
  tension/      terror, suspenso
  epico/        cinematográfico, fotorrealista, fantasía
  alegre/       anime, cartoon, cómic, pixel
  electronico/  ciencia ficción
  calmado/      acuarela, óleo, minimalista
```

Con `--musica auto` (el valor por defecto) se elige una pista del ambiente que
corresponde al estilo visual; si esa carpeta está vacía, cae a `general/`, y si
no hay nada, el video sale sin música. La elección es determinista sobre el
nombre del proyecto: rehacer el mismo video conserva su pista en vez de sonar
distinto cada vez.

**No se descarga música sola, a propósito.** Subir música ajena es lo que tumba
un canal, y ninguna fuente gratuita sin clave da una licencia verificable desde
aquí. Fuentes gratuitas con licencia clara para llenar la carpeta:
[pixabay.com/music](https://pixabay.com/music/),
[freemusicarchive.org](https://freemusicarchive.org/),
[incompetech.com](https://incompetech.com/).

## Proveedores

Se cambian en `.env` o con `--imagenes` / `--voz`. El pipeline no cambia.

| Tipo | Opción | Costo | Notas |
|---|---|---|---|
| Voz | `edge` (por defecto) | **gratis** | Voces neuronales de Microsoft, 45 en español. Da tiempos exactos por palabra. |
| Voz | `openai` | ~$0.015/min | Mejor calidad. Los tiempos se estiman, no son exactos. |
| Imagen | `pollinations` (por defecto) | **casi gratis** | IA a medida, 9:16. Con token elige modelo (ver abajo). Sin token, el endpoint anónimo ignora el modelo, devuelve 576x1024 y quema una marca de agua. |
| Imagen | `pexels` | **gratis** | Fotos de stock reales. Key sin tarjeta, 200 búsquedas/hora. **Solo por CLI**: ver el aviso de abajo. |
| Imagen | `placeholder` | **gratis** | Degradados con Pillow. Sin clave ni internet. Para probar el montaje. |
| Imagen | `gemini` | ~$0.039/img | Gemini Image (Nano Banana). IA a medida. Requiere facturación. |
| Imagen | `openai` | ~$0.015/img | gpt-image-1-mini. Genera 2:3, se recorta. |

Hay tres sabores gratis: **IA a medida** (Pollinations dibuja lo que pides),
**foto real** (Pexels busca la más cercana) y **degradado** (para probar sin
internet). Los de pago (Gemini, OpenAI) dan más control y estabilidad: un short
de 45s con 8 escenas sale **gratis** con Pollinations y por **~$0.31** con
Gemini. ClipShort cobra el equivalente a ~$1.13 por video en su plan básico.

> **Importante:** la API de Gemini **no tiene tier gratis para imágenes**. Para
> usar `gemini` u `openai` hay que **habilitar facturación** en el proyecto de la
> clave (con solo la clave, la API responde 429). Además, Google retiró Imagen 4
> Fast para cuentas nuevas: el modelo vigente es Gemini Image / Nano Banana. Y
> **Pollinations sin token da solo `sana` (básico).** Para la ilustración buena
> (`gptimage`, `flux`) hay que usar un token, que va contra el gateway nuevo
> `gen.pollinations.ai` con crédito "pollen":
>
> 1. Regístrate en [enter.pollinations.ai](https://enter.pollinations.ai) y crea
>    un **API key** (`sk_…`). Ponla en `.env` como `POLLINATIONS_TOKEN`.
> 2. **Reclama pollen gratis** en la sección de *quests/rewards* (crear la key ya
>    da 0.25 pollen; hay más). El saldo debe quedar > 0.
> 3. Listo: el proveedor detecta el token y usa `POLLINATIONS_MODEL` (por defecto
>    `flux`).
>
> **Por qué `flux` y no `gptimage`:** `gptimage` es el modelo de OpenAI y su
> moderación rechaza al azar (HTTP 400) los prompts de terror (calaveras, huesos),
> que es justo el contenido de estos shorts. `flux` es permisivo y fiable. Si aun
> así usas `gptimage` y una escena da 400, el proveedor la reintenta con `flux`
> automáticamente. Coste: `flux` ~0.0018 pollen/imagen (0.25 pollen ≈ 138 imgs,
> ~17 shorts); `gptimage` ~0.000006 (casi nada) pero censura. Con los 0.25 pollen
> gratis empiezas de sobra; reclama más quests o recarga (baratísimo) cuando baje.
> Si el saldo llega a 0, la generación avisa claro (o quita el token para `sana`).
>
> **Importante:** la API de Gemini **no tiene tier gratis para imágenes**. Para
> usar `gemini` u `openai` hay que **habilitar facturación** en el proyecto de la
> clave (con solo la clave, la API responde 429). Además, Google retiró Imagen 4
> Fast para cuentas nuevas: el modelo vigente es Gemini Image / Nano Banana. Y
> las suscripciones de ChatGPT Plus / Gemini Advanced **no incluyen acceso a la
> API**: el saldo se carga aparte en cada plataforma. Por eso el camino gratis
> va por Pexels.

## Qué modelo de imagen usar

Con token, el modelo se elige con `--modelo` o desde el selector de la web.
`python -m crearvideos modelos` lista el catálogo con precios:

| Clave | Modelo | Pollen/img | Short de 7 |
|---|---|---|---|
| `flux` | FLUX.1 Schnell | 0.002 | 0.014 |
| `flux-klein` | **FLUX.2 Klein — por defecto** | 0.005 | **0.035** |
| `MarcosFRG/phoenix-1.0` | Leonardo Phoenix | 0.016 | 0.112 |
| `gpt-image-1.5` | GPT Image 1.5 | 0.031 | 0.217 |

El defecto era `flux` (FLUX.1 Schnell) y **ese era el problema**: schnell es un
destilado de 4 pasos, rapidísimo y barato, pero de imagen blanda y con poca
adherencia al prompt. Es lo que hacía que estos shorts se vieran poco
profesionales. `flux-klein` cuesta 2.5× más —o sea 0.035 pollen por short, nada—
y es un salto de generación. `gpt-image-1.5` es el que mejor obedece el prompt:
vale la pena cuando la precisión importa (época histórica, detalles concretos).

> **El camino sin token ya no sirve para publicar.** Medido: el endpoint
> anónimo ignora el parámetro de modelo (pedir `sana` o `flux` devuelve el mismo
> archivo byte a byte), entrega **576x1024** en vez de 1080x1920, e ignora
> `nologo=true` — la marca "pollinations.ai" viene quemada en la imagen. Sirve
> para probar el montaje; no para un video que vayas a subir. Para lo demás hace
> falta pollen: se reclama en *quests* o se recarga en
> [enter.pollinations.ai](https://enter.pollinations.ai), y es baratísimo.

> **Pexels no sirve para temas históricos ni narrativos.** Busca una foto de
> stock con las **tres primeras palabras** de la narración, y el director no
> corre con él. Un guion sobre prehistoria que empieza "Antes de los
> supermercados…" devuelve literalmente carritos de supermercado, y después un
> gato en Estambul y un chef flambeando. Por eso ya no está en el selector de la
> web; sigue disponible con `--imagenes pexels` para temas modernos y
> concretos ("un café", "una ciudad de noche"), que es donde sí brilla.

## Módulo Montaje: tus imágenes y videos + la voz de aquí

Cuando ya tienes los medios (Google Flow, Midjourney, stock, tu cámara) y lo que
falta es armarlos con la narración. Es la pestaña **4 · Montaje** en la web.

```powershell
# Ver qué archivo caería en cada escena, sin generar nada
python -m crearvideos montar guion.txt C:\medios\mi-historia --solo-ver

# Montar, en vertical (por defecto) u horizontal
python -m crearvideos montar guion.txt C:\medios\mi-historia
python -m crearvideos montar guion.txt C:\medios --formato horizontal --musica auto

# Máster limpio, para poner el texto en tu editor
python -m crearvideos montar guion.txt C:\medios --sin-subtitulos
```

**La narración manda sobre la duración.** Cada imagen o clip dura exactamente lo
que dura su escena: un clip de 3 s en una escena de 8 s se repite; uno de 20 s se
corta. Así no hay que recortar nada a mano para que cuadre.

- **Imágenes** (jpg, png, webp…): llevan movimiento de cámara Ken Burns.
- **Videos** (mp4, mov, webm…): van tal cual, recortados al frame exacto y **sin
  su audio original** — la banda sonora es la narración.
- Cualquier proporción se encaja al formato recortando el sobrante, sin franjas
  negras.

**Cómo empareja los archivos** ([medios.py](crearvideos/medios.py)): si los
nombres traen el número de escena (`escena_0007.mp4`, `07.png`) manda ese número;
si no, el orden natural de la carpeta (`imagen2` antes que `imagen10`). Si faltan
archivos se repite el anterior y se avisa. `--solo-ver` enseña la tabla antes de
gastar tiempo narrando.

**Formatos**: `vertical` (1080x1920, por defecto), `horizontal` (1920x1080) y
`cuadrado` (1080x1080). Los subtítulos se reescalan al alto real del video, así
que en 16:9 no salen gigantes ni flotando en el centro.

Deja además `montaje.json` con qué archivo quedó en cada escena y en qué segundo
—lo que se mira cuando algo se ve fuera de sitio— y `subtitulos.srt` por si los
quieres subir aparte a YouTube.

### Tu propia voz

Tres formas de que el video no suene a robot, de menos a más trabajo tuyo:

```powershell
# 1. Clonar tu voz: una muestra y narra TODO imitándola
python -m crearvideos montar guion.txt C:\medios --voz-clon C:\voz\muestra.wav

# 2. Tu narración ya grabada, un archivo con todo
python -m crearvideos montar guion.txt C:\medios --voz-propia C:\voz\narracion.mp3

# 3. Tu narración ya grabada, un audio por escena (lo más exacto)
python -m crearvideos montar guion.txt C:\medios --voz-propia C:\voz\escenas\
```

**Clonada** (`--voz-clon`): graba 15-30 segundos de voz limpia, sin música ni
ruido. Usa [Chatterbox](https://github.com/resemble-ai/chatterbox) de Resemble
AI, que corre en tu PC y es **licencia MIT — se puede monetizar** (ojo: XTTS de
Coqui suena parecido pero prohíbe el uso comercial). Como genera escena por
escena, la duración de cada escena se mide del audio real y la imagen nunca se
despega.

Puedes apuntar a un **archivo o a una carpeta** (se usa el audio más largo que
haya dentro). La muestra se prepara sola antes de clonar: se convierte a WAV
mono de 24 kHz, se normaliza el volumen y se recorta a 25 segundos saltando el
primer segundo, que en una grabación casera suele ser ruido. Una nota de voz de
WhatsApp de 8 minutos vale tal cual.

**Antes de lanzarlo, pruébalo.** En la web, bajo el campo de la voz:

| Botón | Qué hace | Cuánto tarda |
|---|---|---|
| Revisar la muestra | dice qué archivo usará y cuántos segundos | instantáneo |
| ▶ Oír mi grabación | reproduce tu audio, para confirmar que es el correcto | instantáneo |
| Probar cómo sonará | genera UNA frase con tu voz clonada | ~1 min (luego cacheada) |

**Velocidad medida** en una RTX A500 de 4 GB: unos **8 segundos de proceso por
cada segundo de audio**. Un guion de 4 escenas (43 s) tardó 6 min; uno de 10
minutos de narración puede irse a más de una hora. Por eso la voz clonada se
reutiliza: se genera una vez y volver a montar no la repite.

**Grabada** (`--voz-propia`): con un solo archivo, se reparte entre las escenas
según el largo del texto de cada una (error medido: ±0,2 s por escena, y la
última absorbe lo que sobre). Con una carpeta —un audio por escena, emparejados
como los medios— cuadra exacto.

En los tres casos los **tiempos por palabra** se reparten dentro de cada escena
según el largo de cada palabra: el subtítulo entra y sale en su sitio, pero el
resaltado no es la medición exacta que sí da edge-tts.

#### Instalar la voz clonada

Va en un entorno aparte porque arrastra PyTorch y ~3 GB de modelo, y el camino
normal debe seguir siendo ligero. Necesita **Python 3.12**: con 3.13+ varias
dependencias no tienen rueda compilada y piden Visual Studio.

```powershell
uv venv --python 3.12 .venv-voz
uv pip install --python .venv-voz\Scripts\python.exe torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124
uv pip install --python .venv-voz\Scripts\python.exe chatterbox-tts
```

La app lo llama sola ([clonvoz.py](crearvideos/clonvoz.py) →
[tools/clonar_voz.py](tools/clonar_voz.py)), carga el modelo una vez por video y
usa la GPU si la hay. La primera ejecución descarga el modelo.

### Solo unir clips que ya traen voz

Otro caso distinto: los segmentos ya están narrados y no hacen falta ni voz ni
subtítulos. Solo ponerlos en orden y pegarlos. En la web es el botón **Solo unir
clips** dentro de la pestaña **4 · Montaje**.

```powershell
# Ver qué segmentos hay y cuánto duran, sin tocar nada
python -m crearvideos ensamblar C:\videos\segmentos --solo-ver

# Unir con corte seco
python -m crearvideos ensamblar C:\videos\segmentos

# Con transición, y música de fondo baja
python -m crearvideos ensamblar C:\videos\segmentos --transicion fundido --musica auto
python -m crearvideos transiciones
```

Se ordenan por el número del nombre (`01_intro.mp4`) o alfabéticamente, igual
que los medios del montaje. Cada clip **conserva su audio**.

**Transiciones** (`--transicion`): fundido, negro, blanco, disolver, izquierda,
derecha, arriba, barrido, círculo, pixel. Cruzan también el audio, así que la
voz no se corta de golpe. Ojo: **cada cruce acorta el video**, porque los dos
clips se solapan — con 10 clips y medio segundo, el resultado dura 4,5 s menos
que la suma. La app lo calcula y te lo dice antes.

**Formato**: `original` (el del primer clip), `vertical`, `horizontal` o
`cuadrado`. Lo que venga en otra proporción se recorta para llenar el cuadro.

Dos cosas que se arreglan solas, y que son las que suelen romper este tipo de
unión:

- **Clips mudos**: se les pone una pista de silencio. Sin eso, el audio de los
  demás se descoloca al pegarlos.
- **Audio más corto que la imagen** dentro del mismo clip (pasa al exportar
  desde otras herramientas): se rellena con silencio hasta igualar. Si no, cada
  transición arrastra el desfase y la última frase del video se pierde.

### Reusar la voz ya generada

Si primero generaste el audio en la pestaña **3 · Audio** y luego montas **con el
mismo nombre de proyecto**, el montaje usa esa misma pista en vez de volver a
narrarla: el video suena exactamente como lo que ya escuchaste y aprobaste, y se
ahorra la síntesis entera. Medido en una prueba de 4 escenas: **21 s reusando
contra 177 s renarrando**.

Funciona porque junto a los WAV se guarda `narracion.json` con los tiempos de
cada palabra, que es lo que el audio por sí solo no contiene y los subtítulos
necesitan.

Se reusa solo si coincide **todo**: el texto de cada escena, la voz, el efecto,
el ritmo y el respiro entre escenas. Cambia cualquier cosa y se vuelve a narrar
sin que tengas que acordarte. Para forzarlo: `--renarrar`, o quitar la casilla
*Reusar la voz ya generada*.

## Módulo Voces: una voz por escena

Cuando no quieres que todo lo narre la misma voz: un diálogo entre dos
personajes, una sección con otro tono, un cierre con voz grave. Es la pestaña
**🎙 Voces**, y se usa **antes** de montar.

```powershell
# Ver el guion escena por escena con la voz que le toca a cada una
python -m crearvideos voces-escena guion.txt --nombre mi-video

# Poner otra voz a escenas concretas y generar lo que falte
python -m crearvideos voces-escena guion.txt -n mi-video --asignar "3,7=es-ES-ElviraNeural" --generar

# Rehacer solo una escena, y oír el conjunto
python -m crearvideos voces-escena guion.txt -n mi-video --rehacer 3 --unir
```

En la web sale una tabla con todas las escenas: el desplegable de voz, **▶** para
oírla y **↻** para rehacerla. Abajo, *Oír todo seguido* une la narración completa.

### Cada escena puede venir de tres sitios

En el desplegable de cada escena, además de las voces generadas:

| Origen | Qué es | Qué hace falta |
|---|---|---|
| Voz generada | edge-tts, gratis | nada |
| 🎤 **Mi grabación** | un MP3/WAV tuyo | la ruta de tu carpeta de audios, arriba |
| 🧬 **Voz clonada** | tu voz, clonada de una muestra | la ruta de la muestra, arriba |

Arriba de la tabla hay dos campos de ruta: **Tu narración ya grabada** (una
carpeta con un audio por escena, emparejados por el número del nombre como los
medios del montaje, o un archivo suelto) y **Clonar tu voz** (la muestra), con
un botón para *probar cómo sonará* antes de aplicarla.

Por terminal:

```powershell
# La escena 2 con un audio tuyo, la 5 clonada, el resto con voz generada
python -m crearvideos voces-escena guion.txt -n mi-video ^
  --grabada 2 --audios C:\mis-audios ^
  --clonar 5 --muestra C:\voz\muestra.mp3 --generar

# Todo el video con tu voz clonada
python -m crearvideos voces-escena guion.txt -n mi-video --clonar todas --muestra C:\voz\muestra.mp3 --generar
```

Las escenas **clonadas se generan todas juntas en una sola pasada**: cargar el
modelo tarda más que generar una escena corta. Y si luego cambias la voz de otra
escena, la clonación **no se repite** — probado: cambiar una escena generada
tardó 2,2 s y la clonada quedó intacta.

**Lo que hace que se pueda usar de verdad**: cada escena se guarda con su propia
huella (texto + voz + efecto), así que **cambiar la voz de una escena regenera
solo esa**. Medido en una prueba de 3 escenas: generar todas, 18 s; cambiar una,
1,2 s. Las demás no se vuelven a sintetizar, así que tampoco cambian de sonido.

Cuando montes el video **con el mismo nombre de proyecto**, el montaje encuentra
esas voces y las usa tal cual: suena exactamente como lo aprobaste.

## Módulo Audio: solo la narración

Cuando el video lo vas a montar en otro lado (Premiere, CapCut, Resolve, Google
Flow) y de aquí solo quieres la voz. Es la pestaña **3 · Audio** en la web.

```powershell
python -m crearvideos audio guiones\mi-historia.txt
python -m crearvideos audio guion.txt --voz es-MX-DaliaNeural --velocidad 1.1
```

Genera la misma voz del pipeline completo, con los tiempos medidos del audio
real, y se detiene antes de ilustrar y montar. Deja en `proyectos/<nombre>/`:

| Archivo | Para qué |
|---|---|
| `narracion.mp3` | la pista completa, para el editor |
| `narracion.wav` | la misma sin comprimir |
| `audio/escena_NNN.wav` | cada escena por separado, ya alineada |
| `escenas.srt` | una entrada por escena: cuándo entra cada imagen |
| `subtitulos.srt` | líneas cortas, para quemar subtítulos |
| `escenas.json` | tiempos + narración + texto en pantalla + prompt de cada escena |
| `narracion.txt` | el texto plano narrado |

`--velocidad` cambia el ritmo sin tocar el tono, y **escala los SRT** con él,
así que las marcas siguen cuadrando con la pista exportada.

Repetir el mismo comando no vuelve a narrar: aprovecha lo ya generado y tarda un
segundo (`--renarrar` lo fuerza). Y esa misma voz la reutiliza después el módulo
de montaje.

### Importar guiones de StoryFlow Pro

Un export de StoryFlow Pro se pega **tal cual**, sin editarlo: se detecta solo y
se traduce al formato propio ([importar.py](crearvideos/importar.py)). Los
bloques `ESCENA #0001 [TIPO]` con `NARRACIÓN`, `TEXTO PANTALLA` y `PROMPT` se
convierten en escenas, y el `PROMPT` pasa a ser el `[imagen: ...]` de cada una.
Vale en los tres módulos: audio, producción y `escenas`.

## Módulo Publicar: SEO y CTR para YouTube

El video terminado no se sube solo: hacen falta título, descripción, capítulos,
etiquetas y miniatura. Es la pestaña **5 · Publicar**.

```powershell
python -m crearvideos publicar mi-proyecto
python -m crearvideos publicar mi-proyecto --contexto "canal de salud basada en evidencia"
```

Genera, listo para pegar:

- **5 títulos** distintos para probar como A/B, con su tipo (curiosidad, dato,
  pregunta, lista, negación), el porqué de cada uno y el conteo de caracteres —
  avisa si alguno pasa de 100 y YouTube lo cortaría.
- **Descripción completa**, con el gancho, los capítulos dentro, el aviso legal
  si el tema es de salud/dinero/derecho, las llamadas a la acción y los hashtags.
- **Capítulos con el minuto real** (ver abajo).
- **Etiquetas**, recortadas al límite de 500 caracteres de YouTube.
- **Comentario fijado** y **texto e idea visual para la miniatura**, que es lo
  que de verdad mueve el CTR.

### Los capítulos se calculan, no se inventan

Es la diferencia con pedírselo a un chat. El proyecto ya tiene el segundo exacto
en que empieza cada escena, porque lo fijó la narración al generarse. La IA solo
decide **en qué escena** cortar cada capítulo y cómo llamarlo; el minuto lo pone
Python. Después se aplican las reglas de YouTube: mínimo 3 capítulos, el primero
en `00:00`, y 10 segundos entre uno y otro. Si no se cumplen, no se ponen
capítulos y se dice por qué, en vez de publicar unos que no cuadran.

Verificado en un video de 8:49 y 47 escenas: los 9 capítulos caían exactamente
en la escena donde empieza cada tema.

Necesita que el proyecto tenga audio generado (pestaña **3 · Audio**) o video
montado (pestaña **4 · Montaje**): de ahí salen los tiempos.

**Los videos viejos también valen.** Los hechos antes de que se guardaran los
tiempos no tienen `escenas.json`, pero conservan `audio/escena_NNN.wav` —que da
la duración exacta de cada escena— y `subtitulos.ass`, con lo que se dice y en
qué segundo. De ahí se reconstruye la línea de tiempo sin estimar nada, y esos
proyectos aparecen en la lista con su fecha.

**Contenido de terror, crimen o salud:** el modelo principal va por Azure, cuyo
filtro rechaza esos guiones —justo los nichos que mejor funcionan sin cámara—.
Cuando pasa, se reintenta solo con un modelo permisivo y el informe anota cuál
respondió de verdad. Todo queda en
`publicacion.md` y `publicacion.json` dentro del proyecto.

### Subir el video a YouTube

Con la publicación ya generada, el botón **Subir a YouTube** (o el comando
`subir`) manda el MP4 con ese título, esa descripción y esas etiquetas.

```powershell
python -m crearvideos youtube-conectar          # una sola vez
python -m crearvideos subir mi-proyecto         # privado, pregunta antes
python -m crearvideos subir mi-proyecto -p oculto
python -m crearvideos subir mi-proyecto --programar "2026-10-02 18:30"
```

**Se sube en privado por defecto** y siempre pregunta antes: revisas el
resultado en YouTube Studio, le pones la miniatura y publicas tú. Con
`--programar` queda privado hasta la fecha que digas y se publica solo.

#### Conectar el canal (una vez)

Esto lo tienes que hacer tú en tu cuenta; el programa no puede:

1. Crea un proyecto en <https://console.cloud.google.com/>.
2. Activa la **YouTube Data API v3**.
3. Pantalla de consentimiento OAuth: tipo **Externo**, y añádete como **usuario
   de prueba** con la cuenta del canal.
4. Credenciales → Crear → **ID de cliente de OAuth** → **Aplicación de
   escritorio**. Descarga el JSON y déjalo en la raíz como `client_secret.json`.
5. `python -m crearvideos youtube-conectar` abre el navegador una vez y guarda
   el permiso en `.youtube-token.json`.

Ambos archivos están en `.gitignore`: son tus llaves, no se comparten.
Para revocar: `python -m crearvideos youtube-conectar --desconectar`.

#### Dos límites que no dependen del código

- Subir cuesta **1600 unidades** de las 10.000 diarias: unos **6 videos al día**.
- Mientras la app esté **sin verificar** por Google, la API solo permite subir
  en **privado**, aunque pidas público. Se publica después desde YouTube Studio.

El permiso que se pide es solo `youtube.upload`: no da acceso a leer, borrar ni
modificar nada más de tu canal.

## Buscador de nichos: qué hacer, y el guion para hacerlo

Es la pestaña **1 · Estudio → Buscar nichos**. Escribes un tema como lo
buscarías en YouTube y devuelve una cuadrícula de videos que están
funcionando, con su miniatura y lo que se pudo medir de cada uno.

```powershell
python -m crearvideos nichos "misterios historicos sin resolver"
python -m crearvideos nichos "finanzas personales" --orden dinero --tipo largo
python -m crearvideos nichos "recetas rapidas" --orden despegue --faceless
```

**No necesita la API de YouTube**: busca con `yt-dlp` como lo haría cualquiera.

### ¿No sabes qué nicho elegir?

El botón **Ver los nichos más populares** (o `python -m crearvideos catalogo`)
lista **26 nichos que se pueden hacer sin dar la cara**, y no como lista
copiada de un blog: cada uno se **mide en YouTube en ese momento**.

```
Nicho                    RPM      Vistas del típico   USD por video
Curiosidades y top 10    $2–6            6.6M          $13.3K–39.8K
Inversión y bolsa        $12–30          1.0M          $12.3K–30.8K
Historia y documental    $3–9            2.2M           $6.5K–19.6K
Finanzas personales      $12–30          344K           $4.1K–10.3K
Autos y motor            $1.5–5          26K              $39–131
```

Cada fila trae además **qué tener en cuenta** antes de meterse: *"Cocina:
necesita grabar comida, es el menos faceless de la lista"*, *"IA: tema caliente
pero caduca rápido"*, *"Crimen real: retención altísima, ojo con datos de
personas reales"*. Y un botón **Explorar** que lanza la búsqueda de ese nicho.

Tarda unos 15 segundos en medir los 26, en paralelo.

**Cómo leer «vistas del típico»**: es la mediana de los primeros resultados de
ese nicho, así que mide lo que consigue **quien ya está arriba**, no lo que
sacarás tú la primera semana. Sirve para comparar nichos entre sí — demanda
frente a lo que paga — no para prometer ingresos.

Las tres señales que trae cada tarjeta, y por qué importan:

| Señal | Qué dice |
|---|---|
| **Vistas/día** | qué corre **ahora**; un viral de hace tres años no sirve de referencia |
| **×subs** | vistas ÷ suscriptores. Un 200× significa que lo empujó el algoritmo, no su audiencia: **ahí está la fórmula que funciona sola** |
| **$ estimado** | vistas × RPM del nicho. Las vistas no valen lo mismo en finanzas que en entretenimiento |

Más la etiqueta **SIN CÁMARA** (si se puede hacer sin dar la cara), el nicho
concreto y una frase de por qué ese video funciona. Todo eso lo clasifica la IA
en **una sola llamada** para la tanda entera.

Ordena por: `virales` (vistas/día), `despegue` (×subs), `dinero`, `vistas` o
`audiencia`. Cada búsqueda se guarda en `estudio/nichos/`.

### De la tarjeta al guion

Cada tarjeta tiene tres botones: **Ver** (abre el video), **Analizar** (manda el
canal al análisis a fondo) y **Guion**.

*Guion* abre el escritor con esa **fórmula como referencia**: escribes tu tema y
tus minutos, y sale un guion en el formato de la app — cabecera de estilo,
escenas separadas por `---` y su `[imagen: ...]` — listo para mandarlo a **Voces**,
**Audio**, **Montaje** o **Producción** con un botón, sin copiar y pegar.

```powershell
python -m crearvideos guion "el enigma de las lineas de Nazca" --minutos 3
python -m crearvideos guion "tu tema" --como <id del video de referencia>
```

Se imita la **estructura** (cómo engancha, cómo encadena, dónde va el giro),
nunca el texto ni el tema: un guion calcado se nota y YouTube desmonetiza el
contenido reutilizado.

Medido: 12 s para un guion de 2 minutos (12 escenas, 344 palabras), y la
duración estimada cuadró en 2:01.

## Del análisis al guion completo

Dentro del informe de cualquier canal o video analizado está el botón
**✍ Escribir un guion con esta fórmula**. No pasa solo el título: le da al
guionista **el ADN entero** que costó averiguar.

Lo que viaja del análisis al guion:

| Del análisis | Para qué se usa |
|---|---|
| Gancho y su técnica | cómo abre el guion |
| Estructura, paso a paso | el esqueleto, en el mismo orden |
| Técnicas de retención | dónde se ponen los reenganches |
| Fórmula del título | cómo se titula |
| Tono y a quién le habla | cómo suena la narración |
| Promesa del canal | qué se le promete al espectador |
| Estilo visual y paleta | la cabecera del guion, tal cual |

Y arriba salen **las ideas que el propio análisis ya propuso** como botones:
pulsas una y se convierte en el tema del guion. Salieron de mirar lo que
funciona en ese canal, así que son el mejor punto de partida.

Probado con un canal de salud: el guion abrió con una anécdota personal —que es
justo el gancho característico de ese canal—, tituló con su plantilla
`[Problema] + [solución] + [resultado]`, y heredó su cabecera visual. 13
segundos, 15 escenas, 2:22 para un objetivo de 2,5 minutos.

Se copia la **estructura**, nunca el texto ni el tema.

## La estética del canal, en imágenes

Del análisis de un canal que funciona salen su paleta, su luz y su tipo de
encuadre. Con eso, el botón **🎨 Crear la estética de mi canal** (dentro del
informe) construye una identidad **propia**, no una copia:

```powershell
python -m crearvideos estetica <canal-analizado>
python -m crearvideos estetica <canal> --extra "protagonista mujer joven, estilo ilustración"
python -m crearvideos estetica <canal> --piezas avatar,miniatura
```

Genera cinco piezas en `estudio/canales/<canal>/estetica/`:

| Pieza | Tamaño | Para qué |
|---|---|---|
| `personaje.jpg` | 1080×1920 | el narrador recurrente del canal |
| `escena.jpg` | 1080×1920 | cómo se ven los videos por dentro |
| `miniatura.jpg` | 1280×720 | la composición de miniatura del nicho |
| `avatar.jpg` | 1024×1024 | el icono del canal |
| `portada.jpg` | 1920×1080 | la cabecera |

Más el **concepto visual** en dos frases y la **paleta en códigos de color**,
para usarla en tus miniaturas.

### El personaje es lo que hace que parezca un canal

La descripción física del protagonista se guarda en `personaje.txt`:

```
protagonistas: Dra. Vera — Mujer de 42 años, complexión media atlética, piel
morena clara, cabello castaño oscuro liso a la altura de los hombros, un lunar
pequeño bajo el ojo izquierdo, blusa de lino blanco crudo y chaqueta azul petróleo.
```

Esa línea se pega al principio de cualquier guion y el director de imágenes
mantiene al mismo protagonista en todas las escenas de todos tus videos.

Dos cosas que se arreglan en código y no se dejan a la IA, porque fallan
justo cuando importan:

- **El personaje se repite literal** en las cuatro piezas donde sale. Sin eso,
  cada imagen inventaba una persona distinta y el tablero no servía de nada.
- **El avatar se reescribe** si la IA describió a una persona: una cara no se
  lee a 48 píxeles, que es el tamaño real de un icono de canal.

Necesita `POLLINATIONS_TOKEN`: sin token las imágenes salen pequeñas y con
marca de agua. Tarda alrededor de un minuto las cinco.

## Módulo Estudio: analizar videos y canales que funcionan

La app tiene dos módulos. **Producción** (todo lo anterior) convierte un guion en
video. **Estudio** decide qué video vale la pena hacer: analiza un video o canal
de YouTube y explica por qué funciona. En la web es la pestaña **1 · Estudio**.

```powershell
# Un video: datos, comparación con su canal y análisis con IA
python -m crearvideos analizar "https://www.youtube.com/watch?v=DlgL67zmAKw"

# Un canal: sus outliers y su ADN (lee 30 largos + 30 shorts, 3 outliers a fondo)
python -m crearvideos analizar @nombredelcanal --videos 30 --profundidad 3

# Solo datos medidos y transcripción, sin IA
python -m crearvideos analizar @nombredelcanal --sin-ia
```

**No necesita YouTube Data API.** Lee la página pública con `yt-dlp`: vistas,
likes, suscriptores, duración, tags, descripción, capítulos y subtítulos
(manuales o automáticos).

Qué se **mide** (datos, no opiniones):

- **Múltiplo**: vistas del video ÷ mediana de los últimos 30 del canal, separando
  shorts de largos. Un 16× es un outlier: algo de ese video funcionó mucho más de
  lo normal para ese canal, y eso es lo que vale la pena estudiar.
- Vistas por día, engagement y palabras por minuto de la narración.

Qué **interpreta la IA** (Pollinations `gpt-5.4-mini`, ~0.015 pollen por análisis;
lee la transcripción entera y mira la miniatura): gancho con cita y minuto,
estructura, técnicas de retención, fórmula del título, miniatura, tono, si es
faceless, por qué funcionó, cómo replicarlo sin copiar, ideas de videos y el
estilo visual del nicho.

Los **ingresos** son una estimación por el RPM típico del nicho
([metricas.py](crearvideos/estudio/metricas.py)): YouTube no publica el RPM de
nadie. Sirven para comparar nichos, no para prometer dinero.

Todo queda en `estudio/videos/<id>/` o `estudio/canales/<handle>/`: `informe.md`,
`informe.json`, transcripción y miniatura. El informe trae al final una
**cabecera para el guion** (`estilo/paleta/evitar`), y en la web el botón
*Usar su estilo en Producción* la pega encima de tu guion.

Desde Claude Code, la skill `analizar-youtube` ejecuta lo mismo, revisa el informe
contra la transcripción y puede escribir el guion en el formato de producción.

## Ajustes

Casi todo vive en [config.py](crearvideos/config.py):

- `SubtitleStyle` — fuente, tamaño, colores, cuántas palabras se ven a la vez,
  altura del subtítulo.
- `SceneSettings` — cuánto texto entra en cada escena, y por tanto cada
  cuánto cambia la imagen.
- `VoiceSettings.rate` — velocidad de la narración (por defecto `+8%`).
- `voice_fx` — efecto de voz por defecto (`auto`). Los presets viven en
  [voicefx.py](crearvideos/voicefx.py); ahí se ajustan tonos y reverbs o se
  añaden nuevos. También con `VOICE_FX` en `.env`.
- `MusicSettings` — volumen de la música cuando la voz calla (`volume`, por
  defecto `0.30`, también con `MUSIC_VOLUME` en `.env`) y el ducking: cuánto
  baja (`duck_ratio`), a partir de qué nivel de voz (`duck_threshold`) y con
  qué rapidez entra y sale (`duck_attack`, `duck_release`).
- `MOVES` en [compose.py](crearvideos/compose.py) — los recorridos de cámara.

## Estructura

```
crearvideos/
  pipeline.py     orquesta todo
  script.py       guion -> escenas
  subtitles.py    tiempos -> .ass con resaltado
  compose.py      escenas -> mp4 (ffmpeg)
  ffmpeg.py       envoltura de ffmpeg/ffprobe
  voicefx.py      efectos de voz (grave, cripta, fantasma…)
  music.py        biblioteca de música de fondo
  montaje.py      tus medios + voz + subtítulos -> mp4
  medios.py       empareja una carpeta de archivos con las escenas
  narracion.py    solo audio: voz + SRT, sin montaje
  importar.py     traduce exports de otras herramientas (StoryFlow Pro)
  config.py       todos los ajustes
  providers/
    tts/          edge, openai
    images/       pollinations, pexels, placeholder, gemini, openai
  web/            servidor local + página (stdlib, sin dependencias)
  estudio/        módulo de análisis de YouTube
    youtube.py    lectura con yt-dlp (video, canal, transcripción)
    metricas.py   outliers, RPM por nicho, engagement
    ia.py         llamada a la IA de texto con visión
    analizador.py orquesta y escribe el informe
```

Cada proyecto deja todo en `proyectos/<nombre>/`: las imágenes, el audio por
escena, los subtítulos y el MP4. Los intermedios se conservan a propósito —
si algo sale raro, ahí se ve en qué paso pasó.

## Notas de implementación

Dos decisiones que no son obvias y que sostienen la sincronía:

1. **Todas las duraciones se redondean al frame exacto** antes de generar
   nada (`_quantize` en `pipeline.py`). Un clip de video solo puede durar un
   número entero de frames; si el audio no cae en la misma rejilla, cada
   escena se desfasa hasta 33 ms y el error se acumula.

2. **La narración se une en PCM, no en MP3.** Concatenar MP3 arrastra el
   relleno de cada frame (26 ms) y la voz termina despegándose de la imagen.

El resultado medido: video 41.3667s / audio 41.3667s en un video de 5 escenas.
Cero deriva.

## Pendiente

- La interfaz web no expone todavía el ajuste fino de escenas; para eso, de
  momento, la línea de comandos.
- Biblioteca de música descargable desde la app (Pixabay tiene API gratuita con
  clave; haría falta enseñar la licencia de cada pista antes de usarla).
- Publicación automática a TikTok / YouTube.
- Generación del guion con IA a partir de un tema. Ojo: no hay forma gratis de
  automatizarlo — requiere saldo de API. El camino gratis sigue siendo escribir
  el guion (o pedírselo a ChatGPT/Claude en su web) y pegarlo en un `.txt`.
