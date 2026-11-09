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


_VIDEO_HOSTS = ("youtube.com", "youtu.be", "tiktok.com", "vimeo.com",
                "facebook.com", "instagram.com", "twitter.com", "x.com",
                "dailymotion.com", "twitch.tv")


def is_video_page(url):
    """True si la URL es la página de un sitio de videos: jamás es un
    archivo descargable tal cual, aunque el extractor falle."""
    import urllib.parse
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:  # noqa: BLE001
        return False
    return any(host == h or host.endswith("." + h) for h in _VIDEO_HOSTS)


def extract_info_full(url):
    """(info, error): como extract_info, pero cuando el sitio no entrega el
    video devuelve la CAUSA legible del último intento. Sin ella, la app no
    distinguía «no es una página de video» de «YouTube nos bloqueó» y
    terminaba bajando la página HTML como si fuera un archivo."""
    if not yt_dlp_available():
        return None, "yt-dlp no está instalado en el entorno de TurboDL."
    import yt_dlp
    info = None
    last_exc = None
    for mode in _yt_modes(url):
        opts = {"quiet": True, "no_warnings": True, "noplaylist": True,
                "skip_download": True, "logger": _QuietLogger()}
        _apply_youtube_opts(opts, url, mode)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:  # noqa: BLE001
            info = None
            last_exc = e
        if info and info.get("formats"):
            break
    if not info:
        if last_exc is not None:
            return None, _friendly_error(last_exc)
        return None, "el sitio no entregó información del video."
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
    }, ""


def extract_info(url):
    """Devuelve dict con title, duration y opciones de calidad, o None si
    la URL no es una página de video reconocible."""
    info, _ = extract_info_full(url)
    return info


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


def _find_ffprobe():
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return None
    probe = os.path.join(os.path.dirname(ffmpeg), "ffprobe.exe" if ffmpeg.endswith(".exe") else "ffprobe")
    if os.path.exists(probe):
        return probe
    import shutil
    return shutil.which("ffprobe")


