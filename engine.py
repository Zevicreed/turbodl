"""
Motor de descargas segmentadas estilo IDM.

- Divide el archivo en N segmentos y los descarga en paralelo (hilos).
- Cada segmento vive en su propio archivo .part{i}; al completar se fusionan.
- Pausa/reanuda de verdad: el estado queda en disco (partes + meta JSON),
  así que se puede cerrar el programa y continuar después.
- Si el servidor no acepta rangos (Range), cae a descarga simple con reanudación.
"""
import json
import os
import threading
import time
import urllib.request
import urllib.error
from urllib.parse import unquote

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TurboDL/1.0"}


def probe(url, extra_headers=None):
    """Devuelve (total_bytes|None, acepta_rangos bool, nombre_sugerido)."""
    hdrs = {**UA, **(extra_headers or {})}
    req = urllib.request.Request(url, headers={**hdrs, "Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            total = None
            cr = r.headers.get("Content-Range")
            if r.status == 206 and cr and "/" in cr:
                try:
                    total = int(cr.rsplit("/", 1)[1])
                except ValueError:
                    total = None
            name = _filename_from(url, r.headers.get("Content-Disposition"))
            return total, r.status == 206, name
    except Exception:
        # Algunos servidores rechazan el Range pero aceptan HEAD/GET normal.
        req = urllib.request.Request(url, method="HEAD", headers=hdrs)
        with urllib.request.urlopen(req, timeout=20) as r:
            total = r.headers.get("Content-Length")
            name = _filename_from(url, r.headers.get("Content-Disposition"))
            return (int(total) if total else None), False, name


def _filename_from(url, disposition):
    if disposition and "filename=" in disposition:
        fn = disposition.split("filename=", 1)[1].strip(' ";')
        if fn:
            return fn
    path = url.split("?")[0].rstrip("/")
    name = path.rsplit("/", 1)[-1] or "descarga.bin"
    return unquote(name)


class SegmentedDownloader:
    """Descarga segmentada con pausa/reanudación. on_progress(dict) opcional."""

    def __init__(self, url, dest_path, segments=8, on_progress=None, headers=None):
        self.url = url
        self.dest = dest_path
        self.segments = max(1, segments)
        self.on_progress = on_progress
        self.headers = dict(headers or {})  # p. ej. Referer que exigen los CDN de streaming
        self.total = None
        self.supports_range = False
        self.downloaded = 0
        self.speed = 0.0  # bytes/s (media móvil)
        self.state = "idle"  # idle|downloading|paused|merging|done|error|cancelled
        self.error = None
        self._stop = threading.Event()
        self._threads = []
        self._lock = threading.Lock()
        self._meta_path = dest_path + ".turbodl.json"
        self._ranges = []  # [(inicio, fin_inclusive, archivo_parte)]

    # ---------- utilidades de estado ----------
    def _part_path(self, i):
        return f"{self.dest}.part{i}"

    def _save_meta(self):
        meta = {
            "url": self.url, "total": self.total,
            "ranges": [[a, b] for a, b, _ in self._ranges],
        }
        try:
            with open(self._meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f)
        except OSError:
            pass

    def _load_meta(self):
        try:
            with open(self._meta_path, encoding="utf-8") as f:
                m = json.load(f)
            if m.get("url") == self.url and m.get("total"):
                return m
        except (OSError, ValueError):
            pass
        return None

    def _current_downloaded(self):
        n = 0
        for _, _, part in self._ranges:
            try:
                n += os.path.getsize(part)
            except OSError:
                pass
        return n

    def eta(self):
        if self.total and self.speed > 0:
            return max(0, (self.total - self.downloaded) / self.speed)
        return None

    # ---------- control ----------
    def start(self):
        if self.state in ("downloading", "merging"):
            return
        self._stop.clear()
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def pause(self):
        if self.state == "downloading":
            self._stop.set()
            self.state = "paused"

    def cancel(self):
        self._stop.set()
        self.state = "cancelled"

    def cleanup_parts(self):
        for _, _, part in self._ranges:
            try:
                os.remove(part)
            except OSError:
                pass
        try:
            os.remove(self._meta_path)
        except OSError:
            pass

    # ---------- núcleo ----------
    def _run(self):
        try:
            self.state = "downloading"
            total, ranges_ok, _ = probe(self.url, self.headers)
            self.total = total
            self.supports_range = ranges_ok
            if total and ranges_ok:
                self._prepare_segments(total)
                self._save_meta()
                self.downloaded = self._current_downloaded()
                self._threads = []
                for i, (a, b, part) in enumerate(self._ranges):
                    th = threading.Thread(target=self._worker, args=(a, b, part), daemon=True)
                    th.start()
                    self._threads.append(th)
                self._monitor()
                for th in self._threads:
                    th.join()
                if self._stop.is_set():
                    self.downloaded = self._current_downloaded()
                    self._emit()
                    return
                if self.downloaded >= total:
                    self.state = "merging"
                    self._emit()
                    self._merge()
                    self.cleanup_parts()
                    self.state = "done"
                else:
                    self.state = "error"
                    self.error = "La descarga se detuvo antes de completar."
            else:
                self._single_stream()
            self._emit()
        except Exception as e:  # noqa: BLE001 - se reporta al usuario vía state/error
            self.state = "error"
            self.error = str(e)
            self._emit()

    def _prepare_segments(self, total):
        meta = self._load_meta()
        if meta and meta["total"] == total and len(meta["ranges"]) == self.segments:
            self._ranges = [(a, b, self._part_path(i)) for i, (a, b) in enumerate(meta["ranges"])]
            return
        # Reparto nuevo: descarta partes anteriores incompatibles.
        self.cleanup_parts()
        n = self.segments
        size = total // n
        self._ranges = []
        for i in range(n):
            a = i * size
            b = total - 1 if i == n - 1 else (a + size - 1)
            self._ranges.append((a, b, self._part_path(i)))

    def _worker(self, start, end, part):
        have = 0
        try:
            have = os.path.getsize(part)
        except OSError:
            have = 0
        want = end - start + 1
        if have >= want:
            return
        pos = start + have
        req = urllib.request.Request(self.url, headers={**UA, **self.headers, "Range": f"bytes={pos}-{end}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r, open(part, "ab") as f:
                while not self._stop.is_set():
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
        except Exception:  # noqa: BLE001 - el estado global decide si es error
            if not self._stop.is_set():
                self.error = "Un segmento falló; pausa y reanuda para reintentar."

    def _single_stream(self):
        part = self.dest + ".part0"
        self._ranges = [(0, self.total or 0, part)]
        have = 0
        try:
            have = os.path.getsize(part)
        except OSError:
            have = 0
        headers = {**UA, **self.headers}
        mode = "wb"
        if have and self.total:
            headers["Range"] = f"bytes={have}-"
            mode = "ab"
        req = urllib.request.Request(self.url, headers=headers)
        last_t = time.time()
        last_n = have
        with urllib.request.urlopen(req, timeout=30) as r, open(part, mode) as f:
            if mode == "ab" and r.status != 206:
                f.close()
                with open(part, "wb") as f2:
                    have = 0
                    last_n = 0
                    while not self._stop.is_set():
                        chunk = r.read(65536)
                        if not chunk:
                            break
                        f2.write(chunk)
                        have += len(chunk)
                        self._tick_speed(have, last_t, last_n)
                self.downloaded = have
            else:
                while not self._stop.is_set():
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    have += len(chunk)
                    now = time.time()
                    if now - last_t >= 0.4:
                        inst = (have - last_n) / (now - last_t)
                        self.speed = inst if self.speed == 0 else 0.7 * self.speed + 0.3 * inst
                        last_t, last_n = now, have
                    self.downloaded = have
                    self._emit()
        self.downloaded = have
        if self._stop.is_set():
            return
        os.replace(part, self.dest)
        self.state = "done"

    def _tick_speed(self, *_):
        pass

    def _monitor(self):
        last_t = time.time()
        last_n = self.downloaded
        while any(th.is_alive() for th in self._threads) and not self._stop.is_set():
            time.sleep(0.35)
            self.downloaded = self._current_downloaded()
            now = time.time()
            inst = (self.downloaded - last_n) / max(0.01, now - last_t)
            self.speed = inst if self.speed == 0 else 0.65 * self.speed + 0.35 * inst
            last_t, last_n = now, self.downloaded
            self._emit()
        self.downloaded = self._current_downloaded()

    def _merge(self):
        tmp = self.dest + ".merge"
        with open(tmp, "wb") as out:
            for _, _, part in self._ranges:
                with open(part, "rb") as f:
                    while True:
                        chunk = f.read(1024 * 256)
                        if not chunk:
                            break
                        out.write(chunk)
        os.replace(tmp, self.dest)

    def _emit(self):
        if self.on_progress:
            try:
                self.on_progress({
                    "state": self.state, "downloaded": self.downloaded,
                    "total": self.total, "speed": self.speed,
                    "eta": self.eta(), "error": self.error,
                })
            except Exception:
                pass


def format_bytes(n):
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def format_eta(s):
    if s is None:
        return "—"
    s = int(s)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    return f"{s // 3600}h {(s % 3600) // 60}m"
