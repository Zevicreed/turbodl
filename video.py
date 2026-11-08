"""
Detección y descarga de videos desde páginas (YouTube, TikTok, etc.)
usando yt-dlp si está instalado. Si no está, la app lo dice claramente
y el resto (enlaces directos) sigue funcionando con el motor propio.
"""
import os


def yt_dlp_available():
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


class _QuietLogger:
    def debug(self, *a):
        pass

    def warning(self, *a):
        pass

    def error(self, *a):
        pass


# --- YouTube: rutas contra su control anti-bots -----------------------------
# Verificado en la PC de Elis (2026-11-07, dos matrices): la ruta de máxima
# calidad es el cliente WEB por defecto con el runtime JS en Node (Node v24
# está en su PATH y yt-dlp-ejs en el venv): escalera completa 144→1080p.
# El cliente «android» pasa el muro pero ENVENENA la sesión (SABR): con que
# esté en la lista, todo cae a 360p. Por eso android queda solo como reintento
# de emergencia (formato combinado «best»), nunca mezclado con el web.
_YT = {"cookies": False}


def set_youtube_cookies(enabled):
    """Si True, yt-dlp usa la sesión de YouTube abierta en el Chrome del usuario."""
    _YT["cookies"] = bool(enabled)


def _is_youtube(url):
    return "youtube.com" in url or "youtu.be" in url


def _apply_youtube_opts(opts, url, mode="default"):
    if _is_youtube(url):
        opts["js_runtimes"] = {"node": {}, "deno": {}}
        if _YT["cookies"]:
            opts["cookiesfrombrowser"] = ("chrome",)
        elif mode == "android":
            opts["extractor_args"] = {"youtube": {"player_client": ["android"]}}
    return opts


def _yt_modes(url):
    """Orden de intentos para YouTube: web+Node primero; android de emergencia."""
    if not _is_youtube(url) or _YT["cookies"]:
        return ("default",)
    return ("default", "android")


def _clean_error(e):
    """Errores legibles: sin códigos de color ANSI ni saltos de línea."""
    import re
    t = re.sub(r"\x1b\[[0-9;]*m", "", str(e))
    return " ".join(t.split())[:300]


def _friendly_error(e):
    t = _clean_error(e)
    if "Could not copy Chrome cookie database" in t:
        return ("No pude leer las cookies de Chrome porque está abierto. "
                "Ciérralo por completo y reintenta, o desactiva la casilla de la sesión.")
    if "not a bot" in t or "Sign in to confirm" in t:
        return ("YouTube bloqueó la descarga con su control anti-bots. "
                "Activa «YouTube: usar mi sesión de Chrome» en TurboDL y reintenta.")
    if "PO Token" in t or "po_token" in t:
        return ("YouTube exige un token de verificación para este video. "
                "Activa «YouTube: usar mi sesión de Chrome» en TurboDL y reintenta.")
    return t


def extract_info(url):
    """Devuelve dict con title, duration y opciones de calidad, o None si
    la URL no es una página de video reconocible."""
    if not yt_dlp_available():
        return None
    import yt_dlp
    info = None
    for mode in _yt_modes(url):
        opts = {"quiet": True, "no_warnings": True, "noplaylist": True,
                "skip_download": True, "logger": _QuietLogger()}
        _apply_youtube_opts(opts, url, mode)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception:  # noqa: BLE001
            info = None
        if info and info.get("formats"):
            break
    if not info:
        return None
    heights = sorted({f.get("height") for f in info.get("formats", [])
                      if f.get("height") and f.get("vcodec") not in (None, "none")}, reverse=True)
    options = []
    for h in heights[:6]:
        options.append({"label": f"Video {h}p", "format": f"bestvideo[height<={h}]+bestaudio/best[height<={h}]"})
    options.append({"label": "Mejor calidad", "format": "bestvideo+bestaudio/best"})
    options.append({"label": "Solo audio (m4a/mp3)", "format": "bestaudio/best", "audio_only": True})
    return {
        "title": info.get("title") or "video",
        "duration": info.get("duration"),
        "uploader": info.get("uploader"),
        "options": options,
    }


