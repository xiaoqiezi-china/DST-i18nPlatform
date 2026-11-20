"""Load language documents from mods, KubeJS and FTB Quests."""

from __future__ import annotations

import os
from typing import Any

from . import jarreader, jsonutil, snbt

LANG_SEGMENT = "lang"


def load(session: Any, query: dict[str, Any]) -> dict[str, Any]:
    info = session.current_info()
    if info is None:
        raise ValueError("尚未打开工作目录")
    kind = str(query.get("kind") or "mod").lower()
    if kind == "mod":
        return _load_mod(info, query)
    if kind == "kubejs":
        return _load_kubejs(info, query)
    if kind in ("ftb", "ftbquests"):
        return _load_ftb(info, query)
    raise ValueError("未知的文档类型: " + str(query.get("kind")))


def _load_mod(info: dict[str, Any], query: dict[str, Any]) -> dict[str, Any]:
    file_name = str(query.get("file") or "").strip()
    if not file_name:
        raise ValueError("缺少 mod 文件名")
    mod = None
    for item in info.get("mods", {}).get("mods", []):
        if str(item.get("fileName", "")).lower() == file_name.lower():
            mod = item
            break
    if mod is None:
        raise ValueError("未找到该 mod: " + file_name)

    source_lang = str(query.get("lang") or "").strip().lower() or "en_us"
    candidates = [f for f in mod.get("langFiles", []) if f.get("lang") == source_lang]
    if not candidates:
        candidates = [f for f in mod.get("langFiles", []) if f.get("lang") == "en_us"]
    if not candidates:
        raise ValueError("该 mod 没有可用的源语言文件")

    namespace = str(query.get("namespace") or query.get("ns") or "").strip()
    if not namespace:
        source = candidates[0]
    else:
        source = next(
            (item for item in candidates if str(item.get("namespace", "")).lower() == namespace.lower()),
            None,
        )
        if source is None:
            raise ValueError(
                "该 mod 的命名空间 " + namespace + " 没有 " + source_lang + " 语言文件"
            )

    text = _read_mod_text(mod, source["archivePath"])
    if text is None:
        raise ValueError("无法读取语言文件: " + str(source.get("archivePath")))
    tree, error = jsonutil.try_loads(text)
    if error is not None:
        raise ValueError("语言文件解析失败: " + error)
    entries = jsonutil.flatten(tree)

    existing: dict[str, str] = {}
    zh_file = next(
        (
            item
            for item in mod.get("langFiles", [])
            if str(item.get("namespace", "")).lower() == str(source.get("namespace", "")).lower()
            and item.get("lang") == "zh_cn"
        ),
        None,
    )
    if zh_file is not None:
        zh_text = _read_mod_text(mod, zh_file["archivePath"])
        if zh_text is not None:
            try:
                zh_tree, _err = jsonutil.try_loads(zh_text)
                for item in jsonutil.flatten(zh_tree):
                    existing[item["key"]] = item["value"]
            except Exception:  # noqa: BLE001
                pass

    display_name = str(mod.get("name") or "").strip() or str(mod.get("fileName") or "")
    if str(mod.get("version") or "").strip():
        display_name += " " + str(mod["version"])

    dto = {
        "id": "mod|" + str(mod["fileName"]) + "|" + str(source["namespace"]) + "|" + str(source["lang"]),
        "kind": "mod",
        "ownerId": str(mod.get("modId") or ""),
        "displayName": display_name,
        "relativePath": str(source.get("archivePath") or ""),
        "format": "json",
        "sourceLanguage": str(source.get("lang") or "en_us"),
        "targetLanguage": "zh_cn",
        "entries": _public_entries(entries),
        "existing": existing,
        "hasExisting": len(existing) > 0,
        "canRestoreToExisting": False,
        "modId": str(mod.get("modId") or ""),
        "namespaceName": str(source.get("namespace") or ""),
        "modFileName": str(mod.get("fileName") or ""),
    }
    return {
        "dto": dto,
        "tree": tree,
        "raw": {},
        "segments": build_segments(entries),
        "sourcePath": None,
    }


def _read_mod_text(mod: dict[str, Any], archive_path: str) -> str | None:
    if mod.get("isFolder"):
        full = os.path.join(str(mod["path"]), str(archive_path).replace("/", os.sep))
        if os.path.isfile(full):
            with open(full, "rb") as handle:
                return handle.read().decode("utf-8-sig", errors="replace")
        return None
    return jarreader.read_text(str(mod["path"]), archive_path)


