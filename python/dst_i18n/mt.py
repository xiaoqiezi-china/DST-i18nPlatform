"""Machine translation providers: Google, Bing and a custom JSON endpoint."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import jsonutil

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class MtError(Exception):
    pass


def to_google_lang(lang: str) -> str:
    value = (lang or "").lower()
    mapping = {
        "zh_cn": "zh-CN",
        "zh-cn": "zh-CN",
        "zh": "zh-CN",
        "zh_tw": "zh-TW",
        "zh-tw": "zh-TW",
        "en_us": "en",
        "en": "en",
        "ja_jp": "ja",
        "ja": "ja",
        "ko_kr": "ko",
        "ko": "ko",
        "ru_ru": "ru",
        "ru": "ru",
        "de_de": "de",
        "de": "de",
        "fr_fr": "fr",
        "fr": "fr",
        "es_es": "es",
        "es": "es",
        "pt_br": "pt",
        "pt": "pt",
    }
    return mapping.get(value, (lang or "").replace("_", "-"))


def resolve_provider(settings: dict[str, Any], requested: str | None) -> str:
    provider = (requested or "").strip() or str(settings.get("MtProvider") or "")
    provider = (provider or "auto").strip().lower()
    if provider == "auto":
        provider = "custom" if str(settings.get("MtEndpoint") or "").strip() else "google"
    return provider


def translate(
    settings: dict[str, Any],
    provider: str | None,
    texts: list[str],
    source_language: str,
    target_language: str,
) -> list[str]:
    if not texts:
        return []
    resolved = resolve_provider(settings, provider)
    timeout = max(10, min(600, int(settings.get("RequestTimeoutSeconds") or 120)))

    if resolved == "google":
        return _google(texts, source_language, target_language, timeout)
    if resolved == "bing":
        return _bing(texts, source_language, target_language, timeout)
    if resolved == "custom":
        return _custom(settings, texts, source_language, target_language, timeout)
    raise MtError("未知的机翻提供方: " + resolved)


def _google(texts: list[str], source: str, target: str, timeout: int) -> list[str]:
    results: list[str] = []
    sl = to_google_lang(source)
    tl = to_google_lang(target)
    for text in texts:
        if not text or not text.strip():
            results.append(text)
            continue
        url = (
            "https://translate.googleapis.com/translate_a/single?client=gtx&dt=t"
            "&sl=" + urllib.parse.quote(sl, safe="")
            + "&tl=" + urllib.parse.quote(tl, safe="")
            + "&q=" + urllib.parse.quote(text, safe="")
        )
        try:
            body = _get_string(url, timeout)
            node = jsonutil.loads(body)
            collected: list[str] = []
            if isinstance(node, list) and node and isinstance(node[0], list):
                for pair in node[0]:
                    if isinstance(pair, list) and pair and isinstance(pair[0], str):
                        collected.append(pair[0])
            results.append("".join(collected) if collected else text)
        except Exception as exc:  # noqa: BLE001
            raise MtError(
                "Google 翻译失败（可能是网络无法访问 translate.googleapis.com）: " + str(exc)
            ) from exc
    return results


def _bing(texts: list[str], source: str, target: str, timeout: int) -> list[str]:
    results: list[str] = []
    sl = to_google_lang(source)
    tl = to_google_lang(target)
    for text in texts:
        if not text or not text.strip():
            results.append(text)
            continue
        url = (
            "https://api-edge.cognitive.microsofttranslator.com/translate"
            "?api-version=3.0&from=" + urllib.parse.quote(sl, safe="")
            + "&to=" + urllib.parse.quote(tl, safe="")
        )
        payload = json.dumps([{"Text": text}], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8-sig", errors="replace")
            node = jsonutil.loads(body)
            translated = None
            try:
                translated = node[0]["translations"][0]["text"]
            except (KeyError, IndexError, TypeError):
                translated = None
            results.append(translated if isinstance(translated, str) and translated else text)
        except urllib.error.HTTPError as exc:
            raise MtError("Bing 返回 HTTP " + str(exc.code)) from exc
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, MtError):
                raise
            raise MtError("Bing 翻译失败: " + str(exc)) from exc
    return results


def _custom(
    settings: dict[str, Any],
    texts: list[str],
    source: str,
    target: str,
    timeout: int,
) -> list[str]:
    endpoint = str(settings.get("MtEndpoint") or "").strip()
    if not endpoint:
        raise MtError("自定义机翻需要填写接口地址（设置 -> 机翻 -> 自定义接口）。")

    payload = {"texts": list(texts), "source": source, "target": target}
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8", "Accept": "application/json"}
    api_key = str(settings.get("MtApiKey") or "").strip()
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    request = urllib.request.Request(endpoint, data=data, method="POST", headers=headers)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8-sig", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8-sig", errors="replace")
        raise MtError("自定义机翻接口返回 HTTP " + str(exc.code) + ": " + detail[:200]) from exc
    except Exception as exc:  # noqa: BLE001
        raise MtError("自定义机翻请求失败: " + str(exc)) from exc

    node = jsonutil.loads(body)
    result = node.get("translations") if isinstance(node, dict) else None
    if result is None and isinstance(node, list):
        result = node
    if not isinstance(result, list):
        raise MtError("自定义机翻接口响应缺少 translations 数组")
    return [item if isinstance(item, str) else "" for item in result]


def _get_string(url: str, timeout: int) -> str:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8-sig", errors="replace")
    except urllib.error.HTTPError as exc:
        raise MtError("HTTP " + str(exc.code)) from exc
    except (socket.timeout, TimeoutError) as exc:
        raise MtError("请求超时") from exc
    except urllib.error.URLError as exc:
        raise MtError(str(getattr(exc, "reason", exc))) from exc


def test(settings: dict[str, Any], provider: str | None) -> tuple[bool, str, str]:
    resolved = resolve_provider(settings, provider)
    try:
        result = translate(settings, resolved, ["Hello world"], "en_us", "zh_cn")
        return True, resolved, "连接成功: Hello world -> " + (result[0] if result else "")
    except Exception as exc:  # noqa: BLE001
        return False, resolved, str(exc)
