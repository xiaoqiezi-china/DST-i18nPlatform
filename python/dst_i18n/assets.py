"""Embedded wwwroot asset bundle."""

from __future__ import annotations

import os
import sys
from typing import Any


def _asset_root() -> str:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return os.path.join(str(meipass), "wwwroot")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "wwwroot")


CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}

DEFAULT_FAVICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<rect width="64" height="64" rx="14" fill="#0d1b2a"/>'
    '<path d="M18 40V24h6l8 10 8-10h6v16h-5V31l-9 11-9-11v9z" fill="#5cc8ff"/></svg>'
)


class Assets:
    def __init__(self) -> None:
        self.html: str = ""
        self.favicon: str = DEFAULT_FAVICON
        self.files: dict[str, tuple[bytes, str]] = {}
        self._load()

    def _load(self) -> None:
        root = _asset_root()
        if not os.path.isdir(root):
            self.html = (
                '<!doctype html><meta charset="utf-8"><title>DST MC I18n Tool</title>'
                "<h1>UI 资源缺失</h1><p>发布时 wwwroot 未打包进程序。</p>"
            )
            return
        for current, _sub, names in os.walk(root):
            for name in names:
                full = os.path.join(current, name)
                rel = os.path.relpath(full, root).replace("\\", "/")
                try:
                    with open(full, "rb") as handle:
                        data = handle.read()
                except OSError:
                    continue
                if rel.lower() == "index.html":
                    self.html = data.decode("utf-8", errors="replace")
                elif rel.lower() == "favicon.svg":
                    self.favicon = data.decode("utf-8", errors="replace")
                    self.files[rel] = (data, "image/svg+xml")
                else:
                    content_type = CONTENT_TYPES.get(os.path.splitext(rel)[1].lower(), "application/octet-stream")
                    self.files[rel] = (data, content_type)

    def lookup(self, path: str) -> tuple[bytes, str] | None:
        key = path.lstrip("/").replace("\\", "/")
        for name, value in self.files.items():
            if name.lower() == key.lower():
                return value
        return None
