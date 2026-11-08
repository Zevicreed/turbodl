# TurboDL — tu descargador estilo IDM (v1.3.5, con extensión)

Descargador de escritorio para Windows hecho en Python:

- **Enlaces directos** (archivos, .mp4, .zip, lo que sea): motor propio **segmentado**
  como el de IDM — parte la descarga en trozos (1–16, tú eliges), los baja en paralelo,
  muestra velocidad y ETA, y **pausa/reanuda de verdad**: el avance queda guardado en
  disco, puedes cerrar el programa y al reabrirlo la descarga aparece como
  «Pausada (reanusable)».
- **Páginas de video** (YouTube, TikTok y los sitios que soporta yt-dlp): detecta el
  video, te deja elegir calidad (2160p → 360p) o **solo audio**, y descarga.
  Para unir video+audio en la mejor calidad conviene tener **ffmpeg** instalado
  (sin ffmpeg igual descarga, en el formato combinado que ofrezca el sitio).

## La extensión (el modo IDM completo)

Igual que IDM: la **extensión del navegador caza el video** y el **programa lo
descarga**. Sin la extensión, TurboDL funciona pegando enlaces; con ella,
aparece el botón de descargar dentro de las páginas.

**Instalar la extensión (Chrome o Edge, una sola vez):**

1. Abre `chrome://extensions` (en Edge: `edge://extensions`).
2. Activa el **Modo desarrollador** (arriba a la derecha).
3. Clic en **Cargar extensión sin empaquetar** y elige la carpeta `extension`
   que viene junto al programa.
4. Fija el icono de TurboDL en la barra (clic en la pieza de rompecabezas).

**Cómo se usa:**

1. Deja **TurboDL abierto** (la extensión le pasa los trabajos por el puerto
   local 8765; el icono de la extensión te dice si está conectado).
2. Entra a la película o video y dale **play** unos segundos.
3. Aparece un botón flotante **«Descargar con TurboDL»** en la página, o abre
   el icono de la extensión: ahí están todos los streams cazados (mp4, m3u8…)
   con su tamaño y el botón de descargar.
4. Para páginas tipo YouTube usa **«Enviar esta página a TurboDL»**: yt-dlp
   la analiza y descarga en la mejor calidad.

**Notas honestas:**

- Los sitios de películas suelen servir el video como **HLS (.m3u8)**: TurboDL
  lo baja completo y lo deja como un solo archivo de video.
- Los fragmentos del stream se descargan **en paralelo** (tantos como diga el
  campo «Segmentos»): medido, ~6 veces más rápido que de uno en uno.
- Para que el resultado sea un **MP4 estándar** que abra cualquier reproductor
  (VLC incluido), TurboDL usa **ffmpeg**: si hay un `ffmpeg.exe` junto a
  `app.py` lo usa automáticamente. Sin ffmpeg, el HLS queda como volcado crudo
  del stream: algunos reproductores lo abren y otros (VLC) lo rechazan.
- Al terminar, cada video se **verifica con ffprobe**: si el servidor entregó
  un stream defectuoso, la descarga se marca como error en vez de
  «Completada». Y si la descarga termina **sin crear archivo alguno** (pasa
  con streams protegidos o caducados), también es error, nunca «Completada».
- Los streams que manda la extensión viajan con el **Referer de la página**:
  muchos CDN de streaming lo exigen y sin él no entregan nada.
- Cada stream se guarda **con el título de su episodio** (y nombre único si
  ya existe uno igual): los streams de un mismo sitio se llaman todos igual
  (`index-f3-v1-a1`) y yt-dlp saltaba cada episodio tras el primero creyendo
  que «ya se descargó» — la fila decía «Completada» sin bajar nada. Al
  completar, la fila muestra el **tamaño real** del archivo.
- **Instalación como programa**: doble clic en `Instalar TurboDL.bat` y
  quedan un icono en el Escritorio (abre sin consola) y el arranque
  automático con Windows, minimizado y ya escuchando a la extensión.
  `Quitar TurboDL del inicio.bat` lo deshace. La carpeta de destino que
  elijas se recuerda entre aperturas.
- **Actualización automática**: al abrir, la app consulta su repo
  (github.com/Zevicreed/turbodl, archivo `version.json`); si hay una
  versión más nueva, aparece un botón «Actualizar» — y solo entonces.
  Un clic descarga los archivos nuevos (nunca el entorno, el ffmpeg ni
  tu configuración), los aplica y reinicia la app.
- **Progreso visible**: la barra principal sigue sola a la descarga
  activa (sin necesidad de seleccionarla) y cada fila lleva su propia
  barrita junto al porcentaje.
- **YouTube** pelea con su control anti-bots. La ruta que da la escalera
  completa de calidades (verificado en la PC de Elis) es el cliente web con su
  verificación JavaScript resuelta por **Node** (si Node está instalado,
  TurboDL lo usa solo). Ojo: mezclar el cliente «android» envenena la sesión
  (experimento SABR de YouTube) y todo cae a 360p, así que android queda solo
  como reintento de emergencia. Y si aun así YouTube se pone duro, tienes la
  casilla **«YouTube: usar mi sesión de Chrome»** (hay que cerrar Chrome para
  que se puedan leer sus cookies).
- Si un sitio protege su stream con DRM (Netflix, Disney+…), ningún
  descargador puede con él; eso no es un fallo de TurboDL.
- La extensión solo habla con tu propio PC (127.0.0.1); nada sale a internet.

## Cómo ejecutarlo en tu PC

1. Instala Python 3.11+ desde python.org (marca «Add python.exe to PATH»).
2. En esta carpeta, abre una terminal y corre una sola vez:

   ```
   pip install -r requirements.txt
   ```

3. Doble clic en **Correr.bat** — o desde la terminal: `python app.py`.

## Archivos

- `app.py` — la ventana (lista de descargas, progreso, botones).
- `engine.py` — el motor segmentado (la parte «IDM»).
- `video.py` — la detección/descarga de videos con yt-dlp.
- `server.py` — el puente local donde la extensión entrega los trabajos.
- `extension/` — la extensión de Chrome/Edge (el cazador de streams).
- Las descargas pausadas dejan `nombre.part0…` y `nombre.turbodl.json` junto al
  archivo destino: no los borres si quieres poder reanudar.

## Ideas para la v2

- Pegar varios enlaces de una vez / leerlos de un .txt.
- Empaquetarlo como .exe con PyInstaller (doble clic y listo, sin Python visible).
- Límite de velocidad por descarga y descargas programadas.
