"""Background map scanning and safe file-location helpers for the UI."""

from __future__ import annotations

import os
import subprocess
import threading
import time
import uuid
import zipfile
from typing import Any

from .mapscan import scan_map, scan_resource_pack
from .mapscan.model import MapScanResult
from .mapscan.packs import LANG_PATH, logical_rel

SOURCE_LABELS = {
    "lang": "语言文件",
    "resourcepack": "材质包",
    "function": "函数",
    "structure": "结构",
    "bossbar": "Boss 栏",
    "sign": "告示牌",
    "command_block": "命令方块",
    "entity": "实体",
    "item": "物品",
    "book": "成书",
    "nbt": "NBT",
    "text_display": "文本展示实体",
    "level": "level.dat",
    "player": "玩家数据",
}


def _source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source or "未知")


def _file_name(file: str) -> str:
    tail = (file or "").replace("\\", "/").split("::")[-1]
    return tail.rsplit("/", 1)[-1] or tail


def _display_path(file: str, line: int | None, nbt_path: str) -> str:
    text = file or ""
    if nbt_path:
        if nbt_path.startswith(text + "#") or nbt_path.startswith(text + " | "):
            return nbt_path
        if text and nbt_path not in text:
            return text + " | " + nbt_path
        return nbt_path
    if line:
        text += ":" + str(line)
    return text


def _safe_join(root: str, relative: str) -> str:
    path = os.path.normpath(os.path.join(root, relative.replace("/", os.sep)))
    root_norm = os.path.normpath(root)
    try:
        if os.path.commonpath([os.path.abspath(path), os.path.abspath(root_norm)]) != os.path.abspath(root_norm):
            return root_norm
    except ValueError:
        return root_norm
    return path


def resolve_open_path(root: str, file: str) -> str:
    """Resolve a candidate's logical path to a real file/folder on disk.

    Candidates may come from a folder data pack or from a zip member.  For zip
    members we deliberately return the archive path: that is what Explorer can
    select.  For folder packs we return the member file when it exists.  When
    the scan target itself is a resource-pack zip, every member resolves to that
    archive.
    """
    root_norm = os.path.normpath(root)
    if os.path.isfile(root_norm):
        return root_norm

    logical = str(file or "").replace("\\", "/")
    if "::" in logical:
        container, member = logical.split("::", 1)
        container_path = _safe_join(root, container)
        if not os.path.exists(container_path) and os.path.basename(root_norm).lower() == container.lower():
            container_path = root_norm
        if container_path.lower().endswith(".zip"):
            return container_path
        member_path = _safe_join(container_path, member)
        return member_path if os.path.exists(member_path) else container_path
    path = _safe_join(root, logical)
    return path


def _format_candidate(root: str, candidate: dict[str, Any]) -> dict[str, Any]:
    item = dict(candidate)
    file = str(item.get("file") or "")
    line = item.get("line")
    nbt_path = str(item.get("nbtPath") or "")
    item["fileName"] = _file_name(file)
    item["displayPath"] = _display_path(file, line if isinstance(line, int) else None, nbt_path)
    item["sourceLabel"] = _source_label(str(item.get("source") or ""))
    item["keyLabel"] = item.get("key") or item.get("kind") or "text"
    item["openPath"] = resolve_open_path(root, file)
    item["openKind"] = "file" if os.path.isfile(item["openPath"]) else ("dir" if os.path.isdir(item["openPath"]) else "missing")
    return item


def _format_result(result: MapScanResult) -> dict[str, Any]:
    data = result.to_dict()
    entries = [_format_candidate(result.root, c) for c in data.get("candidates") or []]
    data["entries"] = entries
    data["scanId"] = ""
    data["existing"] = {
        str(c.get("id")): str(c.get("existing"))
        for c in entries
        if c.get("existing") and str(c.get("existing")).strip()
    }
    return data


