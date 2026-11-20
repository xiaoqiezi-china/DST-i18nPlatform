"""System prompt defaults and prompt rendering."""

from __future__ import annotations

from typing import Any

TARGET_LANGUAGE_TOKEN = "{{targetLanguage}}"
GLOSSARY_TOKEN = "{{glossary}}"
ASK_USER_TOKEN = "{{askUser}}"

DEFAULT_SYSTEM_PROMPT = (
    "You are a professional game localization engine for Minecraft mods.\n"
    "Translate the given JSON object values from English into " + TARGET_LANGUAGE_TOKEN + " (Simplified Chinese).\n"
    "\n"
    "Rules:\n"
    "1. Return ONLY a JSON object. Keys must be exactly the same as the input. No markdown, no comments.\n"
    "2. Translate the VALUES only. Never translate, remove or reorder placeholder tokens such as %s, %1$s, {0}, or curly-brace placeholders, and keep formatting codes (section sign followed by a letter) untouched.\n"
    "3. Keep escaped sequences such as \n intact.\n"
    "4. Use the terminology Chinese players actually use (Redstone -> 红石, Nether -> 下界, Crafting Table -> 工作台).\n"
    "5. Keep translations concise; UI space is limited.\n"
    "6. If a value is already Chinese or is a pure token/number, return it unchanged.\n"
    "7. If a key/value pair defines an item ID, or references another component or other non-text content (for example a registry id, a tag, a translation key reference, a resource location, a command, a config path), do not translate or modify it — return the original value unchanged.\n"
    + GLOSSARY_TOKEN + "\n"
    + ASK_USER_TOKEN
)

ASK_USER_SECTION = (
    "If a term is a highly specialized domain word whose translation you cannot determine with confidence,\n"
    "you may instead return: {\"__ask__\": {\"question\": \"...\", \"terms\": [\"...\"], \"options\": [\"...\", \"...\"]}}\n"
    "and the user will answer. Use this sparingly (at most once per batch)."
)

DEFAULT_USER_PROMPT = (
    "Translate the following Minecraft mod language entries from {{sourceLanguage}} to {{targetLanguage}}. "
    "Keep every key unchanged. Respond with a JSON object that maps the exact key to the translated text, "
    "for example {\"item.example.demo\": \"示例\"}. Do not wrap the JSON in markdown code fences."
)


def build_glossary(glossary: dict[str, str] | None) -> str:
    if not glossary:
        return ""
    lines = ["Glossary (must be followed exactly):"]
    for key, value in glossary.items():
        lines.append("- " + str(key) + " => " + str(value))
    return "\n".join(lines)


def render(
    user_prompt: str | None,
    target_language: str,
    glossary: dict[str, str] | None,
    ask_user: bool,
) -> str:
    template = user_prompt if user_prompt and user_prompt.strip() else DEFAULT_SYSTEM_PROMPT
    text = template.replace(TARGET_LANGUAGE_TOKEN, target_language)

    glossary_text = build_glossary(glossary)
    if GLOSSARY_TOKEN in text:
        text = text.replace(GLOSSARY_TOKEN, glossary_text)
    elif glossary_text:
        text += "\n" + glossary_text

    ask_text = ASK_USER_SECTION if ask_user else ""
    if ASK_USER_TOKEN in text:
        text = text.replace(ASK_USER_TOKEN, ask_text)
    elif ask_text:
        text += "\n" + ask_text

    return text.rstrip() + "\n"


__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "DEFAULT_USER_PROMPT",
    "ASK_USER_SECTION",
    "render",
]