def sanitize_name(name, maxlen=80):
    """Nombre de archivo seguro para Windows: sin caracteres prohibidos."""
    import re
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", str(name or ""))
    s = " ".join(s.split()).strip(" .")
    return (s[:maxlen].rstrip(" .") or "video")


def unique_outname(outdir, base):
    """Nombre base sin colisión en la carpeta: si ya existe un archivo con
    ese nombre, agrega « (2)», « (3)»… como hace un navegador. Sin esto,
    yt-dlp cree que el episodio nuevo «ya se descargó» (todos los streams
    de un mismo sitio se llaman igual) y no baja nada."""
    base = sanitize_name(base)
    try:
        existing = {os.path.splitext(n)[0] for n in os.listdir(outdir)}
    except OSError:
        existing = set()
    if base not in existing:
        return base
    i = 2
    while f"{base} ({i})" in existing:
        i += 1
    return f"{base} ({i})"


def find_ffmpeg():
    """Ruta a ffmpeg: primero junto a este módulo (lo instala el paquete), luego el PATH."""
    import shutil
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "ffmpeg.exe"), os.path.join(here, "ffmpeg")):
        if os.path.exists(cand):
            return cand
    return shutil.which("ffmpeg")


def validate_media(path):
    """True si ffprobe confirma que el archivo es un medio reproducible."""
    import json
    import subprocess
    ffmpeg = find_ffmpeg()
    if not ffmpeg or not path or not os.path.exists(path):
        return None  # sin ffprobe no se puede juzgar: no condenar el archivo
    probe = os.path.join(os.path.dirname(ffmpeg), "ffprobe.exe" if ffmpeg.endswith(".exe") else "ffprobe")
    if not os.path.exists(probe):
        import shutil
        probe = shutil.which("ffprobe")
    if not probe:
        return None
    try:
        r = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration",
             "-show_entries", "stream=codec_type", "-of", "json", path],
            capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data = json.loads(r.stdout or "{}")
        dur = float((data.get("format") or {}).get("duration") or 0)
        return dur > 0 and bool(data.get("streams"))
    except Exception:  # noqa: BLE001
        return None


