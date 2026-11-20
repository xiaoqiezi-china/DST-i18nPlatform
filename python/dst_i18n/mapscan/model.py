"""Data model shared by the map localization scanner."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from .text import is_noise_text


@dataclass
class TextCandidate:
    """One piece of text that may need localization."""

    id: str
    text: str
    kind: str
    source: str
    file: str
    line: int | None = None
    nbt_path: str = ""
    key: str | None = None
    namespace: str | None = None
    language: str | None = None
    existing: str | None = None
    translated: bool = False
    command: str | None = None
    owner: str | None = None
    raw: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "text": self.text,
            "kind": self.kind,
            "source": self.source,
            "file": self.file,
            "line": self.line,
            "nbtPath": self.nbt_path,
            "key": self.key,
            "namespace": self.namespace,
            "language": self.language,
            "existing": self.existing,
            "translated": self.translated,
            "command": self.command,
            "owner": self.owner,
            "raw": self.raw,
        }
        if self.meta:
            data["meta"] = self.meta
        return data


@dataclass
class LanguageFileInfo:
    pack: str
    path: str
    namespace: str
    language: str
    entry_count: int
    translated_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack": self.pack,
            "path": self.path,
            "namespace": self.namespace,
            "language": self.language,
            "entryCount": self.entry_count,
            "translatedCount": self.translated_count,
        }


@dataclass
class MapScanResult:
    root: str
    kind: str = "map"
    candidates: list[TextCandidate] = field(default_factory=list)
    language_files: list[LanguageFileInfo] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    packs: list[str] = field(default_factory=list)
    _ids: set[str] = field(default_factory=set, repr=False)
    _translate_keys: set[tuple[str, str]] = field(default_factory=set, repr=False)

    def add(self, candidate: TextCandidate) -> None:
        if is_noise_text(candidate.text):
            return
        if candidate.id in self._ids:
            return
        if candidate.kind == "translate" and candidate.key:
            dedupe_key = (candidate.namespace or "", candidate.key)
            if dedupe_key in self._translate_keys:
                return
            self._translate_keys.add(dedupe_key)
        self._ids.add(candidate.id)
        self.candidates.append(candidate)

    def finalize(self) -> None:
        counts: dict[str, int] = {}
        for candidate in self.candidates:
            counts[candidate.source] = counts.get(candidate.source, 0) + 1
        counts["total"] = len(self.candidates)
        counts["translated"] = sum(1 for c in self.candidates if c.translated)
        counts["untranslated"] = counts["total"] - counts["translated"]
        self.stats = counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "kind": self.kind,
            "stats": self.stats,
            "languageFiles": [f.to_dict() for f in self.language_files],
            "packs": self.packs,
            "warnings": self.warnings,
            "candidates": [c.to_dict() for c in self.candidates],
        }


def candidate_id(source: str, file: str, line: int | None, nbt_path: str, text: str) -> str:
    raw = "|".join([source, file, str(line or ""), nbt_path, text])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
