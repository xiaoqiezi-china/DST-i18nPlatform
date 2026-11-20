"""Text extraction from arbitrary NBT structures.

The scanner deliberately does two things:

* follow values that are clearly text (signs, custom names, book pages, item
  components), including plain strings;
* try to parse every string that looks like JSON and every compound that
  contains text-component keys, so modded or unknown fields are not missed.

Minecraft NBT is SNBT, not JSON: item components in commands may look like
``{"minecraft:custom_name":{text:"...",italic:false}}``.  Therefore the walker
must inspect dictionaries as well as strings.
"""

from __future__ import annotations

import re
from typing import Any

from .text import NON_TEXT_KEYS, TextPart, extract_text, looks_like_json

TEXT_COMPONENT_KEYS = {"text", "translate", "fallback", "extra", "with", "children"}

# Keys whose string value is usually display text, even when it is not JSON.
PLAIN_TEXT_KEYS = {
    "customname",
    "custom_name",
    "item_name",
    "displayname",
    "title",
    "lore",
    "pages",
    "messages",
    "text1",
    "text2",
    "text3",
    "text4",
}

ITEM_COMPONENT_SUFFIXES = (":custom_name", ":item_name", ":lore")
COMMAND_RE = re.compile(
    r"^(?:minecraft:)?(tellraw|title|say|me|teammsg|msg|tell|w|kick|bossbar|team|scoreboard|summon|give|item|setblock|fill|data|execute)\b",
    re.IGNORECASE,
)


def collect_nbt_text(
    node: Any,
    path: str = "$",
    hits: list[TextPart] | None = None,
    command_parser=None,
    depth: int = 0,
    command: str | None = None,
) -> list[TextPart]:
    if hits is None:
        hits = []
    seen: set[tuple[str, str, str]] = set()
    if command_parser is None:
        from .commands import CommandParser

        command_parser = CommandParser()
    _walk(node, path, hits, seen, command_parser, depth, command)
    return hits


def _add_part(part: TextPart, hits: list[TextPart], seen: set[tuple[str, str, str]], owner: str | None, command: str | None) -> None:
    if not part.text.strip():
        return
    part.owner = owner
    if command and not part.command:
        part.command = command
    key = (part.path, part.text, part.kind)
    if key in seen:
        return
    seen.add(key)
    hits.append(part)


def _append_component(
    value: Any,
    path: str,
    hits: list[TextPart],
    seen: set[tuple[str, str, str]],
    owner: str | None,
    command: str | None,
) -> None:
    for part in extract_text(value, path):
        _add_part(part, hits, seen, owner, command)


