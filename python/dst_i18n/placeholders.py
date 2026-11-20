"""Protect placeholders/format codes while translating."""

from __future__ import annotations

import re

TOKEN_RE = re.compile(
    r"%(\d+\$)?[-+ 0#]*(\d+)?(\.\d+)?[sdfxXoObBcC%]|\{\d+\}|§[0-9a-fk-orA-FK-OR]|\\n|\$\{[^}]+\}"
)


def extract(text: str) -> tuple[str, list[str]]:
    tokens: list[str] = []

    def repl(match: re.Match[str]) -> str:
        tokens.append(match.group(0))
        return "【" + str(len(tokens) - 1) + "】"

    return TOKEN_RE.sub(repl, text), tokens


def restore(translated: str, tokens: list[str]) -> str:
    if not tokens:
        return translated
    text = translated
    for index, token in enumerate(tokens):
        marker = "【" + str(index) + "】"
        alt = "[[" + str(index) + "]]"
        spaced = "【" + str(index) + " 】"
        if marker in text:
            text = text.replace(marker, token)
        elif alt in text:
            text = text.replace(alt, token)
        elif spaced in text:
            text = text.replace(spaced, token)
        else:
            text = text + " " + token
    return text


def is_trivial(value: str | None) -> bool:
    if not value or not value.strip():
        return True
    stripped = TOKEN_RE.sub("", value)
    return not stripped.strip()
