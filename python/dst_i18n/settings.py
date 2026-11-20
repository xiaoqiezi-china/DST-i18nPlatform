"""Persistent settings stored in %APPDATA%\\DstMcI18nTool\\settings.json."""

from __future__ import annotations

import json
import os
import threading
from typing import Any

from . import prompts

DEFAULT_MODEL = "deepseek-flash"
SUGGESTED_MODELS = ["deepseek-flash", "deepseek-v4-pro"]
LEGACY_MODELS = {"deepseek-chat", "deepseek-reasoner", "deepseek-coder"}

DEFAULTS: dict[str, Any] = {
    "LlmBaseUrl": "https://api.deepseek.com/v1",
    "LlmApiKey": "",
    "LlmModel": DEFAULT_MODEL,
    "LlmSystemPrompt": "",
    "LlmTemperature": 1.0,
    "LlmMaxTokens": 4096,
    "MtProvider": "auto",
    "MtEndpoint": "",
    "MtApiKey": "",
    "TargetLanguage": "zh_cn",
    "AskUserEnabled": True,
    "LastWorkspace": "",
    "RequestTimeoutSeconds": 120,
    "BatchSize": 24,
    "Concurrency": 4,
    "LastMap": "",
}

CAMEL = {
    "llmBaseUrl": "LlmBaseUrl",
    "llmApiKey": "LlmApiKey",
    "llmModel": "LlmModel",
    "llmSystemPrompt": "LlmSystemPrompt",
    "llmTemperature": "LlmTemperature",
    "llmMaxTokens": "LlmMaxTokens",
    "mtProvider": "MtProvider",
    "mtEndpoint": "MtEndpoint",
    "mtApiKey": "MtApiKey",
    "targetLanguage": "TargetLanguage",
    "askUserEnabled": "AskUserEnabled",
    "lastWorkspace": "LastWorkspace",
    "requestTimeoutSeconds": "RequestTimeoutSeconds",
    "batchSize": "BatchSize",
    "concurrency": "Concurrency",
    "lastMap": "LastMap",
}


def resolve_path() -> str:
    appdata = os.environ.get("APPDATA")
    if appdata:
        folder = os.path.join(appdata, "DstMcI18nTool")
    else:
        folder = os.path.join(os.path.expanduser("~"), "DstMcI18nTool")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        return os.path.join(os.getcwd(), "settings.json")
    return os.path.join(folder, "settings.json")


class SettingsStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.file_path = resolve_path()
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        data = dict(DEFAULTS)
        try:
            if os.path.isfile(self.file_path):
                with open(self.file_path, "r", encoding="utf-8-sig") as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    for key in DEFAULTS:
                        if key in loaded:
                            data[key] = loaded[key]
                            continue
                    for camel, pascal in CAMEL.items():
                        if camel in loaded:
                            data[pascal] = loaded[camel]
        except Exception:  # noqa: BLE001
            data = dict(DEFAULTS)
        model = str(data.get("LlmModel") or "").strip()
        if not model or model.lower() in LEGACY_MODELS:
            data["LlmModel"] = DEFAULT_MODEL
        return data

    def current(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def save(self, incoming: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._data
            base_url = _pick(incoming, "llmBaseUrl", "LlmBaseUrl", "")
            if str(base_url or "").strip():
                data["LlmBaseUrl"] = str(base_url).strip()

            api_key = _pick(incoming, "llmApiKey", "LlmApiKey", "")
            if str(api_key or "").strip():
                data["LlmApiKey"] = str(api_key).strip()

            model = _pick(incoming, "llmModel", "LlmModel", "")
            if str(model or "").strip():
                data["LlmModel"] = str(model).strip()

            data["LlmSystemPrompt"] = str(_pick(incoming, "llmSystemPrompt", "LlmSystemPrompt", "") or "")
            data["LlmTemperature"] = _clamp(_as_float(_pick(incoming, "llmTemperature", "LlmTemperature", 1.0)), 0, 2)
            data["LlmMaxTokens"] = _clamp(_as_int(_pick(incoming, "llmMaxTokens", "LlmMaxTokens", 4096)), 256, 32768)

            provider = _pick(incoming, "mtProvider", "MtProvider", "")
            if str(provider or "").strip():
                data["MtProvider"] = str(provider).strip()
            data["MtEndpoint"] = str(_pick(incoming, "mtEndpoint", "MtEndpoint", "") or "")
            mt_key = _pick(incoming, "mtApiKey", "MtApiKey", "")
            if str(mt_key or "").strip():
                data["MtApiKey"] = str(mt_key).strip()

            target = _pick(incoming, "targetLanguage", "TargetLanguage", "")
            if str(target or "").strip():
                data["TargetLanguage"] = str(target).strip()

            data["AskUserEnabled"] = bool(_pick(incoming, "askUserEnabled", "AskUserEnabled", True))
            data["RequestTimeoutSeconds"] = _clamp(
                _as_int(_pick(incoming, "requestTimeoutSeconds", "RequestTimeoutSeconds", 120)), 10, 600
            )
            data["BatchSize"] = _clamp(_as_int(_pick(incoming, "batchSize", "BatchSize", 24)), 1, 200)
            data["Concurrency"] = _clamp(
                _as_int(_pick(incoming, "concurrency", "Concurrency", 4)), 1, 8
            )

            last = _pick(incoming, "lastWorkspace", "LastWorkspace", "")
            if str(last or "").strip():
                data["LastWorkspace"] = str(last)
            last_map = _pick(incoming, "lastMap", "LastMap", "")
            if str(last_map or "").strip():
                data["LastMap"] = str(last_map)

            self._persist()
            return dict(data)

    def reset_prompt(self) -> dict[str, Any]:
        with self._lock:
            self._data["LlmSystemPrompt"] = ""
            self._persist()
            return dict(self._data)

    def set_last_workspace(self, path: str) -> None:
        with self._lock:
            self._data["LastWorkspace"] = path
            self._persist()

    def set_last_map(self, path: str) -> None:
        with self._lock:
            self._data["LastMap"] = path
            self._persist()

    def _persist(self) -> None:
        try:
            folder = os.path.dirname(self.file_path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            text = json.dumps(self._data, ensure_ascii=False, indent=2)
            with open(self.file_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
        except Exception:  # noqa: BLE001
            pass

    def to_dto(self) -> dict[str, Any]:
        with self._lock:
            data = dict(self._data)
        return {
            "llmBaseUrl": data["LlmBaseUrl"],
            "llmApiKey": "",
            "llmApiKeySet": bool(data["LlmApiKey"]),
            "llmModel": data["LlmModel"],
            "suggestedModels": list(SUGGESTED_MODELS),
            "llmSystemPrompt": data["LlmSystemPrompt"],
            "llmSystemPromptDefault": prompts.DEFAULT_SYSTEM_PROMPT,
            "llmTemperature": data["LlmTemperature"],
            "llmMaxTokens": data["LlmMaxTokens"],
            "mtProvider": data["MtProvider"],
            "mtEndpoint": data["MtEndpoint"],
            "mtApiKey": "",
            "mtApiKeySet": bool(data["MtApiKey"]),
            "targetLanguage": data["TargetLanguage"],
            "askUserEnabled": data["AskUserEnabled"],
            "requestTimeoutSeconds": data["RequestTimeoutSeconds"],
            "batchSize": data["BatchSize"],
            "concurrency": data["Concurrency"],
            "lastWorkspace": data["LastWorkspace"],
            "lastMap": data["LastMap"],
            "settingsPath": self.file_path,
        }


def _pick(source: dict[str, Any], camel: str, pascal: str, default: Any) -> Any:
    if camel in source and source[camel] is not None:
        return source[camel]
    if pascal in source and source[pascal] is not None:
        return source[pascal]
    return default


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 1.0


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _clamp(value: Any, low: int, high: int) -> Any:
    return max(low, min(high, value))
