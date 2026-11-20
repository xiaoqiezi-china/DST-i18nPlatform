"""Minimal SNBT reader/writer for FTB Quests language files."""

from __future__ import annotations

import re
from typing import Any


def _fmt_int(value: int) -> str:
    return str(value)


class _Parser:
    def __init__(self, text: str):
        self.text = text or ""
        self.pos = 0
        self.raw: dict[str, str] = {}

    def _skip_ws(self) -> None:
        n = len(self.text)
        while self.pos < n:
            ch = self.text[self.pos]
            if ch in " \t\r\n":
                self.pos += 1
                continue
            if ch == "/" and self.pos + 1 < n and self.text[self.pos + 1] == "/":
                while self.pos < n and self.text[self.pos] != "\n":
                    self.pos += 1
                continue
            break

    def _peek(self) -> str:
        self._skip_ws()
        return self.text[self.pos] if self.pos < len(self.text) else "\0"

    def parse_document(self) -> Any:
        node = self.parse_value("")
        self._skip_ws()
        return node

    def parse_value(self, path: str) -> Any:
        ch = self._peek()
        if ch == "{":
            return self.parse_compound(path)
        if ch == "[":
            return self.parse_list(path)
        if ch in "\"'":
            return self.parse_quoted()
        if ch == "\0":
            raise ValueError("Unexpected end of SNBT input at offset " + str(self.pos))
        return self.parse_bare(path)

    def parse_compound(self, path: str) -> dict[str, Any]:
        self._expect("{")
        obj: dict[str, Any] = {}
        while True:
            ch = self._peek()
            if ch == "}":
                self.pos += 1
                break
            if ch == "\0":
                raise ValueError("Unterminated compound")
            key = self.parse_quoted() if ch in "\"'" else self.parse_bare_key()
            self._skip_ws()
            self._expect(":")
            self._skip_ws()
            child_path = key if not path else path + "." + key
            value = self.parse_value(child_path)
            obj[key] = value
            self._skip_ws()
            if self.pos < len(self.text) and self.text[self.pos] == ",":
                self.pos += 1
                continue
            if self.pos < len(self.text) and self.text[self.pos] == "}":
                self.pos += 1
                break
            if self.pos >= len(self.text):
                raise ValueError("Unterminated compound")
        return obj

    def parse_list(self, path: str) -> list[Any]:
        self._expect("[")
        save = self.pos
        self._skip_ws()
        if (
            self.pos + 1 < len(self.text)
            and self.text[self.pos] in "BIL"
            and self.text[self.pos + 1] == ";"
        ):
            self.pos += 2
            arr: list[Any] = []
            while True:
                self._skip_ws()
                if self.pos < len(self.text) and self.text[self.pos] == "]":
                    self.pos += 1
                    break
                raw_value = self._read_bare_token()
                num, _is_float = _strip_number_suffix(raw_value)
                index = len(arr)
                child_path = path + "." + str(index)
                arr.append(_coerce_number(num))
                self.raw[child_path] = raw_value
                self._skip_ws()
                if self.pos < len(self.text) and self.text[self.pos] == ",":
                    self.pos += 1
                    continue
            return arr

        self.pos = save
        items: list[Any] = []
        while True:
            self._skip_ws()
            if self.pos < len(self.text) and self.text[self.pos] == "]":
                self.pos += 1
                break
            index = len(items)
            items.append(self.parse_value(path + "." + str(index)))
            self._skip_ws()
            if self.pos < len(self.text) and self.text[self.pos] == ",":
                self.pos += 1
                continue
            if self.pos < len(self.text) and self.text[self.pos] == "]":
                self.pos += 1
                break
            if self.pos >= len(self.text):
                raise ValueError("Unterminated list")
        return items

    def parse_bare_key(self) -> str:
        self._skip_ws()
        start = self.pos
        n = len(self.text)
        while self.pos < n and self.text[self.pos] not in ":{}":
            if self.text[self.pos] in " \t\r\n,":
                break
            self.pos += 1
        if self.pos == start:
            raise ValueError("Expected compound key at offset " + str(self.pos))
        return self.text[start:self.pos].strip()

    def parse_bare(self, path: str) -> Any:
        token = self._read_bare_token()
        if not token:
            raise ValueError("Unexpected token at offset " + str(self.pos))
        if token.lower() == "true":
            return True
        if token.lower() == "false":
            return False
        num_text, is_float = _strip_number_suffix(token)
        if is_float:
            try:
                value = float(num_text)
            except ValueError:
                return token
            self.raw[path] = token
            return value
        try:
            value_int = int(num_text)
        except ValueError:
            return token
        self.raw[path] = token
        return value_int

    def _read_bare_token(self) -> str:
        self._skip_ws()
        start = self.pos
        n = len(self.text)
        while self.pos < n:
            ch = self.text[self.pos]
            if ch in " \t\r\n,]}:{[":
                break
            self.pos += 1
        return self.text[start:self.pos]

    def parse_quoted(self) -> str:
        self._skip_ws()
        quote = self.text[self.pos]
        self.pos += 1
        parts: list[str] = []
        n = len(self.text)
        while self.pos < n:
            ch = self.text[self.pos]
            self.pos += 1
            if ch == quote:
                return "".join(parts)
            if ch == "\\" and self.pos < n:
                esc = self.text[self.pos]
                self.pos += 1
                if esc == "n":
                    parts.append("\n")
                elif esc == "r":
                    parts.append("\r")
                elif esc == "t":
                    parts.append("\t")
                elif esc == "b":
                    parts.append("\b")
                elif esc == "f":
                    parts.append("\f")
                elif esc == "u":
                    if self.pos + 4 <= n:
                        try:
                            code = int(self.text[self.pos:self.pos + 4], 16)
                            parts.append(chr(code))
                            self.pos += 4
                        except ValueError:
                            pass
                else:
                    parts.append(esc)
                continue
            parts.append(ch)
        raise ValueError("Unterminated string")

    def _expect(self, ch: str) -> None:
        self._skip_ws()
        if self.pos >= len(self.text) or self.text[self.pos] != ch:
            raise ValueError("Expected '" + ch + "' at offset " + str(self.pos))
        self.pos += 1


