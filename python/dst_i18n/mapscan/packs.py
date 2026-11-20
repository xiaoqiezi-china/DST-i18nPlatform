"""Datapack / resource pack discovery and scanning."""

from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any, Iterator

from .. import jsonutil
from . import nbt, nbt_text
from .model import LanguageFileInfo, MapScanResult, TextCandidate, candidate_id
from .text import TextPart

LANG_PATH = re.compile(r"^(assets|data)/([^/]+)/lang/([^/]+)\.json$", re.IGNORECASE)
FUNCTION_PATH = re.compile(
    r"^data/([^/]+)/(?:functions|function)/(.+\.mcfunction)$", re.IGNORECASE
)
STRUCTURE_PATH = re.compile(r"^data/.*\.nbt$", re.IGNORECASE)


def logical_rel(rel: str) -> str:
    """Strip multi-version pack overlay prefixes.

    Fetchr and several large datapacks ship files such as
    ``pack_format_from_45_to_88.0/data/ns/function/foo.mcfunction``.  The
    logical path starts at ``data/`` or ``assets/``.
    """
    for marker in ("data/", "assets/"):
        if rel.startswith(marker):
            return rel
        index = rel.find("/" + marker)
        if index >= 0:
            return rel[index + 1 :]
    return rel


@dataclass
class Pack:
    name: str
    path: str
    kind: str  # folder | zip
    container: str = ""
    _zip: zipfile.ZipFile | None = field(default=None, repr=False)

    def entry_path(self, logical: str) -> str:
        """Return the stable UI label for a file inside this pack."""
        logical = logical.replace("\\", "/")
        return self.container + "::" + logical if self.container else logical

    def entries(self, suffixes: tuple[str, ...]) -> Iterator[tuple[str, bytes]]:
        if self.kind == "folder":
            for current, _dirs, files in os.walk(self.path):
                for name in files:
                    if not name.lower().endswith(suffixes):
                        continue
                    full = os.path.join(current, name)
                    rel = os.path.relpath(full, self.path).replace("\\", "/")
                    try:
                        with open(full, "rb") as handle:
                            yield rel, handle.read()
                    except OSError:
                        continue
            return

        if self._zip is None:
            raise ValueError("压缩包未打开: " + self.path)
        for info in self._zip.infolist():
            if info.is_dir():
                continue
            rel = info.filename.replace("\\", "/")
            if not rel.lower().endswith(suffixes):
                continue
            try:
                yield rel, self._zip.read(info)
            except (KeyError, OSError, RuntimeError):
                continue

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()
            self._zip = None


def discover_packs(root: str) -> list[Pack]:
    packs: list[Pack] = []
    datapacks = os.path.join(root, "datapacks")
    if os.path.isdir(datapacks):
        for name in sorted(os.listdir(datapacks), key=str.lower):
            full = os.path.join(datapacks, name)
            label = "datapacks/" + name
            if os.path.isdir(full):
                packs.append(Pack(label, full, "folder", label))
            elif name.lower().endswith(".zip"):
                try:
                    packs.append(Pack(label, full, "zip", label, zipfile.ZipFile(full)))
                except (OSError, zipfile.BadZipFile):
                    pass

    resources = os.path.join(root, "resources.zip")
    if os.path.isfile(resources):
        try:
            packs.append(Pack("resources.zip", resources, "zip", "resources.zip", zipfile.ZipFile(resources)))
        except (OSError, zipfile.BadZipFile):
            pass

    resourcepacks = os.path.join(root, "resourcepacks")
    if os.path.isdir(resourcepacks):
        for name in sorted(os.listdir(resourcepacks), key=str.lower):
            full = os.path.join(resourcepacks, name)
            label = "resourcepacks/" + name
            if os.path.isdir(full):
                packs.append(Pack(label, full, "folder", label))
            elif name.lower().endswith(".zip"):
                try:
                    packs.append(
                        Pack(label, full, "zip", label, zipfile.ZipFile(full))
                    )
                except (OSError, zipfile.BadZipFile):
                    pass

    # A datapack / resource pack folder passed directly (pack.mcmeta + data/assets).
    if os.path.isfile(os.path.join(root, "pack.mcmeta")) and (
        os.path.isdir(os.path.join(root, "data")) or os.path.isdir(os.path.join(root, "assets"))
    ):
        name = os.path.basename(os.path.normpath(root))
        packs.append(Pack(name, root, "folder", name))

    return packs


def discover_pack(path: str) -> Pack:
    """Open one resource pack folder or zip as a standalone pack."""
    path = os.path.abspath(path)
    if os.path.isdir(path):
        if not os.path.isdir(os.path.join(path, "assets")):
            raise ValueError("该目录不是材质包：缺少 assets 目录")
        name = os.path.basename(os.path.normpath(path))
        return Pack(name, path, "folder", name)

    if os.path.isfile(path) and path.lower().endswith(".zip"):
        try:
            archive = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile) as exc:
            raise ValueError("无法读取材质包压缩文件：" + str(exc)) from exc
        if not _zip_has_assets(archive):
            archive.close()
            raise ValueError("该压缩包不是材质包：缺少 assets 目录")
        name = os.path.basename(path)
        return Pack(name, path, "zip", name, archive)

    raise ValueError("请选择材质包文件夹或 .zip 文件")


def _zip_has_assets(archive: zipfile.ZipFile) -> bool:
    for info in archive.infolist():
        if info.is_dir():
            continue
        logical = logical_rel(info.filename.replace("\\", "/"))
        if LANG_PATH.match(logical) or "/assets/" in logical or logical.startswith("assets/"):
            return True
    return False


