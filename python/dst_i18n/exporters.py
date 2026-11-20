"""Deterministic zip/resource-pack builders (pack.mcmeta, icon, overlays)."""

from __future__ import annotations

import io
import math
import re
import struct
import zlib
import zipfile
from typing import Any, Iterable

from . import jsonutil

DEFAULT_PACK_FORMAT = 15
UNKNOWN_NAMESPACE = "unknown"
DEFAULT_LANGUAGE = "zh_cn"
MAX_FILE_NAME_LENGTH = 100
MAX_EXTENSION_LENGTH = 10
README_ENTRY = "README.txt"
ICON_SIZE = 64

FIXED_TIMESTAMP = (2020, 7, 1, 12, 0, 0)

ILLEGAL_FILE_NAME_CHARS = set('<>:"/\\|?*')

RESERVED_FILE_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}

PACK_FORMAT_BY_VERSION = {
    "1.16.5": 6,
    "1.17": 7,
    "1.18": 8,
    "1.19": 9,
    "1.19.1": 9,
    "1.19.2": 9,
    "1.19.3": 12,
    "1.19.4": 13,
    "1.20": 15,
    "1.20.1": 15,
    "1.20.2": 18,
    "1.20.3": 22,
    "1.20.4": 22,
    "1.20.5": 32,
    "1.20.6": 32,
    "1.21": 34,
    "1.21.1": 34,
    "1.21.2": 42,
    "1.21.3": 42,
    "1.21.4": 46,
    "1.21.5": 55,
}

PACK_FORMAT_BY_MINOR = {"1.17": 7, "1.18": 8}

VERSION_RE = re.compile(r"(?<!\d)(?P<major>\d{1,2})\.(?P<minor>\d{1,2})(?:\.(?P<patch>\d{1,2}))?(?!\d)")
WHITESPACE_RE = re.compile(r"\s+")
UNDERSCORE_RUN_RE = re.compile(r"_{2,}")

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def build_lang_json(entries: Iterable[dict[str, Any]], indented: bool = True) -> bytes:
    root: dict[str, str] = {}
    for entry in entries:
        if entry is None:
            continue
        key = entry.get("key")
        if key is None:
            continue
        root[str(key)] = str(entry.get("value") or "")
    return jsonutil.dumps(root, indented=indented).encode("utf-8")


def resolve_pack_format(mc_version: str | None, explicit_format: int | None) -> int:
    if explicit_format and explicit_format > 0:
        return int(explicit_format)
    version = normalize_version(mc_version)
    if not version:
        return DEFAULT_PACK_FORMAT
    if version in PACK_FORMAT_BY_VERSION:
        return PACK_FORMAT_BY_VERSION[version]
    dot = version.find(".")
    if dot > 0:
        minor = version[:dot]
        rest = version[dot + 1:]
        dot2 = rest.find(".")
        if dot2 > 0:
            minor += "." + rest[:dot2]
        if minor in PACK_FORMAT_BY_MINOR:
            return PACK_FORMAT_BY_MINOR[minor]
    return DEFAULT_PACK_FORMAT


def normalize_version(mc_version: str | None) -> str:
    if not mc_version or not mc_version.strip():
        return ""
    match = VERSION_RE.search(mc_version.strip())
    if not match:
        return ""
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    if match.group("patch"):
        return f"{major}.{minor}.{int(match.group('patch'))}"
    return f"{major}.{minor}"


def build_resource_pack(
    namespace_name: str,
    entries: Iterable[dict[str, Any]],
    options: dict[str, Any],
) -> bytes:
    namespace = sanitize_namespace(namespace_name)
    lang = sanitize_language(options.get("targetLanguage"))
    pack_format = resolve_pack_format(options.get("mcVersion"), options.get("packFormat"))
    description = resolve_description(options)

    meta = {"pack": {"pack_format": pack_format, "description": description}}
    meta_bytes = jsonutil.dumps(meta, indented=True).encode("utf-8")
    lang_bytes = build_lang_json(entries)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        _add_entry(archive, "pack.mcmeta", meta_bytes)
        _add_entry(archive, "pack.png", build_pack_icon())
        _add_entry(archive, "assets/" + namespace + "/lang/" + lang + ".json", lang_bytes)
    return buffer.getvalue()


