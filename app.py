# -*- coding: utf-8 -*-
"""性能监视器：本地 HTTP 服务 + WebView2 渲染。"""
import json
import os
import subprocess
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import webview

from collector import Collector

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}

collector = Collector()
_window = None


class Api:
    """暴露给前端 JS 的窗口控制。"""

    def minimize(self):
        _window.minimize()

    def toggle_max(self):
        if _window.state == 1:  # maximized
            _window.restore()
            return False
        _window.maximize()
        return True

    def close(self):
        _window.destroy()

    def set_on_top(self, flag):
        _window.on_top = bool(flag)
        return bool(flag)

    def run(self, cmd):
        """运行用户输入的命令，等价于任务管理器的「运行新任务」。"""
        cmd = (cmd or "").strip()
        if not cmd:
            return False
        try:
            subprocess.Popen(cmd, shell=True)
            return True
        except Exception:
            return False


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/api/stats":
            body = json.dumps(collector.payload(), ensure_ascii=False)
            self._send(body.encode("utf-8"), "application/json; charset=utf-8")
            return

        if path == "/":
            path = "/index.html"

        target = os.path.normpath(os.path.join(WEB, path.lstrip("/")))
        if not target.startswith(WEB) or not os.path.isfile(target):
            self.send_error(404)
            return

        ext = os.path.splitext(target)[1].lower()
        with open(target, "rb") as fh:
            data = fh.read()
        self._send(data, MIME.get(ext, "application/octet-stream"))


def main():
    global _window

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    collector.start()
    collector._ready.wait(timeout=20)

    _window = webview.create_window(
        "任务管理器",
        f"http://127.0.0.1:{port}/",
        width=1180, height=760,
        min_size=(980, 620),
        frameless=True,
        easy_drag=False,
        background_color="#f3f3f3",
        js_api=Api(),
    )
    webview.start()
    collector.stop()


if __name__ == "__main__":
    main()
