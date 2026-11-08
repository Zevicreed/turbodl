"""
TurboDL — descargador estilo IDM (v1).

- Enlaces directos: motor propio segmentado (paralelo, pausa/reanudación real).
- Páginas de video (YouTube, TikTok…): vía yt-dlp, con elección de calidad.
Ejecutar:  python app.py     (solo requiere yt-dlp para la parte de video)
"""
import json
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine import SegmentedDownloader, probe, format_bytes, format_eta  # noqa: E402
import video as videomod  # noqa: E402
from server import serve_in_thread, PORT as EXT_PORT  # noqa: E402

APP_VERSION = "1.3.10"
try:
    import updater as updatermod
except Exception:  # noqa: BLE001 — sin módulo de actualización, la app sigue
    updatermod = None

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "Downloads")


class TurboApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TurboDL — Descargador")
        _ico = os.path.join(APP_DIR, "TurboDL.ico")
        if os.path.exists(_ico):
            try:
                self.iconbitmap(_ico)
            except tk.TclError:
                pass
        self.geometry("880x540")
        self.minsize(760, 460)
        self.downloads = {}   # iid -> {"dl": obj, "prog": dict, "kind": "file"|"video"}
        self._validated = set()   # descargas directas ya pasadas por el inspector
        self._counter = 0
        self.ui_queue = queue.Queue()  # los hilos trabajadores piden acciones de UI por aquí
        self._cfg = self._load_cfg()
        videomod.set_youtube_cookies(bool(self._cfg.get("yt_cookies", False)))
        self._build_ui()
        self._scan_resumables()
        # Puente con la extensión del navegador (puerto local).
        self.job_queue = queue.Queue()
        self.server_ok = False
        try:
            serve_in_thread(self.job_queue)
            self.server_ok = True
        except OSError:
            self.server_ok = False
        base = self.status.get()
        if self.server_ok:
            base += f"  ·  Extensión TurboDL: escuchando en el puerto {EXT_PORT}."
        self.status.set(base + f"  ·  TurboDL v{APP_VERSION}")
        self.after(150, self._refresh)
        if "--inicio" in sys.argv:
            # Arranque con Windows: minimizado en la barra de tareas, con el
            # puente de la extensión ya escuchando (como IDM en la bandeja).
            self.iconify()
        # Revisión silenciosa de actualizaciones: el botón solo aparece si hay.
        self._update_info = None
        threading.Thread(target=self._check_updates, daemon=True).start()
        # Sin ffmpeg no hay conversión ni verificación: avisar y ofrecer la
        # auto-reparación en vez de dejar que los videos salgan crudos.
        if videomod.missing_tools():
            self.ffmpeg_btn.pack(side="right", padx=3)
            self.status.set("Falta ffmpeg junto al programa: sin él, los videos de stream "
                            "bajan sin convertir y VLC los rechaza. Pulsa «Instalar ffmpeg» "
                            "y TurboDL lo baja y se repara solo.")

    # ---------------- UI ----------------
    # ---------------- configuración persistente ----------------
    def _cfg_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "turbodl-config.json")

    def _load_cfg(self):
        try:
            with open(self._cfg_path(), encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return {}

    def _save_cfg(self):
        try:
            with open(self._cfg_path(), "w", encoding="utf-8") as f:
                json.dump(self._cfg, f)
        except Exception:  # noqa: BLE001
            pass

    def _on_yt_cookies(self):
        on = bool(self.yt_cookies_var.get())
        self._cfg["yt_cookies"] = on
        self._save_cfg()
        videomod.set_youtube_cookies(on)
        self.status.set("Sesión de Chrome para YouTube: " + ("activada." if on else "desactivada."))

    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=24)

        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Enlace:").grid(row=0, column=0, sticky="w")
        self.url_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.url_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(top, text="Agregar y descargar", command=self._add).grid(row=0, column=2)
        top.columnconfigure(1, weight=1)

        row2 = ttk.Frame(self, padding=(10, 0, 10, 6))
        row2.pack(fill="x")
        ttk.Label(row2, text="Carpeta:").grid(row=0, column=0, sticky="w")
        self.dir_var = tk.StringVar(value=self._cfg.get("dir") or DEFAULT_DIR)
        ttk.Entry(row2, textvariable=self.dir_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(row2, text="Examinar…", command=self._pick_dir).grid(row=0, column=2)
        ttk.Label(row2, text="Segmentos:").grid(row=0, column=3, padx=(14, 4))
        self.seg_var = tk.IntVar(value=8)
        ttk.Spinbox(row2, from_=1, to=16, width=4, textvariable=self.seg_var).grid(row=0, column=4)
        row2.columnconfigure(1, weight=1)

        row3 = ttk.Frame(self, padding=(10, 0, 10, 2))
        row3.pack(fill="x")
        self.yt_cookies_var = tk.BooleanVar(value=bool(self._cfg.get("yt_cookies", False)))
        ttk.Checkbutton(row3, text="YouTube: usar mi sesión de Chrome (para su control anti-bots)",
                        variable=self.yt_cookies_var,
                        command=self._on_yt_cookies).grid(row=0, column=0, sticky="w")

        mid = ttk.Frame(self, padding=(10, 4))
        mid.pack(fill="both", expand=True)
        cols = ("nombre", "tamano", "progreso", "velocidad", "eta", "estado")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
        for col, txt, w in [("nombre", "Nombre", 300), ("tamano", "Tamaño", 90),
                            ("progreso", "Progreso", 90), ("velocidad", "Velocidad", 100),
                            ("eta", "ETA", 70), ("estado", "Estado", 120)]:
            self.tree.heading(col, text=txt)
            self.tree.column(col, width=w, stretch=(col == "nombre"))
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)

        bar = ttk.Frame(self, padding=(10, 2))
        bar.pack(fill="x")
        self.progress = ttk.Progressbar(bar, mode="determinate", maximum=100)
        self.progress.pack(fill="x")

        btns = ttk.Frame(self, padding=10)
        btns.pack(fill="x")
        ttk.Button(btns, text="Pausar", command=lambda: self._ctl("pause")).pack(side="left", padx=3)
        ttk.Button(btns, text="Reanudar", command=lambda: self._ctl("start")).pack(side="left", padx=3)
        ttk.Button(btns, text="Cancelar", command=lambda: self._ctl("cancel")).pack(side="left", padx=3)
        ttk.Button(btns, text="Quitar de la lista", command=self._remove).pack(side="left", padx=3)
        ttk.Button(btns, text="Reparar carpeta", command=self._repair_dir).pack(side="left", padx=3)
        ttk.Button(btns, text="Abrir carpeta", command=self._open_dir).pack(side="right", padx=3)
        # Botón de actualización: creado pero NO empaquetado; aparece solo
        # cuando el chequeo de arranque encuentra una versión más nueva.
        self.update_btn = ttk.Button(btns, text="", command=self._apply_update)
        # Botón de auto-reparación: aparece solo si falta ffmpeg/ffprobe.
        self.ffmpeg_btn = ttk.Button(btns, text="Instalar ffmpeg", command=self._install_ffmpeg)

        self.status = tk.StringVar(value="Listo. Pega un enlace de archivo o de video (YouTube, TikTok, etc.).")
        ttk.Label(self, textvariable=self.status, padding=(10, 4)).pack(fill="x", side="bottom")
        if not videomod.yt_dlp_available():
            self.status.set("Listo. OJO: yt-dlp no está instalado — los enlaces directos funcionan; "
                            "para videos de páginas instala:  pip install yt-dlp")

    # ---------------- acciones ----------------
    def _pick_dir(self):
        d = filedialog.askdirectory(initialdir=self.dir_var.get() or DEFAULT_DIR)
        if d:
            self.dir_var.set(d)
            self._cfg["dir"] = d  # la carpeta elegida se recuerda al reabrir
            self._save_cfg()

    def _open_dir(self):
        d = self.dir_var.get()
        os.makedirs(d, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(d)  # noqa: S606
        elif sys.platform == "darwin":
            os.system(f'open "{d}"')
        else:
            os.system(f'xdg-open "{d}"')

    def _add(self):
        url = self.url_var.get().strip()
        if not url:
            self.status.set("Pega primero un enlace.")
            return
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        self.status.set("Analizando el enlace…")
        # Capturar las variables tk AQUÍ (hilo principal) y pasarlas al hilo.
        outdir = self.dir_var.get()
        segs = int(self.seg_var.get())
        threading.Thread(target=self._analyze, args=(url, outdir, segs), daemon=True).start()

    def _analyze(self, url, outdir, segs):
        os.makedirs(outdir, exist_ok=True)
        info = videomod.extract_info(url)
        if info:
            self._ui(lambda: self._video_dialog(url, info, outdir))
            return
        try:
            total, _, name = probe(url)
        except Exception as e:  # noqa: BLE001
            self._ui(lambda: self.status.set(f"No pude abrir ese enlace: {e}"))
            return
        dest = os.path.join(outdir, name)
        dl = SegmentedDownloader(url, dest, segments=segs)
        self._ui(lambda: self._register_download(url, os.path.basename(dest), dl, "file",
                                             f"Descargando {name} ({format_bytes(total)}) con motor segmentado."))

    def _video_dialog(self, url, info, outdir):
        win = tk.Toplevel(self)
        win.title("Video detectado")
        win.geometry("460x210")
        win.transient(self)
        dur = info.get("duration")
        dur_txt = f"{int(dur // 60)}:{int(dur % 60):02d}" if dur else "?"
        ttk.Label(win, text=info["title"], wraplength=420, padding=10).pack(fill="x")
        ttk.Label(win, text=f"Duración: {dur_txt}   Canal: {info.get('uploader') or ''}", padding=(10, 0)).pack(fill="x")
        labels = [o["label"] for o in info["options"]]
        sel = tk.StringVar(value=labels[0])
        cb = ttk.Combobox(win, values=labels, textvariable=sel, state="readonly", width=30)
        cb.pack(pady=12)

        def go():
            opt = info["options"][labels.index(sel.get())]
            dl = videomod.VideoDownloader(url, outdir, opt["format"],
                                          audio_only=opt.get("audio_only", False),
                                          fragments=int(self.seg_var.get()))
            win.destroy()
            self._register_download(url, info["title"], dl, "video",
                           f"Descargando video: {info['title']} ({opt['label']}).")
            dl.start()

        ttk.Button(win, text="Descargar", command=go).pack(side="left", padx=14, pady=6)
        ttk.Button(win, text="Cancelar", command=win.destroy).pack(side="right", padx=14, pady=6)

    def _register_download(self, url, name, dl, kind, msg):
        self._counter += 1
        iid = f"dl{self._counter}"
        prog = {"state": "idle", "downloaded": 0, "total": None, "speed": 0, "eta": None, "error": None}
        dl.on_progress = lambda data, p=prog: p.update(data)
        self.downloads[iid] = {"dl": dl, "prog": prog, "kind": kind, "url": url}
        self.tree.insert("", "end", iid=iid, values=(name, "?", "0 %", "—", "—", "En cola"))
        self.status.set(msg)
        if kind == "file":
            dl.start()
        self.url_var.set("")

    def _selected(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _on_select(self, _evt=None):
        """Al elegir una fila, la barra de estado muestra su detalle COMPLETO:
        en la lista el error se recorta a 40 caracteres y no se podía leer."""
        iid = self._selected()
        if not iid:
            return
        item = self.downloads.get(iid)
        if not item:
            return
        vals = self.tree.item(iid, "values")
        name = vals[0] if vals else iid
        p = item["prog"]
        if item.get("post_error"):
            self.status.set(f"{name} — Error completo: {item['post_error']}")
        elif p.get("state") == "error" and p.get("error"):
            self.status.set(f"{name} — Error completo: {p['error']}")
        elif item.get("post_note"):
            self.status.set(f"{name} — {item['post_note']}: el archivo venía en un "
                            "contenedor que VLC rechaza y se convirtió a MP4 estándar.")
        else:
            self.status.set(f"{name} — {self._STATE_TXT.get(p.get('state', 'idle'), '')}")

    def _validate_file_job(self, iid, path):
        """Las descargas directas (motor segmentado) no pasan por yt-dlp:
        su contenedor se comprueba — y se convierte si hace falta — aquí,
        para que ninguna «Completada» sea un video que VLC no abre."""
        verdict, detail = videomod.ensure_playable(path)

        def apply():
            item = self.downloads.get(iid)
            if not item:
                return
            if verdict == "repaired":
                item["post_note"] = "Completada (convertida a MP4)"
                self.status.set(f"{os.path.basename(path)}: venía en un contenedor que "
                                "VLC rechaza; quedó convertido a MP4 estándar.")
            elif verdict in ("invalid", "bad"):
                item["post_error"] = detail
                self.status.set(f"{os.path.basename(path)}: {detail}")

        self._ui(apply)

    def _ctl(self, action):
        iid = self._selected()
        if not iid:
            self.status.set("Selecciona primero una descarga de la lista.")
            return
        item = self.downloads.get(iid)
        if not item:
            return
        dl = item["dl"]
        if action == "pause":
            dl.pause()
            if item["kind"] == "video":
                self.status.set("Video detenido. Al reanudar, yt-dlp continúa desde su .part si el sitio lo permite.")
        elif action == "start":
            if item["kind"] == "video" and dl.state in ("paused", "cancelled", "error"):
                # yt-dlp no reanuda el mismo objeto: se crea uno nuevo con la misma config
                new = videomod.VideoDownloader(dl.url, dl.outdir, dl.format_str,
                                               audio_only=dl.audio_only,
                                               fragments=getattr(dl, "fragments", 8),
                                               headers=getattr(dl, "headers", None),
                                               outname=getattr(dl, "outname", None))
                new.on_progress = dl.on_progress
                item["dl"] = new
                new.start()
            else:
                dl.start()
        elif action == "cancel":
            dl.cancel()
            if item["kind"] == "file":
                self.status.set("Descarga cancelada (los segmentos quedan en disco; Reanudar continúa).")

    def _remove(self):
        iid = self._selected()
        if not iid:
            return
        item = self.downloads.pop(iid, None)
        if item and item["dl"].state in ("downloading", "merging"):
            item["dl"].cancel()
        self.tree.delete(iid)

    # ---------------- reparación de videos ----------------
    def _repair_dir(self):
        outdir = self.dir_var.get()
        if not outdir or not os.path.isdir(outdir):
            self.status.set("La carpeta de destino no existe.")
            return
        self.status.set("Revisando los videos de la carpeta… (los grandes tardan un poco)")
        threading.Thread(target=self._repair_dir_run, args=(outdir,), daemon=True).start()

    def _repair_dir_run(self, outdir):
        report = videomod.scan_and_repair_folder(outdir)
        self._ui(lambda: self._repair_report(report))

    def _repair_report(self, report):
        from tkinter import messagebox
        if report.get("probe_error"):
            # Sin inspector no hay veredictos: decirlo claro en vez de
            # marcar todos los archivos como dañados.
            self.status.set("Reparar carpeta: el inspector de video (ffprobe) no responde.")
            messagebox.showwarning(
                "Reparar carpeta",
                "OJO: el inspector de video (ffprobe) no respondió en esta PC:\n"
                f"{report['probe_error']}\n\n"
                "Sin él no puedo juzgar ningún archivo, así que no marco nada "
                "como dañado. Avísale a Zevi con este mensaje.")
            return
        lines = []
        if report["repaired"]:
            lines.append("Reparados (copia nueva junto al original, que no se toca):")
            lines += [f"  • {n}" for n in report["repaired"]]
        if report["failed"]:
            lines.append("No se pudieron reparar:")
            lines += [f"  • {n}" for n in report["failed"]]
        if report["broken"]:
            lines.append("No son videos válidos (la descarga vino defectuosa):")
            lines += [f"  • {n}" for n in report["broken"]]
        if report["codec_watch"]:
            lines.append("Ojo: llevan codec AV1/VP9 — un VLC viejo puede fallar con estos:")
            lines += [f"  • {n}" for n in report["codec_watch"]]
        if report.get("already"):
            lines.append("Ya tenían su copia «(reparado)» de un barrido anterior:")
            lines += [f"  • {n}" for n in report["already"]]
        head = (f"{report['ok']} video(s) ya estaban bien · "
                f"{len(report['repaired'])} reparado(s) · "
                f"{len(report['failed'])} sin arreglo · {len(report['broken'])} dañado(s).")
        self.status.set("Reparación de carpeta: " + head)
        messagebox.showinfo(
            "Reparar carpeta",
            head + ("\n\n" + "\n".join(lines) if lines
                    else "\n\nNada que reparar: todo está en el contenedor que su nombre promete."))

    def _scan_resumables(self):
        """Al abrir, ofrece reanudar descargas con meta pendiente en la carpeta."""
        outdir = self.dir_var.get()
        try:
            names = os.listdir(outdir)
        except OSError:
            return
        import json
        for n in names:
            if n.endswith(".turbodl.json"):
                dest = os.path.join(outdir, n[: -len(".turbodl.json")])
                try:
                    with open(os.path.join(outdir, n), encoding="utf-8") as f:
                        meta = json.load(f)
                    dl = SegmentedDownloader(meta["url"], dest, segments=int(self.seg_var.get()))
                    dl.total = meta.get("total")
                    self._counter += 1
                    iid = f"dl{self._counter}"
                    prog = {"state": "paused", "downloaded": 0, "total": dl.total,
                            "speed": 0, "eta": None, "error": None}
                    dl.on_progress = lambda data, p=prog: p.update(data)
                    self.downloads[iid] = {"dl": dl, "prog": prog, "kind": "file", "url": meta["url"]}
                    self.tree.insert("", "end", iid=iid,
                                     values=(os.path.basename(dest), format_bytes(dl.total), "—", "—", "—", "Pausada (reanusable)"))
                except Exception:
                    pass

    # ---------------- actualizaciones ----------------
    def _check_updates(self):
        if updatermod is None:
            return
        info = updatermod.check(APP_VERSION)
        if info:
            self._update_info = info
            self._ui(lambda: self._show_update_button(info))

    def _show_update_button(self, info):
        self.update_btn.configure(text=f"⬆ Actualizar a v{info['version']}")
        if not self.update_btn.winfo_ismapped():
            self.update_btn.pack(side="right", padx=3)
        self.status.set(f"Hay una actualización disponible: TurboDL v{info['version']}. "
                        "Pulsa «Actualizar» y la app se actualiza y se reinicia sola.")

    def _apply_update(self):
        if updatermod is None or not self._update_info:
            return
        self.update_btn.configure(state="disabled")
        self.status.set("Descargando la actualización…")
        threading.Thread(target=self._apply_update_run, daemon=True).start()

    def _apply_update_run(self):
        ok, err = updatermod.apply(APP_DIR)
        if not ok:
            self._ui(lambda: (self.update_btn.configure(state="normal"),
                              self.status.set(f"No se pudo actualizar: {err}")))
            return

        def restart():
            import subprocess
            try:
                subprocess.Popen([sys.executable, os.path.join(APP_DIR, "app.py")],
                                 cwd=APP_DIR)
            except Exception:  # noqa: BLE001
                pass
            self.destroy()

        self._ui(lambda: (self.status.set("Actualizado. Reiniciando TurboDL…"),
                          self.after(700, restart)))

    # ---------------- ffmpeg: auto-reparación ----------------
    def _install_ffmpeg(self):
        self.ffmpeg_btn.configure(state="disabled")
        self.status.set("Descargando ffmpeg… puede tardar según tu conexión.")
        threading.Thread(target=self._install_ffmpeg_run, daemon=True).start()

    def _install_ffmpeg_run(self):
        def pct(p):
            self._ui(lambda: self.status.set(f"Descargando ffmpeg… {p} %"))
        ok, err = videomod.fetch_ffmpeg(APP_DIR, pct)
        if ok:
            def done():
                self.ffmpeg_btn.pack_forget()
                self.status.set(f"ffmpeg instalado en {APP_DIR}. Ya puedes pulsar "
                                "«Reparar carpeta» para sanar los videos que VLC no abre.")
            self._ui(done)
        else:
            def fail():
                self.ffmpeg_btn.configure(state="normal")
                self.status.set(f"No pude instalar ffmpeg: {err}")
            self._ui(fail)

    # ---------------- trabajos desde la extensión ----------------
    def _ext_job(self, job):
        url = job["url"]
        referer = job.get("referer") or ""
        outdir = self.dir_var.get()
        os.makedirs(outdir, exist_ok=True)
        headers = {"Referer": referer} if referer else {}
        segs = int(self.seg_var.get())  # las variables tk solo se leen en el hilo principal
        path = url.split("?")[0].lower()
        if path.endswith((".m3u8", ".mpd")):
            # El archivo sale con el título del EPISODIO y nombre único: los
            # streams de un sitio se llaman todos igual (index-f3-v1-a1) y
            # yt-dlp saltaba cada episodio tras el primero («ya descargado»).
            outname = videomod.unique_outname(outdir, (job.get("title") or "stream").strip() or "stream")
            dl = videomod.VideoDownloader(url, outdir, "best", fragments=segs, headers=headers,
                                          outname=outname)
            self._register_download(url, outname, dl, "video",
                                    f"Stream capturado por la extensión: {outname} (HLS/DASH vía yt-dlp).")
            dl.start()
        elif path.endswith((".mp4", ".webm", ".mkv", ".avi", ".mov", ".mp3", ".m4a",
                            ".zip", ".rar", ".7z", ".pdf", ".exe", ".msi")):
            threading.Thread(target=self._ext_direct, args=(url, headers, outdir, segs), daemon=True).start()
        else:
            threading.Thread(target=self._ext_page, args=(url, outdir, segs), daemon=True).start()

    def _ext_direct(self, url, headers, outdir, segs=8):
        try:
            total, _, name = probe(url, headers)
        except Exception as e:  # noqa: BLE001
            self._ui(lambda: self.status.set(f"La extensión envió un enlace que no abrió: {e}"))
            return
        dest = os.path.join(outdir, name)
        dl = SegmentedDownloader(url, dest, segments=segs, headers=headers)
        self._ui(lambda: self._register_download(
            url, os.path.basename(dest), dl, "file",
            f"Desde la extensión: {name} ({format_bytes(total)}) con motor segmentado."))

    def _ext_page(self, url, outdir, segs=8):
        info = videomod.extract_info(url)
        if info:
            opt = info["options"][0]
            dl = videomod.VideoDownloader(url, outdir, opt["format"],
                                          audio_only=opt.get("audio_only", False),
                                          fragments=segs)
            self._ui(lambda: (self._register_download(
                url, info["title"], dl, "video",
                f"Página enviada por la extensión: {info['title']} ({opt['label']})."), dl.start()))
        else:
            self._ext_direct(url, {}, outdir, segs)

    # ---------------- refresco ----------------
    _STATE_TXT = {"idle": "En cola", "downloading": "Descargando", "paused": "Pausada",
                  "merging": "Uniendo segmentos…", "done": "Completada", "error": "Error",
                  "cancelled": "Cancelada"}

    def _ui(self, fn):
        """Ejecuta fn en el hilo principal (los hilos nunca tocan tkinter)."""
        self.ui_queue.put(fn)

    def _refresh(self):
        # Acciones de UI pedidas por los hilos trabajadores.
        while True:
            try:
                fn = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:  # noqa: BLE001 — una acción mala no debe matar el refresco
                pass
        # Recoger trabajos que dejó la extensión del navegador.
        while True:
            try:
                job = self.job_queue.get_nowait()
            except queue.Empty:
                break
            self._ext_job(job)
        sel = self._selected()
        for iid, item in self.downloads.items():
            p = item["prog"]
            dl = item["dl"]
            # Para descargas de archivo sin callback reciente, lee el objeto directamente.
            if item["kind"] == "file" and dl.state != p.get("state"):
                p.update({"state": dl.state, "downloaded": dl.downloaded, "total": dl.total,
                          "speed": dl.speed, "eta": dl.eta(), "error": dl.error})
            if item.get("post_error"):
                p.update({"state": "error", "error": item["post_error"]})
            # Las descargas directas también pasan por el inspector al terminar:
            # si el video venía en un contenedor que VLC rechaza, se convierte.
            if (item["kind"] == "file" and p.get("state") == "done"
                    and iid not in self._validated
                    and os.path.splitext(getattr(dl, "dest", "") or "")[1].lower()
                    in videomod.VIDEO_EXTS):
                self._validated.add(iid)
                threading.Thread(target=self._validate_file_job,
                                 args=(iid, dl.dest), daemon=True).start()
            total = p.get("total")
            down = p.get("downloaded") or 0
            pct = (down / total * 100) if total else 0
            state = p.get("state", "idle")
            txt = self._STATE_TXT.get(state, state)
            if state == "error" and p.get("error"):
                txt = "Error: " + str(p["error"])[:40]
            if state == "done" and item.get("post_note"):
                txt = item["post_note"]
            vals = list(self.tree.item(iid, "values"))
            if vals:
                vals[1] = format_bytes(total)
                if total:
                    full = 6 if state == "done" else max(0, min(6, int(pct / 100 * 6 + 0.5)))
                    vals[2] = "█" * full + "░" * (6 - full) + f" {pct:.0f} %"
                else:
                    vals[2] = "…"
                vals[3] = format_bytes(p.get("speed") or 0) + "/s" if state == "downloading" else "—"
                vals[4] = format_eta(p.get("eta"))
                vals[5] = txt
                self.tree.item(iid, values=vals)
        # La barra principal sigue a la fila elegida; sin elección, sigue
        # sola a la descarga activa (antes se quedaba vacía si no clicabas).
        focus = sel
        if not focus:
            for iid2, item2 in self.downloads.items():
                if item2["prog"].get("state") in ("downloading", "merging"):
                    focus = iid2
                    break
        if focus and focus in self.downloads:
            pf = self.downloads[focus]["prog"]
            tf = pf.get("total")
            self.progress["value"] = ((pf.get("downloaded") or 0) / tf * 100) if tf else 0
        self.after(150, self._refresh)


if __name__ == "__main__":
    TurboApp().mainloop()