def _walk(
    node: Any,
    path: str,
    hits: list[TextPart],
    seen: set[tuple[str, str, str]],
    command_parser,
    depth: int,
    command: str | None,
) -> None:
    if isinstance(node, list):
        for index, value in enumerate(node):
            _walk(value, path + "[" + str(index) + "]", hits, seen, command_parser, depth, command)
        return

    if not isinstance(node, dict):
        return

    owner = node.get("id") if isinstance(node.get("id"), str) else None
    handled: set[str] = set()

    # Commands are not text components; hand them to the independent parser.
    command_value = node.get("Command")
    if isinstance(command_value, str):
        handled.add("Command")
        _add_command(command_value, path + ".Command", hits, seen, command_parser, depth, owner)

    # A compound such as {text:"...", color:"red"} is a text component.
    if any(key in node for key in TEXT_COMPONENT_KEYS):
        _append_component(node, path, hits, seen, owner, command)
        handled.update(TEXT_COMPONENT_KEYS)

    for key, value in node.items():
        lower = key.lower()
        if key in handled:
            continue
        if lower in NON_TEXT_KEYS:
            # In a structure file an entity's ``nbt`` field is a compound that
            # contains the actual entity data; in a text component it is only a
            # data-path string.  Keep walking the compound form.
            if lower == "nbt" and isinstance(value, (dict, list)):
                pass
            else:
                continue

        if lower == "display" and isinstance(value, dict):
            handled.add(key)
            for display_key, display_value in value.items():
                dlower = display_key.lower()
                if dlower in ("name", "lore"):
                    _collect_value(display_value, path + "." + key + "." + display_key, hits, seen, owner, command)
            _walk(value, path + "." + key, hits, seen, command_parser, depth, command)
            continue

        if lower in ("front_text", "back_text") and isinstance(value, dict):
            handled.add(key)
            messages = value.get("messages")
            if isinstance(messages, list):
                _collect_value(messages, path + "." + key + ".messages", hits, seen, owner, command)
            _walk(value, path + "." + key, hits, seen, command_parser, depth, command)
            continue

        if lower == "components" and isinstance(value, dict):
            handled.add(key)
            for component_key, component_value in value.items():
                _collect_item_component(
                    component_value,
                    path + "." + key + "." + component_key,
                    hits,
                    seen,
                    owner,
                    command,
                )
            _walk(value, path + "." + key, hits, seen, command_parser, depth, command)
            continue

        if lower == "pages" and isinstance(value, list):
            handled.add(key)
            _collect_pages(value, path + "." + key, hits, seen, owner, command)
            continue

        # Plain display fields even when the value is not JSON.
        if lower in PLAIN_TEXT_KEYS or lower.endswith(ITEM_COMPONENT_SUFFIXES):
            handled.add(key)
            _collect_value(value, path + "." + key, hits, seen, owner, command)
            if isinstance(value, (dict, list)):
                _walk(value, path + "." + key, hits, seen, command_parser, depth, command)
            continue

        # Catch JSON text hidden in arbitrary keys (including modded NBT).
        if isinstance(value, str):
            if looks_like_json(value):
                _collect_value(value, path + "." + key, hits, seen, owner, command)
            continue

        if isinstance(value, (dict, list)):
            _walk(value, path + "." + key, hits, seen, command_parser, depth, command)


def _collect_item_component(
    value: Any,
    path: str,
    hits: list[TextPart],
    seen: set[tuple[str, str, str]],
    owner: str | None,
    command: str | None,
) -> None:
    if not isinstance(value, dict):
        _append_component(value, path, hits, seen, owner, command)
        return

    title = value.get("title")
    if title is not None:
        _append_component(title, path + ".title", hits, seen, owner, command)

    pages = value.get("pages")
    if isinstance(pages, list):
        _collect_pages(pages, path + ".pages", hits, seen, owner, command)
        return

    _append_component(value, path, hits, seen, owner, command)


def _collect_pages(
    pages: list[Any],
    path: str,
    hits: list[TextPart],
    seen: set[tuple[str, str, str]],
    owner: str | None,
    command: str | None,
) -> None:
    for index, page in enumerate(pages):
        page_path = path + "[" + str(index) + "]"
        if isinstance(page, dict):
            raw = page.get("raw")
            if isinstance(raw, str):
                _append_component(raw, page_path + ".raw", hits, seen, owner, command)
            _append_component(page, page_path, hits, seen, owner, command)
        else:
            _append_component(page, page_path, hits, seen, owner, command)


def _collect_value(
    value: Any,
    path: str,
    hits: list[TextPart],
    seen: set[tuple[str, str, str]],
    owner: str | None,
    command: str | None,
) -> None:
    if isinstance(value, str):
        if not value.strip():
            return
        if looks_like_json(value):
            _append_component(value, path, hits, seen, owner, command)
        else:
            _add_part(TextPart(value.strip(), "literal", path, component=value), hits, seen, owner, command)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _collect_value(item, path + "[" + str(index) + "]", hits, seen, owner, command)
        return
    if isinstance(value, dict):
        _append_component(value, path, hits, seen, owner, command)


def _add_command(
    value: str,
    path: str,
    hits: list[TextPart],
    seen: set[tuple[str, str, str]],
    command_parser,
    depth: int,
    owner: str | None,
) -> None:
    stripped = value.strip()
    if not stripped:
        return
    # Keep the command itself as context, but do not emit it as localizable text.
    hits.append(TextPart(stripped, "command", path, command=stripped, owner=owner))
    if COMMAND_RE.match(stripped.lstrip("/")):
        command_parser.extract_into(stripped, path, hits, depth + 1)
