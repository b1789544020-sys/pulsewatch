#!/usr/bin/env python3
"""
PulseWatch Web 服务
===================
用标准库 http.server 提供：
  GET /              仪表盘页面
  GET /api/metrics   最新一帧采集数据（JSON）
  GET /static/*      静态资源

零第三方依赖。监听 0.0.0.0:8765，
在 WSL2 里运行后，Windows 浏览器直接访问 http://localhost:8765
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from collector import MetricsCollector

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
HOST, PORT = "0.0.0.0", 8765

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".ico": "image/x-icon",
}

collector = MetricsCollector(interval=1.0)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body: bytes, content_type="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]

        if path == "/api/metrics":
            if collector.snapshot is None:
                self._send(503, b'{"error": "warming up"}')
            else:
                body = json.dumps(collector.snapshot).encode("utf-8")
                self._send(200, body)
            return

        if path == "/":
            path = "/index.html"

        # 安全地映射到 static 目录，禁止路径穿越
        rel = os.path.normpath(path.lstrip("/"))
        file_path = os.path.join(STATIC_DIR, rel)
        if not file_path.startswith(STATIC_DIR) or not os.path.isfile(file_path):
            self._send(404, b"<h1>404</h1>", "text/html; charset=utf-8")
            return

        with open(file_path, "rb") as f:
            body = f.read()
        ext = os.path.splitext(file_path)[1].lower()
        self._send(200, body, CONTENT_TYPES.get(ext, "application/octet-stream"))

    def log_message(self, fmt, *args):
        # 界面每秒轮询，静默普通访问日志，只打印错误
        pass


def main():
    collector.start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("=" * 56)
    print("  PulseWatch 已启动")
    print(f"  本机访问:   http://localhost:{PORT}")
    print(f"  局域网访问: http://<本机IP>:{PORT}")
    print("  按 Ctrl+C 停止")
    print("=" * 56)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭...")
        server.shutdown()


if __name__ == "__main__":
    main()
