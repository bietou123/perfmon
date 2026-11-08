# -*- coding: utf-8 -*-
"""离线冒烟测试：不创建 WebView 窗口，直接验证采集器与本地 HTTP 接口。"""
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app  # noqa: E402  只导入 Handler / collector，不调用 main()
from collector import Collector  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (" -> " + detail if detail else ""))
    if not ok:
        failures.append(name)


def check_collector():
    c = Collector()
    c.prepare()
    c._tick()
    c._tick()
    snap = c.payload()

    cur = snap.get("current") or {}
    check("collector payload", bool(cur))
    check("cpu fields", isinstance(cur.get("cpu"), dict)
          and "usage" in cur["cpu"] and "per_core" in cur["cpu"])
    check("memory fields", isinstance(cur.get("mem"), dict)
          and cur["mem"].get("total", 0) > 0)
    check("disk fields", isinstance(cur.get("disks"), dict))
    check("network fields", isinstance(cur.get("net"), dict))
    check("gpu fields", isinstance(cur.get("gpu"), dict))
    check("static info", isinstance(snap.get("static"), dict)
          and snap["static"].get("cpu_name"))
    check("history length", len(snap["history"]["cpu"]) >= 2,
          str(len(snap["history"]["cpu"])))
    check("cpu usage 0-100", 0 <= cur["cpu"]["usage"] <= 100,
          str(cur["cpu"]["usage"]))
    return c


def check_http(collector):
    # 用独立实例替换 app 模块里的采集器，避免起线程
    app.collector = collector
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"

    def get(path):
        try:
            with urllib.request.urlopen(base + path, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as exc:
            return exc.code, b""

    try:
        status, body = get("/api/stats")
        check("/api/stats 200", status == 200, str(status))
        data = json.loads(body.decode("utf-8"))
        check("stats parses", isinstance(data.get("current"), dict))

        for path, needle in (("/", b"<title>"),
                             ("/app.js", b"function"),
                             ("/style.css", b"{")):
            status, body = get(path)
            check(f"{path} 200", status == 200, str(status))
            check(f"{path} body", needle in body)

        status, _ = get("/nope.txt")
        check("/nope.txt 404", status == 404, str(status))
        status, _ = get("/../app.py")
        check("path traversal 404", status == 404, str(status))
    finally:
        server.shutdown()
        server.server_close()


def main():
    collector = check_collector()
    check_http(collector)
    print()
    if failures:
        print(f"{len(failures)} failed: {failures}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
