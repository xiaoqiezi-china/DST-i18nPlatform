"""World save scanning: region files, structures and level.dat."""

from __future__ import annotations

import os
import re
from typing import Any

from . import nbt, nbt_text, region
from .model import MapScanResult, TextCandidate, candidate_id
from .text import TextPart, extract_text

SKIP_DIRS = {"datapacks", "resourcepacks", ".git", "__pycache__"}


def scan_world(root: str, result: MapScanResult, command_parser: Any) -> None:
    _scan_level_dat(root, result, command_parser)
    _scan_regions(root, result, command_parser)
    _scan_nbt_files(root, result, command_parser)


def _scan_regions(root: str, result: MapScanResult, command_parser: Any) -> None:
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS]
        for name in files:
            if not name.lower().endswith(".mca"):
                continue
            full = os.path.join(current, name)
            rel = os.path.relpath(full, root).replace("\\", "/")
            try:
                region_file = region.RegionFile(full)
            except Exception as exc:  # noqa: BLE001
                result.warnings.append(rel + " 区域文件读取失败: " + str(exc))
                continue
            try:
                for chunk_x, chunk_z, chunk in region_file.iter_chunks():
                    base = rel + "#chunk[" + str(chunk_x) + "," + str(chunk_z) + "]"
                    hits: list[TextPart] = []
                    nbt_text.collect_nbt_text(chunk, base, hits, command_parser)
                    for part in hits:
                        _append_world_part(result, part, base, None)
            except Exception as exc:  # noqa: BLE001
                result.warnings.append(rel + " 区域文件扫描中断: " + str(exc))


def _scan_level_dat(root: str, result: MapScanResult, command_parser: Any) -> None:
    path = os.path.join(root, "level.dat")
    if not os.path.isfile(path):
        return
    try:
        root_tag = nbt.load_nbt(_read(path))
    except Exception as exc:  # noqa: BLE001
        result.warnings.append("level.dat 解析失败: " + str(exc))
        return

    data = root_tag.get("Data") if isinstance(root_tag, dict) else None
    if isinstance(data, dict):
        bossbars = data.get("CustomBossEvents")
        if isinstance(bossbars, dict):
            for key, value in bossbars.items():
                if isinstance(value, dict) and isinstance(value.get("Name"), str):
                    for part in extract_text(value["Name"], "level.dat#bossbar." + str(key)):
                        part.owner = "minecraft:bossbar"
                        _append_world_part(result, part, "level.dat#bossbar." + str(key), None, "bossbar")

    hits: list[TextPart] = []
    nbt_text.collect_nbt_text(root_tag, "level.dat", hits, command_parser)
    for part in hits:
        if "CustomBossEvents" in part.path:
            # Already emitted above with the bossbar-specific source.
            continue
        _append_world_part(result, part, "level.dat", None)


def _scan_nbt_files(root: str, result: MapScanResult, command_parser: Any) -> None:
    """Scan structures and save data files, including players and dimensions."""
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS]
        for name in files:
            lower = name.lower()
            if not (lower.endswith(".nbt") or lower.endswith(".dat")):
                continue
            if lower in ("level.dat", "level.dat_old"):
                continue
            full = os.path.join(current, name)
            rel = os.path.relpath(full, root).replace("\\", "/")
            try:
                root_tag = nbt.load_nbt(_read(full))
            except Exception as exc:  # noqa: BLE001
                result.warnings.append(rel + " NBT 解析失败: " + str(exc))
                continue
            source = "structure" if lower.endswith(".nbt") else None
            hits: list[TextPart] = []
            nbt_text.collect_nbt_text(root_tag, rel, hits, command_parser)
            for part in hits:
                _append_world_part(result, part, rel, None, source)


def _append_world_part(
    result: MapScanResult,
    part: TextPart,
    base_path: str,
    line: int | None,
    source: str | None = None,
) -> None:
    if part.kind == "command" or not part.text.strip():
        return
    actual = source or _classify(part.path, part.owner)
    result.add(
        TextCandidate(
            id=candidate_id(actual, base_path, line, part.path, part.text),
            text=part.text,
            kind=part.kind,
            source=actual,
            file=base_path.split("#", 1)[0],
            line=line,
            nbt_path=part.path,
            key=part.key,
            command=part.command,
            owner=part.owner,
        )
    )


def _classify(path: str, owner: str | None = None) -> str:
    lowered = path.lower()
    if lowered.endswith(".command") or ".command" in lowered:
        return "command_block"
    if owner and "text_display" in owner.lower():
        return "text_display"
    if "front_text" in lowered or "back_text" in lowered:
        return "sign"
    if re.search(r"\.text[1-4]($|\.)", lowered):
        return "sign"
    if "components." in lowered or "display.name" in lowered or "display.lore" in lowered:
        if "book_content" in lowered:
            return "book"
        return "item"
    if "pages[" in lowered:
        return "book"
    if "customname" in lowered:
        return "entity"
    return "nbt"


def _read(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()
