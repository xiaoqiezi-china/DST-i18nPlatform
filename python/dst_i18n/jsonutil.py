"""JSON helpers: tolerant parsing, flatten/apply, deterministic serialization."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable


def _strip_comments(text: str) -> str:
    """Remove // and /* */ comments while respecting string literals."""
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    quote = ""
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            quote = ch
            out.append(ch)
            i += 1
            continue

        if ch == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":
                i += 2
                while i < n and text[i] not in "\r\n":
                    i += 1
                continue
            if nxt == "*":
                i += 2
                while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                    i += 1
                i += 2
                continue

        out.append(ch)
        i += 1
    return "".join(out)


def _strip_trailing_commas(text: str) -> str:
    """Remove commas that appear directly before } or ] outside strings."""
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    quote = ""
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            quote = ch
            out.append(ch)
            i += 1
            continue

        if ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1
                continue

        out.append(ch)
        i += 1
    return "".join(out)


def loads(text: str | None) -> Any:
    """Parse JSON the way System.Text.Json does here: comments and trailing commas allowed."""
    if text is None:
        return None
    if not text.strip():
        return None
    cleaned = _strip_trailing_commas(_strip_comments(text))
    return json.loads(cleaned)


def try_loads(text: str | None) -> tuple[Any, str | None]:
    try:
        return loads(text), None
    except Exception as exc:  # noqa: BLE001 - surface the message to the user
        return None, str(exc)


def dumps(value: Any, indented: bool = False) -> str:
    """Serialize with UTF-8 friendly escaping (Chinese kept as-is)."""
    if value is None:
        return "{}"
    if indented:
        return json.dumps(value, ensure_ascii=False, indent=2)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def flatten(root: Any) -> list[dict[str, Any]]:
    """Flatten every string leaf into {key, value, segments} in document order."""
    entries: list[dict[str, Any]] = []
    segments: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                segments.append(str(key))
                walk(child)
                segments.pop()
        elif isinstance(node, list):
            for index, child in enumerate(node):
                segments.append("[" + str(index) + "]")
                walk(child)
                segments.pop()
        elif isinstance(node, str):
            entries.append(
                {
                    "key": ".".join(segments),
                    "value": node,
                    "segments": list(segments),
                }
            )

    walk(root)
    return entries


def is_flat_object(tree: Any) -> bool:
    return isinstance(tree, dict) and all(isinstance(v, str) for v in tree.values())


def _parse_index(segment: str) -> int:
    if len(segment) > 2 and segment[0] == "[" and segment[-1] == "]":
        try:
            return int(segment[1:-1])
        except ValueError:
            return -1
    return -1


def set_path(node: Any, segments: Iterable[str], value: str) -> None:
    segs = list(segments)
    if node is None or not segs:
        return
    current = node
    for index, seg in enumerate(segs):
        last = index == len(segs) - 1
        if isinstance(current, dict):
            if last:
                current[seg] = value
                return
            current = current.get(seg)
        elif isinstance(current, list):
            idx = _parse_index(seg)
            if idx < 0 or idx >= len(current):
                return
            if last:
                current[idx] = value
                return
            current = current[idx]
        else:
            return


def apply_entries(root: Any, entries: Iterable[dict[str, Any]]) -> None:
    for entry in entries:
        segs = entry.get("segments")
        if segs:
            set_path(root, segs, entry.get("value", ""))
        elif entry.get("key"):
            set_path(root, str(entry["key"]).split("."), entry.get("value", ""))


def build_flat(entries: Iterable[dict[str, Any]]) -> dict[str, str]:
    obj: dict[str, str] = {}
    for entry in entries:
        key = entry.get("key")
        if key is None:
            continue
        obj[str(key)] = entry.get("value") or ""
    return obj


def clone(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: clone(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clone(v) for v in value]
    return value


LANG_NAME = re.compile(r"^[a-z]{2,3}_[a-z]{2,4}$")