def scan_packs(packs: list[Pack], result: MapScanResult, command_parser: Any) -> None:
    for pack in packs:
        result.packs.append(pack.name)
        _scan_languages(pack, result, "lang")
        _scan_functions(pack, result, command_parser)
        _scan_structures(pack, result, command_parser)


def scan_resource_pack(pack: Pack, result: MapScanResult) -> None:
    """Scan only the standard ``assets/<namespace>/lang/*.json`` entries."""
    result.packs.append(pack.name)
    _scan_languages(pack, result, "resourcepack")


def _scan_languages(pack: Pack, result: MapScanResult, source: str) -> None:
    groups: dict[tuple[str, str], dict[str, str]] = {}
    files: dict[tuple[str, str], tuple[str, int]] = {}
    priority: dict[tuple[str, str], int] = {}

    for rel, data in pack.entries((".json",)):
        logical = logical_rel(rel)
        match = LANG_PATH.match(logical)
        if not match:
            continue
        namespace = match.group(2)
        language = match.group(3).lower()
        try:
            obj = jsonutil.loads(data.decode("utf-8-sig", errors="replace"))
        except Exception as exc:  # noqa: BLE001
            result.warnings.append(pack.name + "::" + rel + " 语言文件解析失败: " + str(exc))
            continue
        if not isinstance(obj, dict):
            result.warnings.append(pack.name + "::" + rel + " 不是标准的 lang 键值对象")
            continue
        key = (namespace, language)
        # Root assets are always active. Version overlays are only a fallback,
        # so do not let pack_format_* directories silently replace the base file.
        prefix = rel[: len(rel) - len(logical)]
        overlay = any(
            part.lower().startswith("pack_format_")
            for part in prefix.replace("\\", "/").split("/")
            if part
        )
        rank = 1 if overlay else 2
        if key in priority and priority[key] >= rank:
            continue
        entries = {str(k): v for k, v in obj.items() if isinstance(v, str)}
        groups[key] = entries
        files[key] = (pack.entry_path(logical), len(entries))
        priority[key] = rank

    for (namespace, language), entries in groups.items():
        rel, count = files[(namespace, language)]
        result.language_files.append(
            LanguageFileInfo(
                pack=pack.name,
                path=rel,
                namespace=namespace,
                language=language,
                entry_count=count,
            )
        )

    for namespace in sorted({key[0] for key in groups}):
        source_lang = _pick_source(groups, namespace)
        if source_lang is None:
            continue
        source_entries = groups[(namespace, source_lang)]
        target = groups.get((namespace, "zh_cn"), {})
        source_file = files[(namespace, source_lang)][0]
        translated = 0

        for key, value in source_entries.items():
            if not value.strip():
                continue
            existing = target.get(key)
            is_translated = bool(existing and existing.strip())
            if is_translated:
                translated += 1
            result.add(
                TextCandidate(
                    id=candidate_id(source, source_file, None, key, value),
                    text=value,
                    kind="lang",
                    source=source,
                    file=source_file,
                    key=key,
                    namespace=namespace,
                    language=source_lang,
                    existing=existing if is_translated else None,
                    translated=is_translated,
                )
            )

        # Reflect target coverage on the zh_cn language-file record.
        for info in result.language_files:
            if info.pack == pack.name and info.namespace == namespace and info.language == "zh_cn":
                info.translated_count = translated


def _pick_source(groups: dict[tuple[str, str], dict[str, str]], namespace: str) -> str | None:
    candidates = [lang for (ns, lang) in groups if ns == namespace and lang != "zh_cn"]
    if not candidates:
        return None
    for preferred in ("en_us", "en_gb"):
        if preferred in candidates:
            return preferred
    return sorted(candidates, key=lambda lang: (-len(groups[(namespace, lang)]), lang))[0]


def _scan_functions(pack: Pack, result: MapScanResult, command_parser: Any) -> None:
    for rel, data in pack.entries((".mcfunction",)):
        logical = logical_rel(rel)
        match = FUNCTION_PATH.match(logical)
        if not match:
            continue
        namespace = match.group(1)
        text = data.decode("utf-8-sig", errors="replace")
        file_label = pack.entry_path(logical)
        for lineno, line in enumerate(text.splitlines(), 1):
            command = line.strip()
            if not command or command.startswith("#"):
                continue
            if command.startswith("$"):
                command = command[1:].strip()
            if not command:
                continue
            for part in command_parser.extract(command, file_label + "#L" + str(lineno)):
                _append_part(result, part, "function", file_label, lineno, namespace)


def _scan_structures(pack: Pack, result: MapScanResult, command_parser: Any) -> None:
    for rel, data in pack.entries((".nbt",)):
        logical = logical_rel(rel)
        if not STRUCTURE_PATH.match(logical):
            continue
        try:
            root = nbt.load_nbt(data)
        except Exception as exc:  # noqa: BLE001
            result.warnings.append(pack.name + "::" + rel + " NBT 结构解析失败: " + str(exc))
            continue
        hits: list[TextPart] = []
        file_label = pack.entry_path(logical)
        nbt_text.collect_nbt_text(root, file_label, hits, command_parser)
        for part in hits:
            _append_part(result, part, "structure", file_label, None, None)


def _append_part(
    result: MapScanResult,
    part: TextPart,
    source: str,
    file: str,
    line: int | None,
    namespace: str | None,
) -> None:
    if part.kind == "command" or not part.text.strip():
        return
    result.add(
        TextCandidate(
            id=candidate_id(source, file, line, part.path, part.text),
            text=part.text,
            kind=part.kind,
            source=source,
            file=file,
            line=line,
            nbt_path=part.path,
            key=part.key,
            namespace=namespace,
            command=part.command,
            owner=part.owner,
        )
    )


def close_packs(packs: list[Pack]) -> None:
    for pack in packs:
        pack.close()
