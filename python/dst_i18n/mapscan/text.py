"""Extraction of literal text from Minecraft JSON text components."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .. import jsonutil, snbt


# Keys in a text component that carry identifiers, data paths, formatting or
# interaction metadata rather than display text.  ``hoverEvent`` is deliberately
# NOT here because its ``contents``/``value`` may contain a real text component.
NON_TEXT_KEYS = {
    "nbt",
    "storage",
    "selector",
    "score",
    "font",
    "color",
    "interpret",
    "separator",
    "insertion",
    "keybind",
    "click_event",
    "clickevent",
}

_CONTROL_CHAR = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PUNCT_ONLY = re.compile(r"^[\s\[\]\(\)\{\}<>+=|/\\.,:;!?~_\-–—…·•*#@&^%$'\"`]*$")
_PLACEHOLDER = re.compile(
    r"^(?:(?:%[0-9]*\$?[sd])|(?:\$\([^)]*\))|(?:\\u[0-9a-fA-F]{4})|[\s.,:;/\\-])+$"
)


@dataclass
class TextPart:
    text: str
    kind: str
    path: str
    key: str | None = None
    component: Any = None
    command: str | None = None
    owner: str | None = None


def is_noise_text(text: str) -> bool:
    """Reject technical glyphs, placeholders and punctuation-only UI strings."""
    if not isinstance(text, str):
        return True
    stripped = text.strip()
    if not stripped:
        return True
    if _CONTROL_CHAR.search(stripped):
        return True
    if stripped.startswith("\\u") and "$(" in stripped:
        return True
    if stripped.startswith("$(") and stripped.endswith(")"):
        return True
    if _PUNCT_ONLY.fullmatch(stripped):
        return True
    if _PLACEHOLDER.fullmatch(stripped):
        return True
    # Single glyphs are overwhelmingly custom-font icon codes (a, Ĳ, Ã...),
    # not prose.  Keep single CJK characters and digits.
    if len(stripped) == 1 and not ("\u4e00" <= stripped <= "\u9fff") and not stripped.isdigit():
        return True
    # Icons such as ≡, arrows and emoji are not localizable prose.
    if not any(ch.isalnum() or "\u4e00" <= ch <= "\u9fff" for ch in stripped):
        return True
    return False


def extract_text(value: Any, path: str = "") -> list[TextPart]:
    """Return literal strings and translate keys contained in a component.

    Besides the standard keys, nested dictionaries/lists are searched
    recursively so modded JSON such as ``{"foo":{"text":"bar"}}`` is not
    missed.  Plain strings under unknown keys are ignored by the nested walker
    to avoid treating IDs and metadata as display text.
    """
    if value is None:
        return []

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if looks_like_json(stripped):
            parsed, error = jsonutil.try_loads(stripped)
            if error is None and isinstance(parsed, (dict, list, str)):
                return extract_text(parsed, path)
            # Command arguments often contain SNBT such as
            # '{ translate: "item.minecraft...", color: "gray" }'.
            if stripped.startswith(("{", "[")):
                node, _raw, snbt_error = snbt.try_parse(stripped)
                if snbt_error is None and isinstance(node, (dict, list)):
                    return extract_text(node, path)
        return [TextPart(stripped, "literal", path, component=value)]

    if isinstance(value, list):
        parts: list[TextPart] = []
        for index, item in enumerate(value):
            parts.extend(extract_text(item, _index(path, index)))
        return parts

    if isinstance(value, dict):
        parts: list[TextPart] = []
        text = value.get("text")
        if isinstance(text, str) and text.strip():
            if looks_like_json(text):
                parts.extend(extract_text(text, _child(path, "text")))
            else:
                parts.append(TextPart(text, "literal", _child(path, "text"), component=value.get("text")))
        elif isinstance(text, (dict, list)):
            parts.extend(extract_text(text, _child(path, "text")))

        translate = value.get("translate")
        if isinstance(translate, str) and translate.strip():
            parts.append(
                TextPart(translate, "translate", _child(path, "translate"), key=translate, component=translate)
            )

        fallback = value.get("fallback")
        if isinstance(fallback, str) and fallback.strip():
            parts.append(TextPart(fallback, "fallback", _child(path, "fallback"), component=fallback))

        for key in ("extra", "with", "children"):
            child = value.get(key)
            if isinstance(child, list):
                parts.extend(extract_text(child, _child(path, key)))

        for key, child in value.items():
            if key in ("text", "translate", "fallback", "extra", "with", "children"):
                continue
            if key.lower() in NON_TEXT_KEYS:
                continue
            if isinstance(child, (dict, list)):
                parts.extend(_extract_nested(child, _child(path, key)))
            elif isinstance(child, str) and looks_like_json(child):
                parts.extend(extract_text(child, _child(path, key)))
        return parts

    return []


def _extract_nested(value: Any, path: str) -> list[TextPart]:
    if isinstance(value, str):
        if looks_like_json(value):
            return extract_text(value, path)
        return []
    if isinstance(value, list):
        parts: list[TextPart] = []
        for index, item in enumerate(value):
            parts.extend(_extract_nested(item, _index(path, index)))
        return parts
    if isinstance(value, dict):
        if any(key in value for key in ("text", "translate", "fallback", "extra", "with", "children")):
            return extract_text(value, path)
        parts: list[TextPart] = []
        for key, child in value.items():
            parts.extend(_extract_nested(child, _child(path, key)))
        return parts
    return []


def looks_like_json(text: str) -> bool:
    if text.startswith("{") or text.startswith("["):
        return True
    return len(text) >= 2 and text.startswith('"') and text.endswith('"')


def _child(path: str, key: str) -> str:
    return key if not path else path + "." + key


def _index(path: str, index: int) -> str:
    return "[" + str(index) + "]" if not path else path + "[" + str(index) + "]"
