"""
Auto-actualización de TurboDL desde su repo público de GitHub.

El repo (github.com/Zevicreed/turbodl) es el canal estable: guarda el
código (solo archivos de texto) y un version.json con la última versión.
La app consulta ese JSON al abrir; si hay algo más nuevo, muestra el
botón «Actualizar» — y solo entonces. Al pulsarlo, descarga los
archivos, los aplica y se reinicia sola.

Lo que jamás se toca: el entorno (.venv), ffmpeg/ffprobe, el icono y
la configuración del usuario (turbodl-config.json) — no están en la
lista de archivos actualizables.
"""
import json
import os
import urllib.parse
import urllib.request

REPO = "Zevicreed/turbodl"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/main"
MANIFEST_URL = RAW_BASE + "/version.json"

UPDATE_FILES = [
    "app.py", "engine.py", "video.py", "server.py", "updater.py",
    "README.md", "requirements.txt", "instalar.ps1",
    "Instalar TurboDL.bat", "Quitar TurboDL del inicio.bat", "Correr.bat",
    "extension/manifest.json", "extension/background.js",
    "extension/content.js", "extension/popup.js", "extension/popup.html",
    "extension/popup.css",
]

_UA = {"User-Agent": "TurboDL-Updater"}


def _fetch(url, timeout=15):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _version_tuple(s):
    out = []
    for part in str(s).strip().split("."):
        out.append(int(part) if part.isdigit() else 0)
    return tuple(out)


def check(current_version):
    """{'version':…, 'notes':…} si hay una versión más nueva; si no, None.
    Cualquier fallo (sin red, repo caído) devuelve None: jamás molesta."""
    try:
        data = json.loads(_fetch(MANIFEST_URL, timeout=10).decode("utf-8"))
        latest = str(data.get("version") or "")
        if latest and _version_tuple(latest) > _version_tuple(current_version):
            return {"version": latest, "notes": str(data.get("notes") or "")}
    except Exception:  # noqa: BLE001
        pass
    return None


def apply(app_dir):
    """Descarga primero TODO a memoria; solo entonces escribe. Así una
    caída de red a mitad de camino no deja la app a medio actualizar.
    Devuelve (True, '') o (False, mensaje legible)."""
    staged = {}
    for rel in UPDATE_FILES:
        url = RAW_BASE + "/" + urllib.parse.quote(rel, safe="/")
        try:
            blob = _fetch(url)
        except Exception as e:  # noqa: BLE001
            return False, f"no pude descargar {rel} ({e})"
        if not blob:
            return False, f"el archivo {rel} llegó vacío"
        staged[rel] = blob
    for rel, blob in staged.items():
        dest = os.path.join(app_dir, *rel.split("/"))
        folder = os.path.dirname(dest)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(dest, "wb") as f:
            f.write(blob)
    return True, ""
