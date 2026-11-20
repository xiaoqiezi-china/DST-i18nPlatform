"""Read jar/zip entries without extracting; parse mod metadata."""

from __future__ import annotations

import json
import zipfile
from typing import Any

from . import jsonutil


def _norm(name: str) -> str:
    return name.replace("\\", "/")


def list_entries(path: str, prefix: str = "") -> list[str]:
    result: list[str] = []
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            name = _norm(info.filename)
            if name.endswith("/"):
                continue
            if name.lower().startswith(prefix.lower()):
                result.append(name)
    return result


def read_bytes(path: str, entry_name: str) -> bytes | None:
    target = _norm(entry_name).lower()
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if _norm(info.filename).lower() == target:
                return archive.read(info)
    return None


def read_text(path: str, entry_name: str) -> str | None:
    data = read_bytes(path, entry_name)
    if data is None:
        return None
    return data.decode("utf-8-sig", errors="replace")


def parse_mods_toml(text: str) -> tuple[str, str, str]:
    mod_id = ""
    display_name = ""
    version = ""
    in_mods = False
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[["):
            in_mods = line.lower().startswith("[[mods]]")
            continue
        if line.startswith("["):
            in_mods = False
            continue
        if not in_mods:
            continue
        idx = line.find("=")
        if idx <= 0:
            continue
        key = line[:idx].strip()
        value = _unquote(line[idx + 1:].strip())
        if key == "modId" and not mod_id:
            mod_id = value
        elif key == "displayName" and not display_name:
            display_name = value
        elif key == "version" and not version:
            version = value
    return mod_id, display_name, version


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1]
    return value


def read_mod_meta(path: str) -> tuple[str, str, str, str]:
    mod_id = ""
    name = ""
    version = ""
    loader = ""
    try:
        entries = list_entries(path, "")
        lower_entries = [(entry, entry.lower()) for entry in entries]

        fabric = next((entry for entry, low in lower_entries if low.endswith("fabric.mod.json")), None)
        if fabric is not None:
            loader = "fabric"
            text = read_text(path, fabric) or ""
            try:
                node = jsonutil.loads(text)
            except Exception:  # noqa: BLE001
                node = None
            if isinstance(node, dict):
                mod_id = _as_str(node.get("id")) or mod_id
                name = _as_str(node.get("name")) or name
                version = _as_str(node.get("version")) or version

        quilt = next((entry for entry, low in lower_entries if low.endswith("quilt.mod.json")), None)
        if quilt is not None and not loader:
            loader = "quilt"
            text = read_text(path, quilt) or ""
            try:
                node = jsonutil.loads(text)
            except Exception:  # noqa: BLE001
                node = None
            if isinstance(node, dict):
                ql = node.get("quilt_loader")
                if isinstance(ql, dict):
                    mod_id = _as_str(ql.get("id")) or mod_id
                    version = _as_str(ql.get("version")) or version
                    meta = ql.get("metadata")
                    if isinstance(meta, dict):
                        name = _as_str(meta.get("name")) or name

        toml = next((entry for entry, low in lower_entries if low == "meta-inf/mods.toml"), None)
        if toml is None:
            toml = next((entry for entry, low in lower_entries if low.endswith("/meta-inf/mods.toml")), None)
        if toml is None:
            toml = next((entry for entry, low in lower_entries if low.endswith("meta-inf/neoforge.mods.toml")), None)
        if toml is not None:
            if not loader:
                loader = "neoforge" if toml.lower().endswith("neoforge.mods.toml") else "forge"
            text = read_text(path, toml) or ""
            toml_mod_id, toml_name, toml_version = parse_mods_toml(text)
            if not mod_id:
                mod_id = toml_mod_id
            if not name:
                name = toml_name
            if not version:
                version = toml_version

        if not loader:
            if any(low.startswith("meta-inf/services/net.minecraftforge") for _entry, low in lower_entries):
                loader = "forge"
    except Exception:  # noqa: BLE001
        pass

    return mod_id, name, version, loader


def _as_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


# Kept for callers that want to inspect raw metadata JSON.
def read_json_entry(path: str, entry_name: str) -> Any:
    text = read_text(path, entry_name)
    if text is None:
        return None
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return None
