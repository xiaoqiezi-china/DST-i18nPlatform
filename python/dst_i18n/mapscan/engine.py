"""Map localization scan orchestrator."""

from __future__ import annotations

import os
from typing import Any, Callable

from .commands import CommandParser
from .model import MapScanResult
from .packs import close_packs, discover_pack, discover_packs, scan_packs
from .packs import scan_resource_pack as scan_pack_entries
from .world import scan_world

ProgressCallback = Callable[[str, float, str], None]


def scan_map(
    root: str,
    include_world: bool = True,
    include_packs: bool = True,
    progress: ProgressCallback | None = None,
) -> MapScanResult:
    """Scan a Minecraft world save / map folder.

    The returned candidates are conservative: only literal text components,
    language entries and obvious NBT text fields are included.
    """
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise NotADirectoryError("地图目录不存在: " + root)
    if not os.access(root, os.R_OK):
        raise PermissionError("没有权限读取地图目录: " + root)

    def report(phase: str, percent: float, label: str) -> None:
        if progress is not None:
            progress(phase, percent, label)

    result = MapScanResult(root=root)
    parser = CommandParser()
    report("prepare", 2, "正在发现数据包与资源包…")
    packs = discover_packs(root) if include_packs else []
    try:
        if include_packs:
            report("packs", 10, "正在扫描语言文件、函数与结构…")
            scan_packs(packs, result, parser)
        if include_world:
            report("world", 55, "正在扫描存档区域与 NBT…")
            scan_world(root, result, parser)
        report("finalize", 92, "正在整理候选文本…")
    finally:
        close_packs(packs)

    result.finalize()
    result.candidates.sort(key=lambda c: (c.source, c.file, c.line or 0, c.nbt_path, c.text))
    report("done", 100, "扫描完成")
    return result


def scan_resource_pack(path: str, progress: ProgressCallback | None = None) -> MapScanResult:
    """Scan one resource pack folder or zip using the standard MC layout.

    Only ``assets/<namespace>/lang/<locale>.json`` is read.  The source locale
    is chosen per namespace (``en_us`` first) and any existing ``zh_cn`` values
    are exposed as existing translations.
    """
    root = os.path.abspath(path)
    if not os.path.exists(root):
        raise FileNotFoundError("材质包路径不存在: " + root)
    if not os.access(root, os.R_OK):
        raise PermissionError("没有权限读取材质包: " + root)

    def report(phase: str, percent: float, label: str) -> None:
        if progress is not None:
            progress(phase, percent, label)

    report("prepare", 2, "正在打开材质包…")
    pack = discover_pack(root)
    result = MapScanResult(root=root, kind="resourcepack")
    try:
        report("packs", 18, "正在读取 assets/<命名空间>/lang 语言文件…")
        scan_pack_entries(pack, result)
        report("finalize", 90, "正在整理语言键值…")
        if not result.language_files:
            result.warnings.append("材质包中没有找到 assets/<命名空间>/lang/<语言>.json 语言文件")
    finally:
        pack.close()

    result.finalize()
    result.candidates.sort(key=lambda c: (c.source, c.file, c.line or 0, c.nbt_path, c.text))
    report("done", 100, "扫描完成")
    return result


def summarize(result: MapScanResult) -> dict[str, Any]:
    return {
        "root": result.root,
        "stats": dict(result.stats),
        "languageFiles": len(result.language_files),
        "warnings": len(result.warnings),
    }
