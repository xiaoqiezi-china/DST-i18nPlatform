"""Workspace session: scan lifecycle, progress snapshots and directory browsing."""

from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any

from . import scanner


class Session:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._info: dict[str, Any] | None = None
        self._active: dict[str, Any] | None = None
        self._active_event: threading.Event | None = None
        self._scans: dict[str, dict[str, Any]] = {}
        self._last_path = ""
        self._cleanup_stop = threading.Event()
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

    # ----- workspace snapshot -------------------------------------------
    def current(self) -> dict[str, Any]:
        with self._lock:
            info = self._info
            scanning = self._active is not None
            if info is None:
                return {
                    "path": "",
                    "exists": False,
                    "name": "",
                    "modsDir": None,
                    "kubejsDir": None,
                    "configDir": None,
                    "gameVersion": None,
                    "loader": None,
                    "warnings": [],
                    "candidates": [],
                    "mods": {"found": False, "root": None, "mods": [], "warnings": []},
                    "kubejs": {"found": False, "root": None, "namespaces": [], "files": [], "warnings": []},
                    "ftb": {"found": False, "root": None, "languages": [], "files": [], "warnings": []},
                    "scannedAt": 0,
                    "scanning": scanning,
                }
            snapshot = _deepcopy_json(info)
            snapshot["scanning"] = scanning
            return snapshot

    def current_path(self) -> str:
        with self._lock:
            return self._info["path"] if self._info else self._last_path

    def active_scan_id(self) -> str | None:
        with self._lock:
            return self._active["progress"]["scanId"] if self._active else None

    def refresh(self) -> dict[str, Any]:
        path = self.current_path()
        if not path:
            raise ValueError("尚未打开工作目录")
        return self.open(path)

    def current_info(self) -> dict[str, Any] | None:
        with self._lock:
            return _deepcopy_json(self._info) if self._info else None

    def get_or_open(self, path: str | None) -> dict[str, Any]:
        """Return the current workspace, or scan `path` synchronously if nothing is loaded."""
        with self._lock:
            has_info = self._info is not None
            active_path = self._active["path"] if self._active else None
        if has_info:
            return self.current()
        if not path:
            return self.current()

        normalized = _normalize(path)
        if active_path and os.path.normcase(active_path) == os.path.normcase(normalized):
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                with self._lock:
                    if self._active is None:
                        return self.current()
                time.sleep(0.05)
            return self.current()

        info = self._scan_now(normalized)
        return info

    def open(self, path: str) -> dict[str, Any]:
        normalized = _normalize(path)
        return self._scan_now(normalized)

    def _scan_now(self, path: str) -> dict[str, Any]:
        with self._lock:
            self._last_path = path
        reporter = scanner.Reporter({}, threading.RLock())
        info = scanner.scan_workspace(path, reporter, threading.Event())
        with self._lock:
            self._info = info
        return self.current()

    # ----- scanning ------------------------------------------------------
    def start_scan(self, path: str) -> dict[str, Any]:
        with self._lock:
            if self._active is not None:
                raise RuntimeError("已有扫描正在进行，请稍候或先取消当前扫描")
            normalized = _normalize(path)
            valid, candidates = scanner.validate_path(normalized)
            if not valid:
                error = "无效的工作目录：没有找到 mods、kubejs、config 或 .minecraft 目录"
                raise ScanRejected(error, candidates)

            scan_id = uuid.uuid4().hex[:8]
            started = time.monotonic()
            progress: dict[str, Any] = {
                "scanId": scan_id,
                "path": normalized,
                "name": os.path.basename(os.path.normpath(normalized)),
                "status": "running",
                "phase": "prepare",
                "phaseLabel": "正在准备…",
                "current": 0,
                "total": 0,
                "percent": 0,
                "currentItem": "",
                "modsFound": 0,
                "error": None,
                "warnings": [],
            }
            state = {
                "progress": progress,
                "lock": threading.RLock(),
                "cancel": threading.Event(),
                "started": started,
                "finished": 0.0,
            }
            self._scans[scan_id] = state
            self._active = state
            self._active_event = threading.Event()
            self._last_path = normalized

        thread = threading.Thread(
            target=self._run_scan,
            args=(state, normalized),
            name="workspace-scan",
            daemon=True,
        )
        thread.start()
        return self._progress_dto(state)

    def _run_scan(self, state: dict[str, Any], path: str) -> None:
        reporter = scanner.Reporter(state["progress"], state["lock"])
        try:
            info = scanner.scan_workspace(path, reporter, state["cancel"])
            with self._lock:
                self._info = info
            with state["lock"]:
                if state["cancel"].is_set():
                    state["progress"]["status"] = "cancelled"
                else:
                    state["progress"]["status"] = "done"
                state["progress"]["phase"] = "finish"
                state["progress"]["phaseLabel"] = "扫描完成"
                state["progress"]["percent"] = 100
                state["progress"]["warnings"] = list(info.get("warnings", []))
                state["finished"] = time.monotonic()
        except scanner.ScanCancelled:
            with state["lock"]:
                state["progress"]["status"] = "cancelled"
                state["finished"] = time.monotonic()
        except Exception as exc:  # noqa: BLE001
            with state["lock"]:
                state["progress"]["status"] = "error"
                state["progress"]["error"] = str(exc)
                state["finished"] = time.monotonic()
        finally:
            with self._lock:
                if self._active is state:
                    self._active = None
                    if self._active_event is not None:
                        self._active_event.set()

    def cancel_scan(self, scan_id: str) -> bool:
        with self._lock:
            state = self._scans.get(scan_id)
            if state is None:
                return False
            state["cancel"].set()
            with state["lock"]:
                if state["progress"]["status"] == "running":
                    state["progress"]["status"] = "cancelled"
            return True

    def get_scan(self, scan_id: str) -> dict[str, Any] | None:
        with self._lock:
            state = self._scans.get(scan_id)
            if state is None:
                return None
            return self._progress_dto(state)

    def dispose_scan(self, scan_id: str) -> bool:
        with self._lock:
            state = self._scans.get(scan_id)
            if state is None:
                return False
            if state is self._active:
                state["cancel"].set()
                self._active = None
            del self._scans[scan_id]
            return True

    def _progress_dto(self, state: dict[str, Any]) -> dict[str, Any]:
        with state["lock"]:
            progress = dict(state["progress"])
        elapsed = (state["finished"] or time.monotonic()) - state["started"]
        progress["elapsedMs"] = int(elapsed * 1000)
        return progress

    def _cleanup_loop(self) -> None:
        while not self._cleanup_stop.wait(300):
            cutoff = time.monotonic() - 600
            with self._lock:
                stale = [
                    scan_id
                    for scan_id, state in self._scans.items()
                    if state["finished"] and state["finished"] < cutoff and state is not self._active
                ]
                for scan_id in stale:
                    self._scans.pop(scan_id, None)

    # ----- browsing ------------------------------------------------------
    def browse(self, path: str | None) -> dict[str, Any]:
        target = path or self.current_path() or os.getcwd()
        if not os.path.isdir(target):
            raise FileNotFoundError("目录不存在: " + target)
        target = os.path.abspath(target)
        dirs: list[dict[str, Any]] = []
        try:
            names = sorted(os.listdir(target), key=str.lower)
        except OSError as exc:
            raise FileNotFoundError("无法读取目录: " + str(exc)) from exc
        for name in names:
            full = os.path.join(target, name)
            if not os.path.isdir(full) or name.startswith("."):
                continue
            dirs.append(
                {
                    "name": name,
                    "path": full,
                    "hasMods": os.path.isdir(os.path.join(full, "mods")),
                    "hasKubejs": os.path.isdir(os.path.join(full, "kubejs")),
                    "hasConfig": os.path.isdir(os.path.join(full, "config")),
                    "hasMinecraft": os.path.isdir(os.path.join(full, ".minecraft")),
                }
            )
        return {"path": target, "parent": os.path.dirname(target), "dirs": dirs}


class ScanRejected(Exception):
    def __init__(self, message: str, candidates: list[str]):
        super().__init__(message)
        self.candidates = candidates


def _normalize(path: str | None) -> str:
    text = (path or "").strip()
    if not text:
        return ""
    return os.path.abspath(os.path.expanduser(text))


def _deepcopy_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _deepcopy_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deepcopy_json(v) for v in value]
    return value
