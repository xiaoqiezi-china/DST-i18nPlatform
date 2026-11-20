"""Loopback HTTP server (stdlib, threaded, HTTP/1.1 keep-alive)."""

from __future__ import annotations

import socketserver
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "DST-MC-I18n-Tool"
    sys_version = ""

    api: Any = None

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle("HEAD")

    def _handle(self, method: str) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        length = 0
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length > 0 else b""
        try:
            response = self.api.handle(method, parsed.path, parsed.query, body)
        except Exception as exc:  # noqa: BLE001
            response = _fallback_error(exc)
        self._send(response)

    def _send(self, response: Any) -> None:
        body = response.body or b""
        self.send_response(response.status)
        if response.content_type:
            self.send_header("Content-Type", response.content_type)
        for key, value in (response.headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        pass


def _fallback_error(exc: Exception) -> Any:
    from .api import ApiResponse

    return ApiResponse(500, ('{"error":' + _quote(str(exc)) + "}").encode("utf-8"), "application/json; charset=utf-8")


def _quote(text: str) -> str:
    import json

    return json.dumps(text, ensure_ascii=False)


def serve(api: Any, port: int) -> tuple[_Server, int]:
    server = _Server(("127.0.0.1", port), _Handler)
    _Handler.api = api
    actual = server.server_address[1]
    return server, actual