class VideoDownloader:
    """Descarga con yt-dlp reportando progreso con la misma forma que el motor."""

    def __init__(self, url, outdir, format_str, on_progress=None, audio_only=False, fragments=8,
                 headers=None, outname=None):
        self.url = url
        self.outdir = outdir
        self.format_str = format_str
        self.audio_only = audio_only
        self.fragments = max(1, int(fragments))
        self.headers = dict(headers or {})  # p. ej. el Referer que exigen los CDN de streaming
        # Nombre de salida forzado (el título del episodio, no el slug del
        # stream): los streams de un mismo sitio se llaman todos igual y
        # yt-dlp saltaría la descarga creyendo que «ya se descargó».
        self.outname = sanitize_name(outname) if outname else None
        self.on_progress = on_progress
        self.state = "idle"
        self.error = None
        self.title = "video"
        self.final_path = None
        self._ydl = None
        self._cancelled = False

    def _hook(self, d):
        if self._cancelled:
            raise Exception("Cancelado por el usuario")
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            self._emit({
                "state": "downloading",
                "downloaded": d.get("downloaded_bytes", 0),
                "total": total,
                "speed": d.get("speed") or 0,
                "eta": d.get("eta"),
                "error": None,
            })
        elif d.get("status") == "finished":
            self._emit({"state": "merging", "downloaded": 0, "total": None,
                        "speed": 0, "eta": None, "error": None})

    def start(self):
        import threading
        threading.Thread(target=self._run, daemon=True).start()

    def pause(self):
        # yt-dlp no pausa en caliente; la app ofrece cancelar y reintentar
        # (yt-dlp reanuda .part por su cuenta al volver a lanzar la misma descarga).
        self._cancelled = True
        self.state = "paused"

    def cancel(self):
        self._cancelled = True
        self.state = "cancelled"

    def _run(self):
        import time
        import yt_dlp
        run_started = time.time()
        self.state = "downloading"
        ffmpeg = find_ffmpeg()
        last_error = None
        # YouTube: primero la ruta buena (web + Node); si el muro la tumba,
        # un reintento de emergencia con android en su formato combinado.
        attempts = [(mode, self.format_str) for mode in _yt_modes(self.url)]
        if attempts and attempts[-1][0] == "android":
            attempts[-1] = ("android", "best")
        for mode, fmt in attempts:
            opts = {
                "quiet": True, "no_warnings": True, "noplaylist": True,
                "logger": _QuietLogger(),
                "format": fmt,
                "outtmpl": os.path.join(self.outdir, (self.outname + ".%(ext)s") if self.outname
                                        else "%(title).80s.%(ext)s"),
                "progress_hooks": [self._hook],
                "continuedl": True,
                # Los sitios de streaming parten el video en fragmentos (HLS/DASH):
                # bajarlos en paralelo multiplica la velocidad, como los segmentos.
                "concurrent_fragment_downloads": self.fragments,
            }
            _apply_youtube_opts(opts, self.url, mode)
            if self.headers:
                opts["http_headers"] = dict(self.headers)
            if self.audio_only:
                opts["postprocessors"] = [{
                    "key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192",
                }]
            if ffmpeg:
                # Con ffmpeg, yt-dlp une el HLS en un MP4 estándar que abre
                # cualquier reproductor; sin él deja el volcado crudo del stream.
                opts["ffmpeg_location"] = ffmpeg
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    self._ydl = ydl
                    info = ydl.extract_info(self.url, download=True)
                    if info:
                        self.title = info.get("title") or self.title
                        req = info.get("requested_downloads") or []
                        if req:
                            self.final_path = req[0].get("filepath")
                last_error = None
                break
            except Exception as e:  # noqa: BLE001
                last_error = e
                if self.state in ("cancelled", "paused"):
                    break
        try:
            if last_error is not None:
                raise last_error
            if self.state != "cancelled":
                # Sin archivo NO hay «Completada»: yt-dlp a veces termina en
                # silencio sin bajar nada (stream rechazado o caducado).
                if (not self.final_path or not os.path.exists(self.final_path)
                        or os.path.getsize(self.final_path) == 0):
                    self.state = "error"
                    self.error = ("La descarga terminó SIN crear ningún archivo: el servidor "
                                  "del stream no entregó nada (enlace caducado o protección "
                                  "del sitio). Dale play otra vez y reenvía el stream.")
                # Con nombre forzado, el archivo DEBE ser de esta corrida: si
                # su fecha es anterior, yt-dlp lo «saltó» sin bajar nada.
                elif self.outname and os.path.getmtime(self.final_path) < run_started - 2:
                    self.state = "error"
                    self.error = ("El archivo de destino ya existía antes de esta descarga "
                                  "y no se bajó nada nuevo. Borra el archivo viejo y reintenta.")
                # Nunca decir «Completada» sin comprobar que el archivo sirve.
                elif validate_media(self.final_path) is False:
                    self.state = "error"
                    self.error = ("El archivo bajó pero NO es un video válido: el servidor "
                                  "entregó un stream defectuoso. Prueba otra calidad u otro enlace.")
                else:
                    self.state = "done"
        except Exception as e:  # noqa: BLE001
            if self.state not in ("cancelled", "paused"):
                self.state = "error"
                self.error = _friendly_error(e)
        # Al completar, reportar el tamaño REAL del archivo: la fila debe
        # mostrar sus MB, no «?» (un «Completada» sin tamaño huele a falso).
        done_size = 0
        if self.state == "done" and self.final_path and os.path.exists(self.final_path):
            done_size = os.path.getsize(self.final_path)
        self._emit({"state": self.state, "downloaded": done_size, "total": done_size or None,
                    "speed": 0, "eta": None, "error": self.error})

    def _emit(self, data):
        if self.on_progress:
            try:
                self.on_progress(data)
            except Exception:
                pass
