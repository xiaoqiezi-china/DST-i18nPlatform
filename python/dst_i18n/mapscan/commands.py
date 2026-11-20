"""Minecraft command text extractor shared by functions and command blocks."""

from __future__ import annotations

from typing import Any

from .. import snbt
from . import nbt_text
from .text import TextPart, extract_text


class _Tokenizer:
    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.size = len(text)

    def tokens(self) -> list[str]:
        result: list[str] = []
        buffer: list[str] = []
        stack: list[str] = []
        quote: str | None = None
        while self.pos < self.size:
            ch = self.text[self.pos]
            if quote is not None:
                buffer.append(ch)
                if ch == "\\" and self.pos + 1 < self.size:
                    self.pos += 1
                    buffer.append(self.text[self.pos])
                elif ch == quote:
                    quote = None
                self.pos += 1
                continue

            if ch in ("'", '"'):
                quote = ch
                buffer.append(ch)
            elif ch in "{[(":
                stack.append(ch)
                buffer.append(ch)
            elif ch in "}])":
                if stack:
                    stack.pop()
                buffer.append(ch)
            elif ch.isspace() and not stack:
                if buffer:
                    result.append("".join(buffer))
                    buffer = []
            else:
                buffer.append(ch)
            self.pos += 1

        if buffer:
            result.append("".join(buffer))
        return result


class CommandParser:
    """Parse one command and return every localizable text component."""

    def extract(self, command: str, base_path: str = "") -> list[TextPart]:
        hits: list[TextPart] = []
        self.extract_into(command, base_path, hits, 0)
        return hits

    def extract_into(self, command: str, base_path: str, hits: list[TextPart], depth: int = 0) -> None:
        if depth > 8:
            return
        text = (command or "").strip()
        if not text:
            return
        if text.startswith("/"):
            text = text[1:].strip()

        tokens = _Tokenizer(text).tokens()
        if not tokens:
            return

        name = tokens[0].lstrip("/").split(":")[-1].lower()
        path = base_path or "$"

        if name == "execute":
            self._extract_execute(tokens, command, path, hits, depth)
            return

        if name == "tellraw":
            self._add_component(" ".join(tokens[2:]), path + ".tellraw.message", hits, command)
        elif name == "title":
            action = tokens[2] if len(tokens) > 2 else "title"
            self._add_component(" ".join(tokens[3:]), path + ".title." + action, hits, command)
        elif name in ("say", "me", "teammsg"):
            self._add_component(" ".join(tokens[1:]), path + "." + name + ".message", hits, command)
        elif name in ("msg", "tell", "w"):
            self._add_component(" ".join(tokens[2:]), path + "." + name + ".message", hits, command)
        elif name == "kick":
            self._add_component(" ".join(tokens[2:]), path + ".kick.reason", hits, command)
        elif name == "bossbar" and len(tokens) > 3 and tokens[1].lower() == "add":
            self._add_component(" ".join(tokens[3:]), path + ".bossbar.name", hits, command)
        elif name == "bossbar" and len(tokens) > 3 and tokens[1].lower() == "set":
            self._add_after_key(tokens, "name", path + ".bossbar.name", hits, command)
        elif name == "team" and len(tokens) > 3 and tokens[1].lower() == "add":
            self._add_component(" ".join(tokens[3:]), path + ".team.displayName", hits, command)
        elif name == "team" and len(tokens) > 3 and tokens[1].lower() == "modify":
            self._add_after_key(tokens, "displayname", path + ".team.displayName", hits, command)
        elif name == "scoreboard" and len(tokens) > 4 and tokens[1].lower() == "teams":
            if tokens[2].lower() == "add":
                self._add_component(" ".join(tokens[4:]), path + ".scoreboard.displayName", hits, command)
            elif tokens[2].lower() == "modify":
                self._add_after_key(tokens, "displayname", path + ".scoreboard.displayName", hits, command)
        elif name == "scoreboard" and len(tokens) > 5 and tokens[1].lower() == "objectives":
            if tokens[2].lower() == "add":
                self._add_component(" ".join(tokens[5:]), path + ".scoreboard.objective.displayName", hits, command)
            elif tokens[2].lower() == "modify":
                self._add_after_key(tokens, "displayname", path + ".scoreboard.objective.displayName", hits, command)

        if name in ("summon", "give", "item", "setblock", "fill", "data"):
            self._scan_nbt(tokens, 1, path, hits, command, depth)

    def _extract_execute(
        self, tokens: list[str], command: str, path: str, hits: list[TextPart], depth: int
    ) -> None:
        for index, token in enumerate(tokens):
            if token.lower() == "run":
                nested = " ".join(tokens[index + 1:])
                if nested:
                    self.extract_into(nested, path + ".execute.run", hits, depth + 1)
                return

    def _scan_nbt(
        self,
        tokens: list[str],
        start: int,
        base_path: str,
        hits: list[TextPart],
        command: str,
        depth: int,
    ) -> None:
        for index in range(start, len(tokens)):
            token = tokens[index]
            brace = token.find("{")
            if brace < 0:
                continue
            fragment = token[brace:]
            node, _raw, error = snbt.try_parse(fragment)
            if error is not None:
                continue
            nbt_text.collect_nbt_text(
                node,
                base_path + ".arg[" + str(index) + "]",
                hits,
                command_parser=self,
                depth=depth + 1,
                command=command,
            )

    def _add_after_key(
        self,
        tokens: list[str],
        key: str,
        path: str,
        hits: list[TextPart],
        command: str,
    ) -> None:
        for index, token in enumerate(tokens):
            if token.lower() == key and index + 1 < len(tokens):
                self._add_component(" ".join(tokens[index + 1:]), path, hits, command)
                return

    def _add_component(self, raw: str, path: str, hits: list[TextPart], command: str) -> None:
        raw = (raw or "").strip()
        if not raw:
            return
        for part in extract_text(raw, path):
            part.command = command
            hits.append(part)


def looks_like_nbt(value: str) -> bool:
    return isinstance(value, str) and "{" in value and "}" in value


def parse_nbt_fragment(value: str) -> Any | None:
    brace = value.find("{")
    if brace < 0:
        return None
    node, _raw, error = snbt.try_parse(value[brace:])
    return None if error is not None else node
