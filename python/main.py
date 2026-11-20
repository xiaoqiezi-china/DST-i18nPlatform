"""Entry point for the Python build of DST MC I18n Tool."""

from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser

from dst_i18n import APP_NAME, APP_VERSION
from dst_i18n.api import Api
from dst_i18n.server import serve
from dst_i18n.session import Session
from dst_i18n.settings import SettingsStore


def parse_args(argv: list[str]) -> dict[str, object]:
    options: dict[str, object] = {"path": None, "port": None, "no_browser": False, "show_help": False}
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("-h", "--help", "/?"):
            options["show_help"] = True
        elif arg == "--no-browser":
            options["no_browser"] = True
        elif arg == "--port":
            if index + 1 < len(argv):
                index += 1
                _set_port(options, argv[index])
        elif arg == "--path":
            if index + 1 < len(argv):
                index += 1
                options["path"] = argv[index]
        elif arg.lower().startswith("--port="):
            _set_port(options, arg[7:])
        elif arg.lower().startswith("--path="):
            options["path"] = arg[7:]
        index += 1
    return options


def _set_port(options: dict[str, object], value: str) -> None:
    try:
        port = int(value)
    except ValueError:
        return
    if 0 < port <= 65535:
        options["port"] = port


HELP_TEXT = """DST MC I18n Tool v1.1.4 — Minecraft 模组本地化批处理工具

用法:
  DST-MC-I18n-Tool.exe [--path <整合包目录>] [--port <端口>] [--no-browser]

参数:
  --path <dir>    工作目录（.minecraft / 版本隔离目录 / 整合包根目录）。
                  也可以启动后在网页里点“打开文件夹”选择。
  --port <port>   监听端口。省略时自动使用一个随机空闲端口。
  --no-browser    启动后不自动打开浏览器。
  -h, --help      显示本帮助。

网页界面地址: http://127.0.0.1:<port>/ ，仅本机可访问。
"""


def _configure_console() -> None:
    """避免在非中文代码页的 Windows 控制台因中文输出直接崩溃。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    if args["show_help"]:
        print(HELP_TEXT)
        return 0

    settings = SettingsStore()
    session = Session()
    api = Api(session, settings)

    requested_port = int(args["port"] or 0)
    try:
        server, actual_port = serve(api, requested_port)
    except OSError as exc:
        print("无法监听端口 " + str(requested_port) + "：" + str(exc), file=sys.stderr)
        return 1

    url = "http://127.0.0.1:" + str(actual_port) + "/"
    print(APP_NAME + " v" + APP_VERSION)
    print("网页界面: " + url)
    print("仅监听本机 127.0.0.1；按 Ctrl+C 退出。")

    startup_path = args["path"] or str(settings.current().get("LastWorkspace") or "").strip()
    if startup_path:
        threading.Thread(
            target=_startup_scan,
            args=(session, str(startup_path)),
            name="startup-scan",
            daemon=True,
        ).start()

    if not args["no_browser"]:
        threading.Thread(target=_open_browser, args=(url,), daemon=True).start()

    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


def _startup_scan(session: Session, path: str) -> None:
    try:
        session.start_scan(path)
    except Exception:  # noqa: BLE001
        pass


def _open_browser(url: str) -> None:
    time.sleep(0.4)
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    sys.exit(main())
