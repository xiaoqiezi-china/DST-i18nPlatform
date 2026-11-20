"""Workspace layout detection and language-file indexing."""

from __future__ import annotations

import os
import re
import time
from typing import Any, Callable

from . import jarreader, jsonutil, snbt

LANG_FILE = re.compile(
    r"^(?:(?P<root>.+?)/)?assets/(?P<ns>[a-zA-Z0-9_.\-]+)/lang/(?P<lang>[a-zA-Z0-9_\-]+)\.json$",
    re.IGNORECASE,
)

VERSION_FILES = ("version.json", "instance.json", "mmc-pack.json")


class ScanCancelled(Exception):
    pass


class Reporter:
    """Progress sink shared with the HTTP layer (mutations protected by a lock)."""

    def __init__(self, progress: dict[str, Any], lock: Any):
        self.progress = progress
        self.lock = lock

    def phase(self, phase: str, label: str, total: int = 0) -> None:
        with self.lock:
            self.progress.update(
                {
                    "phase": phase,
                    "phaseLabel": label,
                    "total": total,
                    "current": 0,
                    "percent": 0,
                }
            )

    def step(self, item: str = "", current: int | None = None, total: int | None = None) -> None:
        with self.lock:
            if current is None:
                self.progress["current"] = int(self.progress.get("current", 0)) + 1
            else:
                self.progress["current"] = current
            if total is not None:
                self.progress["total"] = total
            self.progress["currentItem"] = item or ""
            total_value = int(self.progress.get("total", 0))
            current_value = int(self.progress.get("current", 0))
            self.progress["percent"] = (
                0 if total_value <= 0 else max(0, min(100, int(current_value * 100 / total_value)))
            )

    def mods_found(self, count: int) -> None:
        with self.lock:
            self.progress["modsFound"] = count


def path_exists(path: str) -> bool:
    return bool(path) and os.path.isdir(path)


def find_dir(root: str, name: str) -> str | None:
    direct = os.path.join(root, name)
    if os.path.isdir(direct):
        return direct
    for sub in (".minecraft", "minecraft"):
        nested = os.path.join(root, sub, name)
        if os.path.isdir(nested):
            return nested
    return None


def validate_path(path: str) -> tuple[bool, list[str]]:
    candidates: list[str] = []
    if not path or not os.path.isdir(path):
        return False, candidates
    if (
        os.path.isdir(os.path.join(path, "mods"))
        or os.path.isdir(os.path.join(path, "kubejs"))
        or os.path.isdir(os.path.join(path, "config"))
        or os.path.isdir(os.path.join(path, ".minecraft"))
    ):
        return True, candidates
    candidates = find_candidates(path)
    return False, candidates


def find_candidates(root: str) -> list[str]:
    deadline = time.monotonic() + 2.0
    result: list[str] = []
    queue: list[tuple[str, int]] = [(root, 0)]
    while queue and len(result) < 20 and time.monotonic() < deadline:
        current, depth = queue.pop(0)
        if depth >= 3:
            continue
        try:
            children = [
                os.path.join(current, name)
                for name in sorted(os.listdir(current), key=str.lower)
                if os.path.isdir(os.path.join(current, name))
            ]
        except OSError:
            continue
        for child in children:
            if time.monotonic() >= deadline or len(result) >= 20:
                break
            name = os.path.basename(child)
            if name.startswith("."):
                continue
            if (
                os.path.isdir(os.path.join(child, "mods"))
                or os.path.isdir(os.path.join(child, "kubejs"))
                or os.path.isdir(os.path.join(child, ".minecraft"))
            ):
                if child not in result:
                    result.append(child)
                continue
            queue.append((child, depth + 1))
    result.sort(key=lambda item: len(item))
    return result[:20]


def detect_game_version(path: str) -> tuple[str, str]:
    game_version = ""
    loader = ""
    try:
        names = sorted(os.listdir(path), key=str.lower)
    except OSError:
        return "", ""
    files = [os.path.join(path, n) for n in names if n.lower() in VERSION_FILES]
    for file_path in files:
        try:
            node, _err = jsonutil.try_loads(_read_text_file(file_path))
        except OSError:
            continue
        if not isinstance(node, dict):
            continue
        if not game_version:
            game_version = _first_string(node, ("name", "id", "version", "gameVersion"))
        components = node.get("components")
        if isinstance(components, list):
            for comp in components:
                if not isinstance(comp, dict):
                    continue
                uid = comp.get("uid")
                if uid == "net.minecraft" and not game_version:
                    game_version = _as_str(comp.get("version"))
                if uid == "net.fabricmc.fabric-loader":
                    loader = "fabric"
                if uid == "net.minecraftforge":
                    loader = "forge"
                if uid == "net.neoforged":
                    loader = "neoforge"
                if uid == "org.quiltmc.quilt-loader":
                    loader = "quilt"
        libraries = node.get("libraries")
        if isinstance(libraries, list):
            for lib in libraries:
                if not isinstance(lib, dict):
                    continue
                lib_name = _as_str(lib.get("name"))
                low = lib_name.lower()
                if "fabric-loader" in low:
                    loader = "fabric"
                if "forge" in low and not loader:
                    loader = "forge"
                if "neoforge" in low:
                    loader = "neoforge"
    return game_version, loader


