"""Export orchestration: merge translations, build files, packs and write-back."""

from __future__ import annotations

import os
import re
from typing import Any

from . import documents, exporters, jsonutil, snbt

LANG_RE = re.compile(r"^[a-z]{2,3}_[a-z]{2,4}$")


class ExportService:
    def __init__(self, session: Any) -> None:
        self.session = session

    # ----- basic file building -----------------------------------------
    def build_file_content(self, doc: dict[str, Any], entries: list[dict[str, Any]]) -> bytes:
        if doc["dto"].get("format") == "snbt":
            tree = self.apply_entries(doc, entries)
            text = snbt.serialize(tree, doc.get("raw") or {})
            return text.encode("utf-8")
        tree = self.apply_entries(doc, entries)
        text = jsonutil.dumps(tree, indented=True) + "\n"
        return text.encode("utf-8")

    def apply_entries(self, doc: dict[str, Any], entries: list[dict[str, Any]]) -> Any:
        tree = jsonutil.clone(doc.get("tree"))
        if tree is None:
            return jsonutil.build_flat(entries)

        if jsonutil.is_flat_object(tree):
            for entry in entries:
                if entry.get("key") is not None:
                    tree[str(entry["key"])] = str(entry.get("value") or "")
            return tree

        segments_map = doc.get("segments") or {}
        for entry in entries:
            key = str(entry.get("key") or "")
            if not key:
                continue
            segs = segments_map.get(key)
            if not segs:
                segs = key.split(".")
            jsonutil.set_path(tree, segs, str(entry.get("value") or ""))
        return tree

    @staticmethod
    def merge_entries(doc: dict[str, Any], translated: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        existing = {
            str(k): str(v or "")
            for k, v in (doc["dto"].get("existing") or {}).items()
            if str(k).strip()
        }
        translated_map: dict[str, str] = {}
        for entry in translated or []:
            if not entry:
                continue
            key = str(entry.get("key") or "")
            if not key.strip():
                continue
            translated_map[key] = str(entry.get("value") or "")

        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        segments = doc.get("segments") or {}
        for entry in doc["dto"].get("entries") or []:
            key = str(entry.get("key") or "")
            seen.add(key)
            value = entry.get("value") or ""
            if key in existing:
                value = existing[key]
            if key in translated_map:
                value = translated_map[key]
            result.append({"key": key, "value": value, "segments": segments.get(key)})

        for key, value in existing.items():
            if key in seen:
                continue
            seen.add(key)
            result.append({"key": key, "value": value, "segments": segments.get(key)})
        for key, value in translated_map.items():
            if key in seen:
                continue
            seen.add(key)
            result.append({"key": key, "value": value, "segments": segments.get(key)})
        return result

    # ----- resource pack ------------------------------------------------
    def build_resource_pack(self, request: dict[str, Any]) -> tuple[bytes, str]:
        doc = documents.load(
            self.session,
            {
                "kind": "mod",
                "file": request.get("file"),
                "namespace": request.get("namespace"),
                "lang": request.get("lang"),
            },
        )
        target_lang = normalize_lang(request.get("targetLanguage"))
        namespace = sanitize_namespace_filename(str(doc["dto"].get("namespaceName") or "minecraft"))

        pack_name = str(request.get("packName") or "").strip() or "汉化资源包"
        pack_version = str(request.get("packVersion") or "").strip() or "1.0.0"
        description = str(request.get("description") or "").strip()
        if not description:
            description = str(doc["dto"].get("displayName") or "") + " 汉化资源包 v" + str(
                request.get("packVersion") or "1.0.0"
            )

        options = {
            "packName": pack_name,
            "packVersion": pack_version,
            "mcVersion": request.get("mcVersion"),
            "packFormat": request.get("packFormat"),
            "targetLanguage": target_lang,
            "description": description,
        }
        entries = self.merge_entries(doc, request.get("entries") or [])
        data = exporters.build_resource_pack(namespace, entries, options)
        file_name = exporters.safe_file_name(
            (str(request.get("packName") or "").strip() or "汉化资源包")
            + "-" + pack_version + "-" + namespace + ".zip",
            "resourcepack.zip",
        )
        return data, file_name

    # ----- overlays ------------------------------------------------------
    def build_kubejs_overlay(self, request: dict[str, Any]) -> tuple[bytes, str]:
        info = self._require()
        if not info.get("kubejsDir"):
            raise ValueError("没有找到 kubejs 目录")
        target_lang = normalize_lang(request.get("targetLanguage"))
        workspace_name = workspace_name_of(info)
        scanned = [
            {"path": item.get("relativePath"), "entries": None}
            for item in info.get("kubejs", {}).get("files", [])
        ]
        plan = self._build_overlay_plan(request, scanned)
        files = self._build_overlay_files("kubejs", "kubejs", target_lang, plan)
        if not files:
            raise ValueError("没有可导出的 kubejs 语言文件")

        options = {
            "packName": workspace_name + " KubeJS 覆盖包",
            "packVersion": "",
            "targetLanguage": target_lang,
            "description": "KubeJS 汉化覆盖包，解压到整合包根目录覆盖 kubejs 文件夹即可。",
        }
        data = exporters.build_overlay_zip(files, options)
        return data, exporters.safe_file_name(
            workspace_name + "-kubejs-" + target_lang + ".zip", "kubejs-overlay.zip"
        )

    def build_ftb_overlay(self, request: dict[str, Any]) -> tuple[bytes, str]:
        info = self._require()
        if not info.get("ftb", {}).get("root"):
            raise ValueError("没有找到 config/ftbquests 目录")
        target_lang = normalize_lang(request.get("targetLanguage"))
        workspace_name = workspace_name_of(info)
        scanned = [
            {"path": item.get("relativePath"), "entries": None}
            for item in info.get("ftb", {}).get("files", [])
        ]
        plan = self._build_overlay_plan(request, scanned)
        files = self._build_overlay_files("ftb", "config/ftbquests", target_lang, plan)
        if not files:
            raise ValueError("没有可导出的 ftbquests 语言文件")

        options = {
            "packName": workspace_name + " FTB Quests 覆盖包",
            "packVersion": "",
            "targetLanguage": target_lang,
            "description": "FTB Quests 汉化覆盖包，解压到整合包根目录覆盖 config/ftbquests 文件夹即可。",
        }
        data = exporters.build_overlay_zip(files, options)
        return data, exporters.safe_file_name(
            workspace_name + "-ftbquests-" + target_lang + ".zip", "ftbquests-overlay.zip"
        )

    def _build_overlay_plan(
        self, request: dict[str, Any], scanned: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        requested = request.get("files") or []
        plan: list[dict[str, Any]] = []
        if requested:
            for item in requested:
                if not item or not str(item.get("path") or "").strip():
                    continue
                plan.append({"path": item.get("path"), "entries": item.get("entries")})
            return plan

        target_lang = normalize_lang(request.get("targetLanguage"))
        for item in scanned:
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            if documents.swap_lang(path.replace("\\", "/"), target_lang) is None:
                continue
            plan.append({"path": path, "entries": item.get("entries")})

        plan.sort(key=lambda item: 1 if is_language_path(str(item["path"]), target_lang) else 0)
        return plan

    def _build_overlay_files(
        self,
        kind: str,
        zip_prefix: str,
        target_lang: str,
        plan: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        files: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in plan:
            raw_path = str(item.get("path") or "").replace("\\", "/").lstrip("/")
            if not raw_path:
                continue
            target_path = documents.swap_lang(raw_path, target_lang) or raw_path
            zip_path = zip_prefix + "/" + target_path.lstrip("/")
            if zip_path.lower() in seen:
                continue
            seen.add(zip_path.lower())
            doc = documents.load(self.session, {"kind": kind, "path": raw_path})
            content = self.build_file_content(doc, self.merge_entries(doc, item.get("entries")))
            files.append({"zipPath": zip_path, "content": content.decode("utf-8")})
        return files

    # ----- lang file / write-back ---------------------------------------
    def build_lang_file(self, request: dict[str, Any]) -> tuple[bytes, str]:
        doc = documents.load(
            self.session,
            {
                "kind": request.get("kind"),
                "file": request.get("file"),
                "namespace": request.get("namespace"),
                "lang": request.get("lang"),
                "path": request.get("path"),
            },
        )
        target_lang = normalize_lang(request.get("targetLanguage"))
        data = self.build_file_content(doc, request.get("entries") or [])
        extension = ".snbt" if doc["dto"].get("format") == "snbt" else ".json"
        raw_name = str(request.get("fileName") or "").strip() or target_lang + extension
        return data, exporters.safe_file_name(raw_name, target_lang + ".json")

    def write_back(self, request: dict[str, Any]) -> list[str]:
        info = self._require()
        doc = documents.load(
            self.session,
            {
                "kind": request.get("kind"),
                "file": request.get("file"),
                "namespace": request.get("namespace"),
                "lang": request.get("lang"),
                "path": request.get("path"),
            },
        )
        target_lang = normalize_lang(request.get("targetLanguage"))
        data = self.build_file_content(doc, request.get("entries") or [])

        if str(request.get("kind") or "").lower() == "mod":
            output_directory = str(request.get("outputDirectory") or "").strip()
            if not output_directory:
                output_directory = os.path.join(
                    str(info["path"]),
                    "dst_i18n_output",
                    "assets",
                    sanitize_namespace_filename(str(doc["dto"].get("namespaceName") or "minecraft")),
                    "lang",
                )
            os.makedirs(output_directory, exist_ok=True)
            target = os.path.join(output_directory, target_lang + ".json")
            _write_bytes(target, data)
            return [target]

        source_path = doc.get("sourcePath")
        if not source_path:
            raise ValueError("无法确定源文件路径")
        target_path = documents.swap_lang(str(source_path).replace("\\", "/"), target_lang)
        if target_path is None:
            raise ValueError("无法推断目标文件名")
        target_path = target_path.replace("/", os.sep)
        if os.path.exists(target_path) and not bool(request.get("overwrite")):
            raise ValueError("目标文件已存在: " + target_path)
        folder = os.path.dirname(target_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        _write_bytes(target_path, data)
        return [target_path]

    def _require(self) -> dict[str, Any]:
        info = self.session.current_info()
        if info is None:
            raise ValueError("尚未打开工作目录")
        return info


def _write_bytes(path: str, data: bytes) -> None:
    with open(path, "wb") as handle:
        handle.write(data)


def normalize_lang(lang: str | None) -> str:
    value = (lang or "").strip().lower() or "zh_cn"
    return value if LANG_RE.match(value) else "zh_cn"


def sanitize_namespace_filename(namespace: str) -> str:
    clean = "".join(ch for ch in namespace.lower() if ch.isalnum() or ch in "_.-")
    return clean or "minecraft"


def workspace_name_of(info: dict[str, Any]) -> str:
    name = str(info.get("name") or "").strip()
    if name:
        return name
    trimmed = str(info.get("path") or "").rstrip("\\/")
    base = os.path.basename(trimmed)
    return base or "整合包"


def is_language_path(relative_path: str, target_lang: str) -> bool:
    path = relative_path.replace("\\", "/")
    parts = path.split("/")
    for index in range(len(parts) - 1):
        if parts[index].lower() == "lang" and index + 1 < len(parts):
            if os.path.splitext(parts[index + 1])[0].lower() == target_lang.lower():
                return True
    return os.path.splitext(os.path.basename(path))[0].lower() == target_lang.lower()