def _strip_number_suffix(token: str) -> tuple[str, bool]:
    if not token:
        return token, False
    last = token[-1]
    if last in "bBsSlL":
        return token[:-1], False
    if last in "fFdD":
        return token[:-1], True
    return token, ("." in token or "e" in token or "E" in token)


def _coerce_number(text: str) -> Any:
    try:
        return float(text) if ("." in text or "e" in text or "E" in text) else int(text)
    except ValueError:
        return text


def try_parse(text: str) -> tuple[Any, dict[str, str], str | None]:
    parser = _Parser(text)
    try:
        root = parser.parse_document()
        return root, parser.raw, None
    except Exception as exc:  # noqa: BLE001
        return None, {}, str(exc)


def quote(value: str) -> str:
    out = ['"']
    for ch in value:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\f":
            out.append("\\f")
        elif ord(ch) < 0x20:
            out.append("\\u" + format(ord(ch), "04x"))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def serialize(root: Any, raw: dict[str, str] | None = None) -> str:
    return _write(root, raw or {}, 0, "")


def _write(node: Any, raw: dict[str, str], depth: int, path: str) -> str:
    if isinstance(node, dict):
        if not node:
            return "{}"
        parts = ["{"]
        first = True
        for key, value in node.items():
            if not first:
                parts.append(",")
            first = False
            parts.append("\n")
            parts.append("\t" * (depth + 1))
            parts.append(quote(str(key)))
            parts.append(": ")
            child_path = str(key) if not path else path + "." + str(key)
            parts.append(_write(value, raw, depth + 1, child_path))
        parts.append("\n")
        parts.append("\t" * depth)
        parts.append("}")
        return "".join(parts)

    if isinstance(node, list):
        if not node:
            return "[]"
        parts = ["["]
        for index, value in enumerate(node):
            if index > 0:
                parts.append(",")
            parts.append("\n")
            parts.append("\t" * (depth + 1))
            parts.append(_write(value, raw, depth + 1, path + "." + str(index)))
        parts.append("\n")
        parts.append("\t" * depth)
        parts.append("]")
        return "".join(parts)

    if isinstance(node, str):
        return quote(node)

    if path in raw:
        return raw[path]

    if isinstance(node, bool):
        return "1b" if node else "0b"
    if isinstance(node, int):
        return str(node) + "L"
    if isinstance(node, float):
        return repr(node) + "d"
    return "{}"