def _first_string(node: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = node.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _as_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _read_text_file(path: str) -> str:
    with open(path, "rb") as handle:
        return handle.read().decode("utf-8-sig", errors="replace")


def count_entries_text(text: str) -> int:
    node, _raw, err = snbt.try_parse(text) if text.lstrip().startswith("{") else (None, {}, "not snbt")
    if err is None:
        return len(jsonutil.flatten(node))
    node, err2 = jsonutil.try_loads(text)
    return len(jsonutil.flatten(node)) if err2 is None else 0


def count_entries_file(path: str, snbt_file: bool = False) -> int:
    try:
        text = _read_text_file(path)
    except OSError:
        return 0
    if snbt_file or path.lower().endswith(".snbt"):
        node, _raw, err = snbt.try_parse(text)
        return len(jsonutil.flatten(node)) if err is None else 0
    node, err = jsonutil.try_loads(text)
    return len(jsonutil.flatten(node)) if err is None else 0


def scan_mods(
    info: dict[str, Any],
    reporter: Reporter | None,
    cancel: Any,
    warnings: list[str],
) -> dict[str, Any]:
    mods_dir = info.get("modsDir")
    result: dict[str, Any] = {"found": mods_dir is not None, "root": mods_dir, "mods": [], "warnings": warnings}
    if mods_dir is None:
        return result

    files: list[str] = []
    folders: list[str] = []
    try:
        for name in sorted(os.listdir(mods_dir), key=str.lower):
            full = os.path.join(mods_dir, name)
            if os.path.isdir(full):
                folders.append(full)
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in (".jar", ".zip"):
                continue
            if name.lower().endswith(".disabled"):
                continue
            if name.lower().endswith((".jar.disabled", ".jar.bak", ".zip")):
                continue
            files.append(full)
    except OSError as exc:
        warnings.append("无法读取 mods 目录: " + str(exc))
        return result

    total = len(files) + len(folders)
    if reporter is not None:
        reporter.phase("mods", "正在解析模组…", total)
    index = 0
    mods: list[dict[str, Any]] = []

    for folder in folders:
        if cancel.is_set():
            raise ScanCancelled()
        folder_name = os.path.basename(folder)
        index += 1
        if reporter is not None:
            reporter.step(folder_name, index, total)
        try:
            assets_dir = os.path.join(folder, "assets")
            if not os.path.isdir(assets_dir):
                continue
            lang_files: list[dict[str, Any]] = []
            for current, _sub, names in os.walk(assets_dir):
                if cancel.is_set():
                    raise ScanCancelled()
                for name in names:
                    if not name.lower().endswith(".json"):
                        continue
                    full = os.path.join(current, name)
                    rel = os.path.relpath(full, folder).replace("\\", "/")
                    match = LANG_FILE.match(rel)
                    if not match:
                        continue
                    lang_files.append(
                        {
                            "namespace": match.group("ns"),
                            "lang": match.group("lang").lower(),
                            "archivePath": rel,
                            "entryCount": count_entries_file(full),
                        }
                    )
            if not lang_files:
                continue
            has_zh = any(item["lang"] == "zh_cn" for item in lang_files)
            for item in lang_files:
                item["hasZhCn"] = has_zh
            mods.append(
                {
                    "id": folder_name,
                    "name": folder_name,
                    "fileName": folder_name,
                    "modId": folder_name,
                    "version": "",
                    "loader": "",
                    "path": folder,
                    "isFolder": True,
                    "enabled": True,
                    "langFiles": lang_files,
                    "entryCount": sum(i["entryCount"] for i in lang_files if i["lang"] == "en_us"),
                    "translatedCount": 0,
                    "error": None,
                }
            )
            if reporter is not None:
                reporter.mods_found(len(mods))
        except Exception as exc:  # noqa: BLE001
            warnings.append(folder_name + ": " + _short_error(exc))

    for file_path in files:
        if cancel.is_set():
            raise ScanCancelled()
        name = os.path.basename(file_path)
        index += 1
        if reporter is not None:
            reporter.step(name, index, total)
        try:
            entries = jarreader.list_entries(file_path, "")
            lang_entries = []
            for entry in entries:
                match = LANG_FILE.match(entry.replace("\\", "/"))
                if match:
                    lang_entries.append((entry, match))
            meta = jarreader.read_mod_meta(file_path)
            stem = os.path.splitext(name)[0]
            lang_files = []
            for entry, match in lang_entries:
                if cancel.is_set():
                    raise ScanCancelled()
                lang_files.append(
                    {
                        "namespace": match.group("ns"),
                        "lang": match.group("lang").lower(),
                        "archivePath": entry,
                        "entryCount": _count_jar_entry(file_path, entry),
                    }
                )
            if not lang_files:
                continue
            has_zh = any(item["lang"] == "zh_cn" for item in lang_files)
            for item in lang_files:
                item["hasZhCn"] = has_zh
            mods.append(
                {
                    "id": stem,
                    "name": meta[1] or stem,
                    "fileName": name,
                    "modId": meta[0] or stem,
                    "version": meta[2],
                    "loader": meta[3],
                    "path": file_path,
                    "isFolder": False,
                    "enabled": True,
                    "langFiles": lang_files,
                    "entryCount": sum(i["entryCount"] for i in lang_files if i["lang"] == "en_us"),
                    "translatedCount": 0,
                    "error": None,
                }
            )
            if reporter is not None:
                reporter.mods_found(len(mods))
        except Exception as exc:  # noqa: BLE001
            warnings.append(name + ": " + _short_error(exc))

    result["mods"] = mods
    return result


def _count_jar_entry(jar: str, entry: str) -> int:
    try:
        text = jarreader.read_text(jar, entry)
    except Exception:  # noqa: BLE001
        return 0
    if text is None:
        return 0
    node, err = jsonutil.try_loads(text)
    return len(jsonutil.flatten(node)) if err is None else 0


def _short_error(exc: Exception) -> str:
    text = str(exc).strip()
    return text.split("\n")[0][:200] if text else type(exc).__name__


def _asset_dirs(kubejs_root: str) -> list[str]:
    result: list[str] = []
    direct = os.path.join(kubejs_root, "assets")
    if os.path.isdir(direct):
        result.append(direct)
    try:
        for current, subdirs, _names in os.walk(kubejs_root):
            for sub in subdirs:
                if sub.lower() == "assets":
                    full = os.path.join(current, sub)
                    if full not in result:
                        result.append(full)
    except OSError:
        pass
    return result


def scan_kubejs(info: dict[str, Any], reporter: Reporter | None, cancel: Any) -> dict[str, Any]:
    kubejs_root = info.get("kubejsDir")
    result: dict[str, Any] = {
        "found": kubejs_root is not None,
        "root": kubejs_root,
        "namespaces": [],
        "files": [],
        "warnings": [],
    }
    if kubejs_root is None:
        return result

    if reporter is not None:
        reporter.phase("kubejs", "正在解析 KubeJS…")
    try:
        asset_dirs = _asset_dirs(kubejs_root)
        if not asset_dirs:
            result["warnings"].append("kubejs 目录下没有找到 assets 文件夹。")
            return result

        namespaces: list[str] = []
        files: list[dict[str, Any]] = []
        for assets in asset_dirs:
            try:
                ns_names = sorted(os.listdir(assets), key=str.lower)
            except OSError:
                continue
            for ns in ns_names:
                if cancel.is_set():
                    raise ScanCancelled()
                ns_dir = os.path.join(assets, ns)
                if not os.path.isdir(ns_dir):
                    continue
                lang_dir = os.path.join(ns_dir, "lang")
                if not os.path.isdir(lang_dir):
                    continue
                if ns not in namespaces:
                    namespaces.append(ns)
                try:
                    lang_names = sorted(os.listdir(lang_dir), key=str.lower)
                except OSError:
                    continue
                has_zh = any(os.path.splitext(n)[0].lower() == "zh_cn" for n in lang_names)
                for name in lang_names:
                    if not name.lower().endswith(".json"):
                        continue
                    full = os.path.join(lang_dir, name)
                    files.append(
                        {
                            "namespace": ns,
                            "lang": os.path.splitext(name)[0].lower(),
                            "relativePath": os.path.relpath(full, kubejs_root).replace("\\", "/"),
                            "entryCount": count_entries_file(full),
                            "hasZhCn": has_zh,
                        }
                    )
        files.sort(key=lambda item: (item["namespace"].lower(), item["lang"].lower()))
        result["namespaces"] = namespaces
        result["files"] = files
    except Exception as exc:  # noqa: BLE001
        result["warnings"].append("扫描 kubejs 失败: " + _short_error(exc))
    if reporter is not None:
        reporter.step("", 1, 1)
    return result


def scan_ftb(info: dict[str, Any], reporter: Reporter | None, cancel: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "found": False,
        "root": None,
        "languages": [],
        "files": [],
        "warnings": [],
    }
    roots: list[str] = []
    config_dir = info.get("configDir")
    if config_dir is not None:
        ftb = os.path.join(config_dir, "ftbquests")
        if os.path.isdir(ftb):
            roots.append(ftb)
    kubejs_dir = info.get("kubejsDir")
    if kubejs_dir is not None:
        alt = os.path.join(kubejs_dir, "config", "ftbquests")
        if os.path.isdir(alt) and alt not in roots:
            roots.append(alt)

    if not roots:
        result["warnings"].append("没有找到 config/ftbquests 目录。")
        return result

    result["found"] = True
    result["root"] = roots[0]
    if reporter is not None:
        reporter.phase("ftb", "正在解析 FTB Quests…")

    for ftb_root in roots:
        try:
            all_files: list[str] = []
            for current, _sub, names in os.walk(ftb_root):
                if cancel.is_set():
                    raise ScanCancelled()
                for name in names:
                    if name.lower().endswith(".snbt"):
                        all_files.append(os.path.join(current, name))
            lang_files = [
                path
                for path in all_files
                if "lang" in [seg.lower() for seg in os.path.relpath(path, ftb_root).replace("\\", "/").split("/")]
            ]
            selected = lang_files if lang_files else all_files
            for file_path in selected:
                if cancel.is_set():
                    raise ScanCancelled()
                rel = os.path.relpath(file_path, ftb_root).replace("\\", "/")
                lang = guess_ftb_lang(rel)
                result["files"].append(
                    {
                        "lang": lang,
                        "relativePath": rel,
                        "entryCount": count_entries_file(file_path, snbt_file=True),
                        "hasZhCn": False,
                    }
                )
                if lang not in result["languages"]:
                    result["languages"].append(lang)
        except Exception as exc:  # noqa: BLE001
            result["warnings"].append("扫描 ftbquests 失败: " + _short_error(exc))

    has_zh = any(item["lang"] == "zh_cn" for item in result["files"])
    for item in result["files"]:
        item["hasZhCn"] = has_zh
    result["files"].sort(key=lambda item: item["relativePath"].lower())
    if reporter is not None:
        reporter.step("", 1, 1)
    return result


def guess_ftb_lang(relative_path: str) -> str:
    parts = relative_path.replace("\\", "/").split("/")
    for index in range(len(parts) - 1):
        if parts[index].lower() == "lang":
            return os.path.splitext(parts[index + 1])[0].lower()
    stem = os.path.splitext(os.path.basename(relative_path))[0].lower()
    if re.match(r"^[a-z]{2}_[a-z]{2}$", stem):
        return stem
    return "en_us"


def scan_workspace(
    path: str,
    reporter: Reporter,
    cancel: Any,
) -> dict[str, Any]:
    info: dict[str, Any] = {
        "path": path,
        "exists": os.path.isdir(path),
        "name": os.path.basename(os.path.normpath(path)) or path,
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
        "scannedAt": int(time.time() * 1000),
    }
    if not info["exists"]:
        info["warnings"].append("工作目录不存在: " + path)
        return info

    reporter.phase("prepare", "正在识别目录结构…")
    game_version, loader = detect_game_version(path)
    info["gameVersion"] = game_version or None
    info["loader"] = loader or None

    info["modsDir"] = find_dir(path, "mods")
    info["kubejsDir"] = find_dir(path, "kubejs")
    info["configDir"] = find_dir(path, "config")

    if not info["modsDir"] and not info["kubejsDir"] and not info["configDir"]:
        candidates = find_candidates(path)
        info["candidates"] = candidates
        if candidates:
            info["warnings"].append(
                "当前目录下没有直接找到 mods/kubejs/config，可能选择了启动器目录。请在下方选择具体的版本隔离目录。"
            )
        else:
            info["warnings"].append("没有找到 mods、kubejs 或 config 目录，请确认选择的是整合包 / .minecraft 目录。")

    if cancel.is_set():
        raise ScanCancelled()
    info["mods"] = scan_mods(info, reporter, cancel, info["mods"]["warnings"])

    if cancel.is_set():
        raise ScanCancelled()
    info["kubejs"] = scan_kubejs(info, reporter, cancel)

    if cancel.is_set():
        raise ScanCancelled()
    info["ftb"] = scan_ftb(info, reporter, cancel)

    reporter.phase("finish", "扫描完成", 1)
    reporter.step("", 1, 1)
    info["scannedAt"] = int(time.time() * 1000)
    return info
