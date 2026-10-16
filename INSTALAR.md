# CrearVideos — cómo usarlo

No hay instalación. Es un solo archivo.

## Empezar

1. Descarga **CrearVideos.exe**.
2. Doble clic. Se abre una ventana negra y, detrás, tu navegador con la app.
3. Si Windows avisa de que "protegió tu PC": *Más información* → *Ejecutar de
   todas formas*. Sale porque el programa no está firmado digitalmente, no
   porque tenga nada raro.

Para cerrarlo, cierra la ventana negra.

## Dónde quedan tus cosas

Todo se guarda en **Documentos\CrearVideos**:

```
proyectos\     los videos y audios que generes
guiones\       tus guiones en .txt
estudio\       los análisis de canales de YouTube
assets\musica\ deja aquí tus pistas de fondo
.env           tus claves (opcional)
```

Nada sale de tu computadora, salvo lo que la propia app necesita pedir por
internet: las voces, las imágenes por IA y los análisis de YouTube.

## Lo que funciona sin configurar nada

- Convertir un guion en video con voz y subtítulos.
- Generar solo el audio de un guion.
- Unir tus propios videos e imágenes.
- Unir segmentos que ya traen voz, con transiciones.
- Analizar videos y canales de YouTube.
- Preparar el título, la descripción y los capítulos para YouTube.

## Opcionales

**Mejores imágenes** — un token gratuito de <https://enter.pollinations.ai>.
Se pega en `Documentos\CrearVideos\.env`, en la línea `POLLINATIONS_TOKEN=`.
Sin él las imágenes salen más básicas y con marca de agua.

**Clonar tu voz** — no viene dentro del programa porque son unos **5 GB**. Se
instala desde la pestaña **🎙 Voces**, con el botón **Instalar voz clonada**:
descarga todo solo, sin abrir ninguna terminal. Necesita internet y un rato.

Sin esto, todo lo demás funciona igual: solo se desactiva la voz clonada.

Cuánto tarda en generar, una vez instalada: unos **8 segundos por cada segundo
de narración** en una tarjeta gráfica NVIDIA modesta. Un video de 18 minutos
son unas 2,5 horas. Sin tarjeta gráfica, bastante más. El programa te dice la
estimación antes de empezar.

Consejo: prueba primero el botón **Probar cómo sonará**, que genera una sola
frase en un minuto, antes de lanzar el guion completo.

**Subir a YouTube** — hay que crear unas credenciales en Google Cloud. Está
explicado en el README del proyecto.

## Si algo falla

### "No se puede acceder a este sitio web" / "No se pudo contactar al servidor"

Significa que **el programa ya no está corriendo**. La página no vive en
internet: la sirve CrearVideos desde tu propia computadora, así que si el
programa se cierra, la página deja de responder.

Por orden de probabilidad:

1. **Se cerró la ventana negra.** Esa ventana *es* el programa; no es un
   mensaje que se pueda cerrar. Vuelve a abrir `CrearVideos.exe`.
2. **La dirección cambió.** Si el puerto 8090 estaba ocupado por otro
   programa, CrearVideos usa el 8091, el 8092… La dirección buena sale escrita
   en la ventana negra al arrancar, y queda guardada en
   `Documentos\CrearVideos\Abrir CrearVideos.url`. Un marcador viejo del
   navegador apuntará al puerto equivocado.
3. **El antivirus lo cerró.** Al no estar firmado, algunos antivirus lo matan
   en silencio. Búscalo en la cuarentena de tu antivirus y márcalo como
   permitido.
4. **Se quedó sin espacio en disco.** El programa se descomprime en la carpeta
   temporal cada vez que arranca y los videos ocupan bastante.

### Qué mandar para que te ayuden

El archivo **`Documentos\CrearVideos\registro.log`**. Ahí queda todo lo que el
programa iba haciendo, incluidos el diagnóstico del arranque (versión de
Windows, espacio libre, dónde quedó ffmpeg) y el motivo si se cerró solo.

Sin ese archivo no hay forma de saber qué pasó, porque el fallo ocurre en tu
computadora y no en la de quien hizo el programa.
