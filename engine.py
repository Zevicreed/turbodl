"""
Motor de descargas segmentadas estilo IDM (v2: segmentación dinámica).

- Divide el archivo en N segmentos y los descarga en paralelo (hilos).
- Cada segmento vive en su propio archivo .part{i}; al completar se fusionan.
- Pausa/reanuda de verdad: el estado queda en disco (partes + meta JSON),
  así que se puede cerrar el programa y continuar después.
- Si el servidor no acepta rangos (Range), cae a descarga simple con reanudación.
- Segmentación dinámica: cuando una conexión termina su tramo, parte por la
  mitad el tramo al que más le falta y se lleva la segunda mitad. Ninguna
  conexión se queda mirando mientras otra se arrastra (el truco de IDM).
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
        self._ranges = []  # [{"start","end","idx","active","fails","last_fail","started"}]
        self._next_part = 0

    # ---------- utilidades de estado ----------
    def _part_path(self, i):
        return f"{self.dest}.part{i}"

    def _save_meta(self):
        meta = {
            "url": self.url, "total": self.total,
            "ranges": [[r["start"], r["end"], r["idx"]] for r in self._ranges],
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
        for r in list(self._ranges):
            try:
                n += os.path.getsize(self._part_path(r["idx"]))
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
        for r in list(self._ranges):
            try:
                os.remove(self._part_path(r["idx"]))
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
                for _ in range(max(1, self.segments)):
                    th = threading.Thread(target=self._slot_loop, daemon=True)
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

    def _partition_ok(self, saved, total):
        """Los tramos guardados deben cubrir [0, total) sin huecos ni solapes:
        solo entonces las partes en disco significan lo que el meta promete."""
        spans = sorted((e[0], e[1]) for e in saved)
        if not spans or spans[0][0] != 0 or spans[-1][1] != total - 1:
            return False
        if spans[-1][0] > spans[-1][1]:
            return False
        return all(spans[i + 1][0] == b + 1 and a <= b
                   for i, (a, b) in enumerate(spans[:-1]))

    def _prepare_segments(self, total):
        meta = self._load_meta()
        saved = (meta or {}).get("ranges") or []
        idxs = [e[2] if len(e) > 2 else i for i, e in enumerate(saved)]
        if (meta and meta["total"] == total and self._partition_ok(saved, total)
                and len(set(idxs)) == len(idxs)):
            # Reanudar con los tramos guardados, aunque su número no coincida
            # con los segmentos elegidos ahora: así sobreviven las descargas
            # del motor v1 y los tramos ya partidos por el v2.
            self._ranges = [{"start": e[0], "end": e[1], "idx": idxs[i],
                             "active": False, "fails": 0, "last_fail": 0.0, "started": 0.0}
                            for i, e in enumerate(saved)]
            self._next_part = max(idxs) + 1
            return
        # Reparto nuevo: descarta partes anteriores incompatibles.
        self.cleanup_parts()
        n = max(1, min(self.segments, total))
        size = total // n
        self._ranges = []
        for i in range(n):
            a = i * size
            b = total - 1 if i == n - 1 else (a + size - 1)
            self._ranges.append({"start": a, "end": b, "idx": i,
                                 "active": False, "fails": 0, "last_fail": 0.0, "started": 0.0})
        self._next_part = n

    # ---------- motor v2: segmentación dinámica ----------
    def _range_pos(self, rng):
        """Byte siguiente a descargar del tramo (inicio + lo que su parte ya tiene)."""
        try:
            have = os.path.getsize(self._part_path(rng["idx"]))
        except OSError:
            have = 0
        return rng["start"] + have

    def _range_done(self, rng):
        return self._range_pos(rng) > rng["end"]

    def _all_done(self):
        return all(self._range_done(r) for r in self._ranges)

    def _min_split(self):
        return max(128 * 1024, (self.total or 0) // 1024)

    def _fail(self, rng):
        rng["fails"] += 1
        rng["last_fail"] = time.time()

    def _pick_work(self):
        """El truco de IDM: ninguna conexión se queda mirando. Primero, un
        tramo libre e incompleto (reanudado o que falló y ya puede reintentar);
        si no hay, se parte por la mitad el tramo al que más le falta y la
        conexión libre se lleva la segunda mitad."""
        with self._lock:
            now = time.time()
            for rng in self._ranges:
                if (not rng["active"] and not self._range_done(rng)
                        and (rng["fails"] <= 5 or now - rng["last_fail"] >= 1.5)):
                    rng["active"] = True
                    return rng
            best, best_rem = None, 0
            for rng in self._ranges:
                # No partir un tramo recién empezado: su conexión aún no ha
                # demostrado ser lenta y lo ya enviado se desperdiciaría.
                if rng["active"] and now - rng["started"] < 1.5:
                    continue
                rem = rng["end"] - self._range_pos(rng) + 1
                if rem > best_rem:
                    best, best_rem = rng, rem
            if best is not None and best_rem >= 2 * self._min_split():
                split = self._range_pos(best) + best_rem // 2
                new = {"start": split, "end": best["end"], "idx": self._next_part,
                       "active": True, "fails": 0, "last_fail": 0.0, "started": 0.0}
                self._next_part += 1
                best["end"] = split - 1
                self._ranges.append(new)
                self._save_meta()
                return new
            return None

    def _slot_loop(self):
        idle_rounds = 0
        while not self._stop.is_set():
            rng = self._pick_work()
            if rng is None:
                if self._all_done() or idle_rounds >= 20:
                    return
                idle_rounds += 1
                time.sleep(0.3)
                continue
            idle_rounds = 0
            try:
                self._download_range(rng)
            finally:
                with self._lock:
                    rng["active"] = False

    def _download_range(self, rng):
        rng["started"] = time.time()
        part = self._part_path(rng["idx"])
        try:
            have = os.path.getsize(part)
        except OSError:
            have = 0
        want = rng["end"] - rng["start"] + 1
        if have > want:
            # Una parte más grande que su tramo no es de fiar: se recorta.
            try:
                os.truncate(part, want)
                have = want
            except OSError:
                pass
        if have >= want:
            return
        pos = rng["start"] + have
        failed = False
        req = urllib.request.Request(self.url, headers={**UA, **self.headers,
                                     "Range": f"bytes={pos}-{rng['end']}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r, open(part, "ab") as f:
                if r.status == 200 and pos > rng["start"]:
                    # El servidor ignoró el Range y manda el archivo entero:
                    # escribirlo aquí corrompería el tramo.
                    self._fail(rng)
                    self.error = "Un segmento falló; pausa y reanuda para reintentar."
                    return
                while not self._stop.is_set():
                    remaining = rng["end"] - pos + 1  # el fin puede encoger (robo)
                    if remaining <= 0:
                        break
                    chunk = r.read(min(131072, remaining))
                    if not chunk:
                        break
                    if len(chunk) > remaining:
                        chunk = chunk[:remaining]
                    f.write(chunk)
                    pos += len(chunk)
        except Exception:  # noqa: BLE001 - el estado global decide si es error
            failed = True
            self._fail(rng)
            if not self._stop.is_set():
                self.error = "Un segmento falló; pausa y reanuda para reintentar."
        if not failed and not self._stop.is_set() and not self._range_done(rng):
            # El servidor cortó antes de tiempo: el tramo se reintentará.
            self._fail(rng)

    def _single_stream(self):
        part = self.dest + ".part0"
        self._ranges = [{"start": 0, "end": self.total or 0, "idx": 0,
                         "active": False, "fails": 0, "last_fail": 0.0, "started": 0.0}]
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
        # Antes de fusionar: cada parte debe tener EXACTAMENTE el tamaño de
        # su tramo — si no, el archivo final sería una mentira bien cosida.
        ordered = sorted(self._ranges, key=lambda r: r["start"])
        for r in ordered:
            part = self._part_path(r["idx"])
            if os.path.getsize(part) != r["end"] - r["start"] + 1:
                raise ValueError("Una parte no coincide con su tramo; no se fusiona.")
        tmp = self.dest + ".merge"
        with open(tmp, "wb") as out:
            for r in ordered:
                with open(self._part_path(r["idx"]), "rb") as f:
                    while True:
                        chunk = f.read(1024 * 256)
                        if not chunk:
                            break
                        out.write(chunk)
        if self.total and os.path.getsize(tmp) != self.total:
            raise ValueError("El archivo fusionado no tiene el tamaño esperado.")
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