class MapScanService:
    """Run map scans off the HTTP thread and expose progress snapshots."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._scans: dict[str, dict[str, Any]] = {}
        self._last_cleanup = time.time()

    def start(
        self,
        path: str,
        include_world: bool = True,
        include_packs: bool = True,
        kind: str = "map",
    ) -> dict[str, Any]:
        scan_kind = str(kind or "map").strip().lower()
        if scan_kind not in ("map", "resourcepack"):
            raise ValueError("不支持的扫描类型：" + scan_kind)
        root = os.path.abspath(str(path or "").strip().strip('"'))
        if scan_kind == "resourcepack":
            if not os.path.exists(root):
                raise ValueError("材质包路径不存在：" + str(path))
            if not _looks_like_resourcepack(root):
                raise ValueError("该路径不是材质包：需要 assets/<命名空间>/lang 目录或 .zip")
            include_world = False
        else:
            if not os.path.isdir(root):
                raise ValueError("目录不存在：" + str(path))
            if not _looks_like_map(root):
                raise ValueError("该目录不是地图或数据包目录：缺少 level.dat / region / datapacks 或 pack.mcmeta")

        display_name = os.path.basename(root.rstrip("\\/")) or root
        if scan_kind == "resourcepack" and os.path.isfile(root):
            display_name = os.path.splitext(display_name)[0]

        with self._lock:
            self._cleanup()
            for state in self._scans.values():
                if state["progress"].get("status") in ("running", "queued"):
                    raise ValueError("已有扫描正在进行，请先等待完成或取消")
            scan_id = uuid.uuid4().hex[:12]
            progress: dict[str, Any] = {
                "scanId": scan_id,
                "path": root,
                "name": display_name,
                "kind": scan_kind,
                "status": "queued",
                "phase": "prepare",
                "phaseLabel": "准备扫描…",
                "percent": 0,
                "current": 0,
                "total": 0,
                "candidateCount": 0,
                "warnings": [],
                "error": None,
                "elapsedMs": 0,
            }
            state = {
                "progress": progress,
                "root": root,
                "result": None,
                "cancel": threading.Event(),
                "started": time.monotonic(),
                "includeWorld": bool(include_world),
                "includePacks": bool(include_packs),
                "kind": scan_kind,
            }
            self._scans[scan_id] = state

        thread = threading.Thread(
            target=self._run,
            args=(state,),
            name="mapscan-" + scan_id,
            daemon=True,
        )
        thread.start()
        return dict(progress)

    def get(self, scan_id: str) -> dict[str, Any] | None:
        with self._lock:
            state = self._scans.get(scan_id)
            if state is None:
                return None
            return dict(state["progress"])

    def result(self, scan_id: str) -> dict[str, Any] | None:
        with self._lock:
            state = self._scans.get(scan_id)
            if state is None or state["result"] is None:
                return None
            result = dict(state["result"])
        result["scanId"] = scan_id
        return result

    def cancel(self, scan_id: str) -> bool:
        with self._lock:
            state = self._scans.get(scan_id)
            if state is None:
                return False
            state["cancel"].set()
            if state["progress"].get("status") in ("queued", "running"):
                state["progress"]["status"] = "cancelling"
                state["progress"]["phaseLabel"] = "正在取消…"
            return True

    def reveal(self, scan_id: str, file: str) -> dict[str, Any]:
        with self._lock:
            state = self._scans.get(scan_id)
            if state is None:
                raise ValueError("扫描任务不存在或已过期")
            root = state["root"]
        target = resolve_open_path(root, file)
        if not os.path.exists(target):
            parent = os.path.dirname(target)
            if os.path.isdir(parent):
                target = parent
            else:
                raise ValueError("文件不存在：" + file)
        try:
            if os.path.isfile(target):
                subprocess.Popen(["explorer.exe", "/select," + os.path.normpath(target)])
            else:
                subprocess.Popen(["explorer.exe", os.path.normpath(target)])
        except Exception as exc:  # noqa: BLE001
            raise ValueError("无法打开资源管理器：" + str(exc)) from exc
        return {"ok": True, "path": target}

    # ----- worker -------------------------------------------------------
    def _run(self, state: dict[str, Any]) -> None:
        progress = state["progress"]
        scan_id = progress["scanId"]
        root = state["root"]

        def on_phase(phase: str, percent: float, label: str) -> None:
            if state["cancel"].is_set():
                return
            with self._lock:
                if progress["status"] == "cancelled":
                    return
                progress["status"] = "running"
                progress["phase"] = phase
                progress["phaseLabel"] = label
                progress["percent"] = max(0, min(99, int(percent)))
                progress["elapsedMs"] = int((time.monotonic() - state["started"]) * 1000)

        try:
            with self._lock:
                progress["status"] = "running"
                progress["phaseLabel"] = "准备扫描…"
                progress["elapsedMs"] = 0

            result = (
                scan_resource_pack(root, progress=on_phase)
                if state.get("kind") == "resourcepack"
                else scan_map(
                    root,
                    include_world=state["includeWorld"],
                    include_packs=state["includePacks"],
                    progress=on_phase,
                )
            )
            if state["cancel"].is_set():
                with self._lock:
                    progress["status"] = "cancelled"
                    progress["phase"] = "cancelled"
                    progress["phaseLabel"] = "已取消"
                    progress["elapsedMs"] = int((time.monotonic() - state["started"]) * 1000)
                return

            formatted = _format_result(result)
            formatted["scanId"] = scan_id
            formatted["name"] = progress.get("name") or os.path.basename(root)
            formatted["root"] = root
            formatted["kind"] = state.get("kind") or "map"
            with self._lock:
                state["result"] = formatted
                progress["status"] = "done"
                progress["phase"] = "done"
                progress["phaseLabel"] = "扫描完成"
                progress["percent"] = 100
                progress["candidateCount"] = len(formatted.get("entries") or [])
                progress["total"] = progress["candidateCount"]
                progress["warnings"] = list(formatted.get("warnings") or [])
                progress["elapsedMs"] = int((time.monotonic() - state["started"]) * 1000)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                progress["status"] = "error"
                progress["phase"] = "error"
                progress["phaseLabel"] = "扫描失败"
                progress["error"] = str(exc)
                progress["elapsedMs"] = int((time.monotonic() - state["started"]) * 1000)

    def _cleanup(self) -> None:
        now = time.time()
        if now - self._last_cleanup < 30:
            return
        self._last_cleanup = now
        stale = []
        for scan_id, state in self._scans.items():
            started = state["started"]
            if state["progress"].get("status") in ("done", "cancelled", "error") and (time.monotonic() - started) > 600:
                stale.append(scan_id)
        for scan_id in stale:
            self._scans.pop(scan_id, None)


def _looks_like_map(root: str) -> bool:
    names = {name.lower() for name in os.listdir(root)} if os.path.isdir(root) else set()
    if "level.dat" in names or "region" in names or "dimensions" in names or "datapacks" in names:
        return True
    return os.path.isfile(os.path.join(root, "pack.mcmeta"))


def _looks_like_resourcepack(path: str) -> bool:
    if os.path.isdir(path):
        if os.path.isdir(os.path.join(path, "assets")):
            return True
        for current, _dirs, files in os.walk(path):
            for name in files:
                if not name.lower().endswith(".json"):
                    continue
                rel = os.path.relpath(os.path.join(current, name), path).replace("\\", "/")
                if LANG_PATH.match(logical_rel(rel)):
                    return True
        return False
    if os.path.isfile(path) and path.lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(path) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    logical = logical_rel(info.filename.replace("\\", "/"))
                    if LANG_PATH.match(logical):
                        return True
        except (OSError, zipfile.BadZipFile):
            return False
    return False