def build_overlay_zip(files: Iterable[dict[str, Any]], options: dict[str, Any]) -> bytes:
    items = list(files or [])
    if not items:
        raise ValueError("覆盖包至少需要一个文件。")

    buffer = io.BytesIO()
    seen = {README_ENTRY.lower()}
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for item in items:
            if item is None:
                continue
            path = normalize_zip_path(item.get("zipPath"))
            if not path:
                continue
            if path.lower() in seen:
                continue
            seen.add(path.lower())
            _add_entry(archive, path, str(item.get("content") or "").encode("utf-8"))
        _add_entry(archive, README_ENTRY, build_overlay_readme(options))
    return buffer.getvalue()


def _add_entry(archive: zipfile.ZipFile, path: str, content: bytes) -> None:
    info = zipfile.ZipInfo(filename=path, date_time=FIXED_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0
    archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)


def resolve_description(options: dict[str, Any]) -> str:
    description = str(options.get("description") or "")
    if description.strip():
        return description.strip()
    name = str(options.get("packName") or "").strip() or "汉化资源包"
    version = str(options.get("packVersion") or "").strip()
    return name if not version else name + " " + version


def normalize_zip_path(path: str | None) -> str:
    if not path or not str(path).strip():
        return ""
    segments: list[str] = []
    for raw in str(path).replace("\\", "/").strip().split("/"):
        segment = raw.strip()
        if not segment or segment == ".":
            continue
        if segment == "..":
            if segments:
                segments.pop()
            continue
        segments.append(segment)
    return "/".join(segments)


def build_overlay_readme(options: dict[str, Any]) -> bytes:
    name = str(options.get("packName") or "").strip() or "汉化覆盖包"
    version = str(options.get("packVersion") or "").strip()
    version_text = (" " + version) if version else ""
    lang = sanitize_language(options.get("targetLanguage"))
    description = str(options.get("description") or "").strip()

    lines = [name + version_text + "（目标语言：" + lang + "）"]
    if description:
        lines.append(description)
    lines.append("")
    lines.append("使用说明：")
    lines.append("1. 关闭游戏。")
    lines.append("2. 将本压缩包内的所有文件解压到 .minecraft 目录（或整合包根目录），目录结构与游戏内保持一致。")
    lines.append("3. 提示文件已存在时选择“覆盖”。")
    lines.append("4. 重新启动游戏即可看到中文文本。")
    lines.append("")
    lines.append("建议在覆盖前备份原文件；如需还原，请恢复备份。")
    lines.append("")
    lines.append("由 DST MC I18n Tool 生成。")
    return ("\n".join(lines) + "\n").encode("utf-8")


def sanitize_namespace(raw: str | None) -> str:
    if not raw or not raw.strip():
        return UNKNOWN_NAMESPACE
    chars: list[str] = []
    for ch in raw.strip().lower():
        allowed = ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch in "_.-"
        chars.append(ch if allowed else "_")
    result = UNDERSCORE_RUN_RE.sub("_", "".join(chars)).strip("_")
    return result or UNKNOWN_NAMESPACE


def sanitize_language(raw: str | None) -> str:
    if not raw or not raw.strip():
        return DEFAULT_LANGUAGE
    chars: list[str] = []
    for ch in raw.strip().lower():
        allowed = ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch in "_-"
        chars.append(ch if allowed else "_")
    result = UNDERSCORE_RUN_RE.sub("_", "".join(chars)).strip("_")
    return result or DEFAULT_LANGUAGE


def safe_file_name(raw: str | None, fallback: str = "download") -> str:
    fallback_name = clean_file_name(fallback) or "download"
    if not raw or not raw.strip():
        return fallback_name
    cleaned = clean_file_name(raw)
    return cleaned or fallback_name


def clean_file_name(raw: str | None) -> str:
    if not raw:
        return ""
    chars: list[str] = []
    for ch in raw:
        code = ord(ch)
        if code < 0x20 or code == 0x7F:
            continue
        if ch in ILLEGAL_FILE_NAME_CHARS:
            continue
        chars.append(ch)
    text = WHITESPACE_RE.sub(" ", "".join(chars)).strip(" .")
    if len(text) > MAX_FILE_NAME_LENGTH:
        dot = text.rfind(".")
        extension = text[dot:] if dot > 0 and len(text) - dot <= MAX_EXTENSION_LENGTH else ""
        if extension:
            stem = _truncate(text[:dot], MAX_FILE_NAME_LENGTH - len(extension))
            text = _truncate(text, MAX_FILE_NAME_LENGTH) if not stem else stem + extension
        else:
            text = _truncate(text, MAX_FILE_NAME_LENGTH)
    if not text:
        return ""
    dot = text.find(".")
    stem = text[:dot] if dot >= 0 else text
    if stem.upper() in RESERVED_FILE_NAMES:
        text = "_" + text
    return text