def _load_kubejs(info: dict[str, Any], query: dict[str, Any]) -> dict[str, Any]:
    relative = str(query.get("path") or "").strip()
    if not relative:
        raise ValueError("缺少 kubejs 语言文件路径")
    root = info.get("kubejsDir")
    if not root:
        raise ValueError("没有找到 kubejs 目录")
    full = safe_join(root, relative)
    if not os.path.isfile(full):
        raise ValueError("文件不存在: " + relative)

    text = _read_file(full)
    tree, error = jsonutil.try_loads(text)
    if error is not None:
        raise ValueError("语言文件解析失败: " + error)
    entries = jsonutil.flatten(tree)
    source_lang = os.path.splitext(os.path.basename(full))[0].lower()

    existing: dict[str, str] = {}
    zh_path = os.path.join(os.path.dirname(full), "zh_cn.json")
    if os.path.isfile(zh_path) and os.path.normcase(zh_path) != os.path.normcase(full):
        try:
            zh_tree, _err = jsonutil.try_loads(_read_file(zh_path))
            for item in jsonutil.flatten(zh_tree):
                existing[item["key"]] = item["value"]
        except Exception:  # noqa: BLE001
            pass

    namespace = guess_namespace(root, full)
    rel = relative.replace("\\", "/")
    dto = {
        "id": "kubejs|" + rel,
        "kind": "kubejs",
        "ownerId": namespace,
        "displayName": "KubeJS · " + namespace + " · " + source_lang,
        "relativePath": rel,
        "format": "json",
        "sourceLanguage": source_lang,
        "targetLanguage": "zh_cn",
        "entries": _public_entries(entries),
        "existing": existing,
        "hasExisting": len(existing) > 0,
        "canRestoreToExisting": False,
        "modId": None,
        "namespaceName": namespace,
        "modFileName": None,
    }
    return {
        "dto": dto,
        "tree": tree,
        "raw": {},
        "segments": build_segments(entries),
        "sourcePath": full,
    }


def _load_ftb(info: dict[str, Any], query: dict[str, Any]) -> dict[str, Any]:
    relative = str(query.get("path") or "").strip()
    if not relative:
        raise ValueError("缺少 ftbquests 文件路径")
    root = info.get("ftb", {}).get("root")
    if not root:
        raise ValueError("没有找到 config/ftbquests 目录")
    full = safe_join(root, relative)
    if not os.path.isfile(full):
        raise ValueError("文件不存在: " + relative)

    tree, raw, error = snbt.try_parse(_read_file(full))
    if error is not None:
        raise ValueError("SNBT 解析失败: " + error)
    entries = jsonutil.flatten(tree)
    rel = relative.replace("\\", "/")
    source_lang = guess_lang_document(rel)

    existing: dict[str, str] = {}
    zh_rel = swap_lang(rel, "zh_cn")
    if zh_rel and zh_rel.lower() != rel.lower():
        zh_full = safe_join(root, zh_rel)
        if os.path.isfile(zh_full):
            try:
                zh_tree, _raw, zh_error = snbt.try_parse(_read_file(zh_full))
                if zh_error is None:
                    for item in jsonutil.flatten(zh_tree):
                        existing[item["key"]] = item["value"]
            except Exception:  # noqa: BLE001
                pass

    dto = {
        "id": "ftb|" + rel,
        "kind": "ftb",
        "ownerId": "ftbquests",
        "displayName": "FTB Quests · " + rel,
        "relativePath": rel,
        "format": "snbt",
        "sourceLanguage": source_lang,
        "targetLanguage": "zh_cn",
        "entries": _public_entries(entries),
        "existing": existing,
        "hasExisting": len(existing) > 0,
        "canRestoreToExisting": False,
        "modId": None,
        "namespaceName": None,
        "modFileName": None,
    }
    return {
        "dto": dto,
        "tree": tree,
        "raw": raw,
        "segments": build_segments(entries),
        "sourcePath": full,
    }


def _public_entries(entries: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{"key": item["key"], "value": item["value"]} for item in entries]


def build_segments(entries: list[dict[str, Any]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for item in entries:
        segments = item.get("segments")
        if segments:
            result[item["key"]] = list(segments)
    return result


def _read_file(path: str) -> str:
    with open(path, "rb") as handle:
        return handle.read().decode("utf-8-sig", errors="replace")


def safe_join(root: str, relative: str) -> str:
    rel = relative.replace("\\", "/").lstrip("/")
    full = os.path.abspath(os.path.join(root, rel.replace("/", os.sep)))
    root_full = os.path.abspath(root)
    try:
        common = os.path.commonpath([full, root_full])
    except ValueError as exc:
        raise ValueError("非法的相对路径: " + relative) from exc
    if os.path.normcase(common) != os.path.normcase(root_full):
        raise ValueError("非法的相对路径: " + relative)
    return full


def guess_namespace(kubejs_root: str, full_path: str) -> str:
    rel = os.path.relpath(full_path, kubejs_root).replace("\\", "/")
    parts = rel.split("/")
    for index in range(len(parts) - 1):
        if parts[index].lower() == "assets":
            return parts[index + 1]
    return "kubejs"


def guess_lang_document(relative_path: str) -> str:
    parts = relative_path.replace("\\", "/").split("/")
    for index in range(len(parts) - 1):
        if parts[index].lower() == LANG_SEGMENT:
            return parts[index + 1].lower()
    stem = os.path.splitext(os.path.basename(relative_path))[0].lower()
    return stem if _lang_name(stem) else "en_us"


def swap_lang(relative_path: str, target: str) -> str | None:
    parts = relative_path.replace("\\", "/").split("/")
    for index in range(len(parts) - 1):
        if parts[index].lower() == LANG_SEGMENT:
            ext = os.path.splitext(parts[index + 1])[1]
            parts[index + 1] = target + ext
            return "/".join(parts)
    stem = os.path.splitext(os.path.basename(relative_path))[0]
    if _lang_name(stem.lower()):
        parts[-1] = target + os.path.splitext(relative_path)[1]
        return "/".join(parts)
    return None


def _lang_name(text: str) -> bool:
    import re

    return bool(re.match(r"^[a-z]{2}_[a-z]{2}$", text))


def normalize_lang(value: str | None) -> str:
    text = (value or "").strip().lower()
    import re

    return text if re.match(r"^[a-z]{2,3}_[a-z]{2,4}$", text) else "zh_cn"
