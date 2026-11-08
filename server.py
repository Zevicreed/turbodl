"""
Puente local entre la extensión del navegador y TurboDL.

La extensión hace POST http://127.0.0.1:8765/add con JSON:
  {"url": "...", "referer": "https://sitio/pagina", "title": "..."}
y el trabajo cae en una cola que la ventana principal recoge.
Solo escucha en 127.0.0.1 (nadie de la red puede usarlo).
"""
import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8765


def make_server(job_queue: "queue.Queue"):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silencio
            pass

        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self):
            if self.path == "/ping":
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true, "app": "TurboDL"}')
            else:
                self.send_response(404)
                self._cors()
                self.end_headers()

        def do_POST(self):
            if self.path != "/add":
                self.send_response(404)
                self._cors()
                self.end_headers()
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(n) or b"{}")
                url = (data.get("url") or "").strip()
                if not url.startswith(("http://", "https://")):
                    raise ValueError("URL inválida")
                job_queue.put({
                    "url": url,
                    "referer": data.get("referer") or "",
                    "title": data.get("title") or "",
                })
                body = b'{"ok": true}'
                self.send_response(200)
            except Exception as e:  # noqa: BLE001
                body = json.dumps({"ok": False, "error": str(e)}).encode()
                self.send_response(400)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer(("127.0.0.1", PORT), Handler)


def serve_in_thread(job_queue: "queue.Queue"):
    server = make_server(job_queue)
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    return server