def _truncate(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value.rstrip(" .")
    cut = max(1, maximum)
    return value[:cut].rstrip(" .")


# ----- PNG icon -----------------------------------------------------------
def build_pack_icon() -> bytes:
    stride = ICON_SIZE * 4
    raw = bytearray((stride + 1) * ICON_SIZE)
    for y in range(ICON_SIZE):
        row = y * (stride + 1)
        raw[row] = 0
        t = y / float(ICON_SIZE - 1)
        bg_r = _round_byte(24 + 46 * t)
        bg_g = _round_byte(48 + 78 * t)
        bg_b = _round_byte(92 + 96 * t)
        for x in range(ICON_SIZE):
            index = row + 1 + x * 4
            if x < 3 or y < 3 or x >= ICON_SIZE - 3 or y >= ICON_SIZE - 3:
                raw[index], raw[index + 1], raw[index + 2] = 160, 205, 255
            else:
                raw[index], raw[index + 1], raw[index + 2] = bg_r, bg_g, bg_b
            raw[index + 3] = 255

    _fill_rect(raw, 30, 13, 4, 4, 255, 255, 255)
    _fill_rect(raw, 16, 23, 32, 4, 255, 255, 255)
    _draw_line(raw, 30, 29, 18, 49, 2.6, 255, 255, 255)
    _draw_line(raw, 34, 29, 46, 49, 2.6, 255, 255, 255)

    compressed = zlib.compress(bytes(raw), 9)
    out = bytearray(PNG_SIGNATURE)
    ihdr = struct.pack(">IIBBBBB", ICON_SIZE, ICON_SIZE, 8, 6, 0, 0, 0)
    _write_chunk(out, b"IHDR", ihdr)
    _write_chunk(out, b"IDAT", compressed)
    _write_chunk(out, b"IEND", b"")
    return bytes(out)


def _round_byte(value: float) -> int:
    rounded = int(value + 0.5)
    return max(0, min(255, rounded))


def _set_pixel(raw: bytearray, x: int, y: int, r: int, g: int, b: int) -> None:
    if x < 0 or y < 0 or x >= ICON_SIZE or y >= ICON_SIZE:
        return
    index = y * (ICON_SIZE * 4 + 1) + 1 + x * 4
    raw[index], raw[index + 1], raw[index + 2], raw[index + 3] = r, g, b, 255


def _fill_rect(raw: bytearray, x0: int, y0: int, width: int, height: int, r: int, g: int, b: int) -> None:
    for y in range(y0, y0 + height):
        for x in range(x0, x0 + width):
            _set_pixel(raw, x, y, r, g, b)


def _draw_line(
    raw: bytearray,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    thickness: float,
    r: int,
    g: int,
    b: int,
) -> None:
    dx = x1 - x0
    dy = y1 - y0
    length_squared = float(dx * dx + dy * dy)
    radius = thickness / 2.0
    min_x = math.floor(min(x0, x1) - radius)
    max_x = math.ceil(max(x0, x1) + radius)
    min_y = math.floor(min(y0, y1) - radius)
    max_y = math.ceil(max(y0, y1) + radius)
    for y in range(min_y, max_y + 1):
        for x in range(min_x, max_x + 1):
            t = 0.0
            if length_squared > 0:
                t = ((x - x0) * dx + (y - y0) * dy) / length_squared
                t = max(0.0, min(1.0, t))
            px = x0 + t * dx
            py = y0 + t * dy
            distance = ((x - px) ** 2 + (y - py) ** 2) ** 0.5
            if distance <= radius:
                _set_pixel(raw, x, y, r, g, b)


def _write_chunk(out: bytearray, chunk_type: bytes, data: bytes) -> None:
    out.extend(struct.pack(">I", len(data)))
    out.extend(chunk_type)
    out.extend(data)
    out.extend(struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF))