def probe_media(path):
    """Retrato del archivo según ffprobe: contenedor REAL, duración y codecs.
    None si no se puede inspeccionar (sin ffprobe o archivo ilegible)."""
    import json
    import subprocess
    probe = _find_ffprobe()
    if not probe or not path or not os.path.exists(path):
        return None
    try:
        r = subprocess.run(
            [probe, "-v", "error", "-probesize", "20000000", "-analyzeduration", "20000000",
             "-show_entries", "format=format_name,duration",
             "-show_entries", "stream=codec_type,codec_name", "-of", "json", path],
            capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data = json.loads(r.stdout or "{}")
    except Exception:  # noqa: BLE001
        return None  # ffprobe no corrió: no se puede juzgar (distinto de «no es medio»)
    fmt = (data.get("format") or {}).get("format_name") or ""
    try:
        dur = float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0
    vcodec = acodec = None
    for s in data.get("streams") or []:
        if s.get("codec_type") == "video" and not vcodec:
            vcodec = s.get("codec_name")
        elif s.get("codec_type") == "audio" and not acodec:
            acodec = s.get("codec_name")
    # ffprobe SÍ corrió y no encontró nada: el dict vacío ES el veredicto
    # («esto no es un medio»), no «no se puede juzgar».
    return {"format": fmt, "duration": dur, "vcodec": vcodec, "acodec": acodec}


def validate_media(path):
    """True si ffprobe confirma que el archivo es un medio reproducible."""
    info = probe_media(path)
    if info is None:
        return None  # sin ffprobe no se puede juzgar: no condenar el archivo
    return info["duration"] > 0 and bool(info["vcodec"] or info["acodec"])


# Familias de contenedor que ffprobe reporta para cada extensión. El pecado
# clásico de los streams: contenido MPEG-TS guardado con nombre .mp4 —
# ffprobe lo lee sin problema (por eso la validación vieja lo dejaba pasar),
# pero VLC lo RECHAZA; el reproductor de Windows lo tolera.
_FAMILIES = {
    ".mp4": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
    ".m4v": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
    ".mov": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
    ".mkv": {"matroska", "webm"},
    ".webm": {"matroska", "webm"},
    ".mp3": {"mp3"},
    ".m4a": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
    ".ts": {"mpegts"},
    ".avi": {"avi"},
}


def container_matches(path, info):
    """True si el contenedor REAL del archivo es el que su extensión promete."""
    fam = _FAMILIES.get(os.path.splitext(path)[1].lower())
    if fam is None or not info:
        return True  # extensión que no juzgamos: no condenar
    return bool(set((info.get("format") or "").split(",")) & fam)


FFMPEG_PACK_URLS = [
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
]


def missing_tools():
    """Herramientas externas que el programa necesita y no encuentra.
    Sin ffmpeg, yt-dlp deja los streams en su formato crudo (MPEG-TS con
    nombre .mp4) que VLC rechaza; sin ffprobe no se puede verificar nada."""
    miss = []
    if not find_ffmpeg():
        miss.append("ffmpeg")
    if not _find_ffprobe():
        miss.append("ffprobe")
    return miss


def fetch_ffmpeg(destdir, on_pct=None, urls=None):
    """Auto-reparación: baja un paquete estático de ffmpeg para Windows y
    deja ffmpeg.exe y ffprobe.exe en destdir (la carpeta del programa).
    Devuelve (True, '') o (False, error legible)."""
    import shutil
    import urllib.request
    import zipfile
    tmp = os.path.join(destdir, "ffmpeg-pack.zip")
    last_err = ""
    for url in (urls or FFMPEG_PACK_URLS):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 TurboDL"})
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    chunk = r.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if on_pct and total:
                        on_pct(min(100, int(done * 100 / total)))
            got = {}
            with zipfile.ZipFile(tmp) as z:
                for name in z.namelist():
                    base = name.replace("\\", "/").rstrip("/").split("/")[-1]
                    if base in ("ffmpeg.exe", "ffprobe.exe"):
                        got[base] = name
                if len(got) < 2:
                    raise OSError("el paquete no traía ffmpeg.exe y ffprobe.exe")
                for base, member in got.items():
                    with z.open(member) as src, open(os.path.join(destdir, base), "wb") as out:
                        shutil.copyfileobj(src, out, 1024 * 1024)
            os.remove(tmp)
            healthy, det = probe_health()
            if healthy:
                return True, ""
            last_err = f"se instaló pero la comprobación falló ({det})"
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            try:
                os.remove(tmp)
            except OSError:
                pass
    return False, last_err


def probe_health():
    """¿Responde ffprobe en esta máquina? (ok, detalle). Sin él no hay
    veredictos: mejor decirlo que marcar archivos buenos como dañados."""
    import subprocess
    probe = _find_ffprobe()
    if not probe:
        return (False, "no se encontró ffprobe junto al programa ni en el PATH")
    try:
        r = subprocess.run([probe, "-version"], capture_output=True, text=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        first = (r.stdout or "").splitlines()[0] if r.stdout else ""
        if r.returncode == 0 and "ffprobe" in first:
            return (True, first.strip())
        return (False, f"ffprobe respondió raro (código {r.returncode}): {(r.stderr or '')[:120]}")
    except Exception as e:  # noqa: BLE001
        return (False, f"ffprobe no arrancó: {e}")


def sniff_kind(path):
    """Olfato de cabecera: qué parece el archivo por sus primeros bytes,
    aunque ffprobe no lo abra. «ts» = MPEG-TS limpio; «ts-disfrazado» = hay
    un TS íntegro tras una cabecera falsa (el truco del servidor de Cuevana:
    una cabecera PNG delante del stream); «mp4» = familia ISO; None = ni idea."""
    try:
        with open(path, "rb") as f:
            data = f.read(200000)
    except OSError:
        return None
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "mp4"
    def ts_at(off):
        return (off + 377 <= len(data) and data[off] == 0x47
                and data[off + 188] == 0x47 and data[off + 376] == 0x47)
    if ts_at(0):
        return "ts"
    for off in range(1, min(len(data) - 377, 100000)):
        if data[off] == 0x47 and ts_at(off):
            return "ts-disfrazado"
    return None


def _fmt_size(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.1f} {u}".replace(".0 ", " ")
        n /= 1024


def remux_copy(src, dst, deep=False):
    """Reempaqueta el medio en el contenedor de `dst` SIN recodificar
    (copia de streams: rápido, no pierde calidad). True si dst quedó válido.
    deep=True: ffmpeg escarba hasta 100 MB buscando el medio real — para
    archivos con cabecera disfrazada delante del stream."""
    import subprocess
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return False
    args = [ffmpeg, "-y", "-v", "error"]
    if deep:
        args += ["-probesize", "100M", "-analyzeduration", "100M"]
    args += ["-i", src, "-c", "copy"]
    if dst.lower().endswith((".mp4", ".m4v", ".mov")):
        args += ["-movflags", "+faststart"]
    args.append(dst)
    try:
        subprocess.run(args, capture_output=True, timeout=1800,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:  # noqa: BLE001
        return False
    return os.path.exists(dst) and os.path.getsize(dst) > 0


def _dst_valid(dst):
    info = probe_media(dst)
    if info and info["duration"] > 0 and container_matches(dst, info):
        return True
    try:
        os.remove(dst)
    except OSError:
        pass
    return False


def _ts_offset(path, limit=32000000):
    """Offset donde empieza el MPEG-TS íntegro tras la cabecera disfrazada
    (sincronía 0x47 cada 188 bytes), o None. Busca en un solo bloque."""
    try:
        with open(path, "rb") as f:
            data = f.read(limit)
    except OSError:
        return None

    def ts_at(off):
        return (off + 377 <= len(data) and data[off] == 0x47
                and data[off + 188] == 0x47 and data[off + 376] == 0x47)

    for off in range(0, max(0, len(data) - 377)):
        if data[off] == 0x47 and ts_at(off):
            return off
    return None


def repair_to(src, dst, deep=False):
    """Reempaqueta src en dst y comprueba que dst sí promete lo que dice.
    Si el archivo trae una cabecera disfrazada delante de un MPEG-TS (el
    truco de Cuevana), ffmpeg se atraganta con el disfraz: primero se
    recorta el disfraz a un temporal limpio y se reempaqueta ese.
    Si todo falla, dst NO queda en disco: una copia «(reparado)» vacía
    engaña al usuario y bloquearía los intentos futuros."""
    ok = False
    if sniff_kind(src) == "ts-disfrazado":
        import shutil
        off = _ts_offset(src)
        tmp_clean = src + ".turbodl-clean.ts"
        if off:
            try:
                with open(src, "rb") as fin, open(tmp_clean, "wb") as fout:
                    fin.seek(off)
                    shutil.copyfileobj(fin, fout, 1024 * 1024)
                ok = bool(remux_copy(tmp_clean, dst)) and _dst_valid(dst)
            except OSError:
                ok = False
            finally:
                try:
                    os.remove(tmp_clean)
                except OSError:
                    pass
    if not ok:
        ok = bool(remux_copy(src, dst, deep=deep)) and _dst_valid(dst)
    if not ok:
        try:
            os.remove(dst)
        except OSError:
            pass
    return ok


def _try_rescue(path):
    """Rescate por olfato: si los bytes dicen «hay un MPEG-TS» (limpio o tras
    una cabecera disfrazada) aunque ffprobe solo viera el disfraz — un PNG
    falso se lee como imagen de duración 0 —, se reempaqueta y se comprueba
    que el resultado sí es un video de verdad antes de tocar el original."""
    if sniff_kind(path) not in ("ts", "ts-disfrazado"):
        return False
    ext = os.path.splitext(path)[1]
    tmp = path + ".turbodl-fixed" + ext
    if not repair_to(path, tmp):
        return False
    info = probe_media(tmp)
    if info and info["duration"] > 0 and (info["vcodec"] or info["acodec"]):
        os.replace(tmp, path)
        return True
    try:
        os.remove(tmp)
    except OSError:
        pass
    return False


def ensure_playable(path):
    """Veredicto sobre un archivo recién descargado:
    ("ok") el contenedor es el correcto · ("repaired") venía disfrazado
    (p. ej. MPEG-TS con nombre .mp4) y se convirtió en el sitio ·
    ("invalid") no es un medio válido · ("bad") el contenedor es otro y la
    conversión automática falló · ("unknown") no se pudo inspeccionar."""
    info = probe_media(path)
    if info is None:
        if _try_rescue(path):
            return ("repaired", "")
        return ("unknown", "")
    if not (info["duration"] > 0 and (info["vcodec"] or info["acodec"])):
        if _try_rescue(path):
            return ("repaired", "")
        return ("invalid", "El archivo bajó pero NO es un video válido: el servidor "
                            "entregó un stream defectuoso. Prueba otra calidad u otro enlace.")
    if container_matches(path, info):
        return ("ok", "")
    ext = os.path.splitext(path)[1]
    tmp = path + ".turbodl-fixed" + ext
    if repair_to(path, tmp):
        os.replace(tmp, path)
        return ("repaired", "")
    return ("bad", f"El archivo bajó en un contenedor ({info['format']}) que reproductores "
                    f"como VLC rechazan en un archivo «{ext}», y la conversión automática "
                    "falló. Ábrelo con el reproductor de Windows o repáralo desde la carpeta "
                    "con el botón «Reparar carpeta» tras actualizar.")


VIDEO_EXTS = (".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".ts")


def _repaired_dst(outdir, name):
    base, ext = os.path.splitext(name)
    dst = os.path.join(outdir, base + " (reparado)" + ext)
    i = 2
    while os.path.exists(dst):
        dst = os.path.join(outdir, f"{base} (reparado {i}){ext}")
        i += 1
    return dst


def scan_and_repair_folder(outdir):
    """Revisa los videos de una carpeta y reempaqueta los que llevan un
    contenedor disfrazado (copia «(reparado)» junto al original, que no se
    toca). Devuelve el reporte para mostrarlo en la app. Si ffprobe no
    responde en la máquina, el reporte lo dice (probe_error) en vez de
    condenar todos los archivos como dañados."""
    report = {"ok": 0, "repaired": [], "broken": [], "failed": [], "codec_watch": [],
              "already": [], "probe_error": ""}
    healthy, detail = probe_health()
    if not healthy:
        report["probe_error"] = detail
        return report
    try:
        names = sorted(os.listdir(outdir))
    except OSError:
        return report
    for name in names:
        if ".turbodl" in name or name.endswith(".part") or " (reparado)" in name:
            continue
        if os.path.splitext(name)[1].lower() not in VIDEO_EXTS:
            continue
        path = os.path.join(outdir, name)
        try:
            size = _fmt_size(os.path.getsize(path))
        except OSError:
            size = "?"
        base, ext = os.path.splitext(name)
        # «Ya reparado» solo si la copia existe Y es un video válido: una
        # cáscara vacía de un intento fallido se borra y se reintenta.
        has_copy = False
        for n in names:
            if n.startswith(base + " (reparado"):
                sib = os.path.join(outdir, n)
                sinfo = probe_media(sib)
                if sinfo and sinfo["duration"] > 0 and container_matches(sib, sinfo):
                    has_copy = True
                else:
                    try:
                        os.remove(sib)
                    except OSError:
                        pass
        info = probe_media(path)
        valid = info is not None and info["duration"] > 0 and (info["vcodec"] or info["acodec"])
        if valid and container_matches(path, info):
            report["ok"] += 1
            if info["vcodec"] in ("av1", "vp9"):
                report["codec_watch"].append(name)
            continue
        if valid:
            # Contenedor disfrazado a plena vista (p. ej. MPEG-TS con nombre .mp4).
            if has_copy:
                report["already"].append(name)
                continue
            dst = _repaired_dst(outdir, name)
            if repair_to(path, dst):
                report["repaired"].append(os.path.basename(dst))
            else:
                report["failed"].append(name)
            continue
        # ffprobe no lo abre: ¿es basura, o un stream íntegro tras una
        # cabecera disfrazada (el truco PNG de Cuevana)? El olfato decide.
        kind = sniff_kind(path)
        if kind in ("ts", "ts-disfrazado"):
            if has_copy:
                report["already"].append(name)
                continue
            dst = _repaired_dst(outdir, name)
            if repair_to(path, dst, deep=True):
                report["repaired"].append(os.path.basename(dst) + " (venía con la cabecera disfrazada)")
            else:
                report["broken"].append(f"{name} ({size}) — huele a video íntegro tras una "
                                        "cabecera falsa, pero ni escarbando se pudo abrir")
            continue
        seen = (f"formato «{info['format']}», duración {info['duration']:.0f} s"
                if info else "ffprobe no devolvió nada")
        report["broken"].append(f"{name} ({size}) — {seen}")
    return report


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
                # Nunca decir «Completada» sin comprobar que el archivo sirve
                # Y que su contenedor es el que promete su nombre: un MPEG-TS
                # disfrazado de .mp4 hay reproductores (VLC) que lo rechazan.
                # Si viene disfrazado, se convierte en el sitio automáticamente.
                else:
                    verdict, detail = ensure_playable(self.final_path)
                    if verdict in ("invalid", "bad"):
                        self.state = "error"
                        self.error = detail
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
