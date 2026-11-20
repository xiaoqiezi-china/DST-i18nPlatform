"""HTTP API router mirroring the original ASP.NET Core endpoints."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.parse
from typing import Any

from . import APP_NAME, APP_VERSION, assets, documents, exports, jsonutil, llm, mapscan_service, mt, native_picker, translate

STARTED_AT = int(time.time() * 1000)


class ApiResponse:
    def __init__(self, status: int = 200, body: bytes = b"", content_type: str = "", headers: dict[str, str] | None = None):
        self.status = status
        self.body = body
        self.content_type = content_type
        self.headers = headers or {}


class Api:
    def __init__(self, session: Any, settings: Any) -> None:
        self.session = session
        self.settings = settings
        self.exports = exports.ExportService(session)
        self.engine = translate.TranslationEngine()
        self.assets = assets.Assets()
        self.maps = mapscan_service.MapScanService()
        self._picker_lock = threading.Lock()

    # ----- dispatch -----------------------------------------------------
    def handle(self, method: str, raw_path: str, query: str, body: bytes) -> ApiResponse:
        path = urllib.parse.unquote(raw_path)
        params = _parse_query(query)
        try:
            if method in ("GET", "HEAD"):
                response = self._get(path, params)
            elif method == "POST":
                response = self._post(path, params, body)
            else:
                response = _error(405, "请求方法不支持")
        except Exception as exc:  # noqa: BLE001
            return _error(400, str(exc))
        if method == "HEAD":
            response.body = b""
        return response

    def _get(self, path: str, params: dict[str, str]) -> ApiResponse:
        if path == "/api/health":
            return _json(
                {
                    "ok": True,
                    "name": APP_NAME,
                    "version": APP_VERSION,
                    "pid": os.getpid(),
                    "startedAt": STARTED_AT,
                }
            )
        if path == "/api/settings":
            return _json(self.settings.to_dto())
        if path == "/api/workspace":
            query_path = params.get("path")
            info = self.session.current()
            if not info.get("exists") and query_path:
                info = self.session.get_or_open(query_path)
            info["scanId"] = self.session.active_scan_id()
            return _json(info)
        if path.startswith("/api/workspace/scan/") and path.count("/") == 4:
            scan_id = path.rsplit("/", 1)[1]
            progress = self.session.get_scan(scan_id)
            if progress is None:
                return _error(404, "扫描任务不存在或已过期")
            return _json(progress)
        if path.startswith("/api/map/scan/"):
            parts = path.strip("/").split("/")
            if len(parts) == 5 and parts[0] == "api" and parts[1] == "map" and parts[2] == "scan" and parts[4] == "result":
                scan_id = parts[3]
                result = self.maps.result(scan_id)
                if result is None:
                    return _error(404, "地图扫描结果不存在、尚未完成或已过期")
                return _json(result)
            if len(parts) == 4 and parts[0] == "api" and parts[1] == "map" and parts[2] == "scan":
                scan_id = parts[3]
                progress = self.maps.get(scan_id)
                if progress is None:
                    return _error(404, "地图扫描任务不存在或已过期")
                return _json(progress)
        if path == "/api/document":
            kind = params.get("kind")
            if not kind:
                return _error(400, "缺少 kind 参数")
            query = {
                "kind": kind,
                "file": params.get("file"),
                "namespace": params.get("ns") or params.get("namespace"),
                "lang": params.get("lang"),
                "path": params.get("path"),
            }
            doc = documents.load(self.session, query)
            return _json(doc["dto"])
        if path.startswith("/api/translate/"):
            job_id = path[len("/api/translate/"):]
            if "/" in job_id:
                return _error(404, "接口不存在: " + path)
            job = self.engine.get(job_id)
            if job is None:
                return _error(404, "任务不存在或已过期")
            return _json(job)
        return self._static(path)

    def _post(self, path: str, params: dict[str, str], body: bytes) -> ApiResponse:
        data = _read_json(body)

        if path == "/api/settings":
            if not isinstance(data, dict):
                return _error(400, "请求体不是合法的设置对象")
            self.settings.save(data)
            return _json(self.settings.to_dto())
        if path == "/api/settings/reset-prompt":
            self.settings.reset_prompt()
            return _json(self.settings.to_dto())
        if path == "/api/settings/test-llm":
            ok, message = llm.test(self.settings.current())
            return _json({"ok": ok, "model": self.settings.current().get("LlmModel"), "message": message})
        if path == "/api/settings/test-mt":
            provider = data.get("provider") if isinstance(data, dict) else None
            ok, resolved, message = mt.test(self.settings.current(), provider)
            return _json({"ok": ok, "provider": resolved, "message": message})

        if path == "/api/workspace/scan":
            scan_path = data.get("path") if isinstance(data, dict) else None
            if not scan_path or not str(scan_path).strip():
                return _error(400, "缺少 path")
            try:
                state = self.session.start_scan(str(scan_path))
            except Exception as exc:  # noqa: BLE001
                if hasattr(exc, "candidates"):
                    return _json(
                        {"error": str(exc), "valid": False, "candidates": list(getattr(exc, "candidates"))},
                        status=400,
                    )
                if "已有扫描正在进行" in str(exc):
                    return _json({"error": str(exc), "valid": True}, status=409)
                return _json({"error": str(exc), "valid": False, "candidates": []})
            try:
                self.settings.set_last_workspace(state["path"])
            except Exception:  # noqa: BLE001
                pass
            return _json(
                {
                    "scanId": state["scanId"],
                    "path": state["path"],
                    "name": state["name"],
                    "modsDir": os.path.join(state["path"], "mods") if os.path.isdir(os.path.join(state["path"], "mods")) else None,
                    "kubejsDir": os.path.join(state["path"], "kubejs") if os.path.isdir(os.path.join(state["path"], "kubejs")) else None,
                    "configDir": os.path.join(state["path"], "config") if os.path.isdir(os.path.join(state["path"], "config")) else None,
                    "candidates": [],
                }
            )
        if path.startswith("/api/workspace/scan/") and path.endswith("/cancel"):
            scan_id = path[len("/api/workspace/scan/"):-len("/cancel")].strip("/")
            return _json({"ok": self.session.cancel_scan(scan_id)})

        if path == "/api/map/scan":
            map_path = data.get("path") if isinstance(data, dict) else None
            if not map_path or not str(map_path).strip():
                return _error(400, "缺少 path")
            include_world = bool(data.get("includeWorld", True)) if isinstance(data, dict) else True
            include_packs = bool(data.get("includePacks", True)) if isinstance(data, dict) else True
            scan_kind = str(data.get("kind") or "map").strip().lower() if isinstance(data, dict) else "map"
            if scan_kind not in ("map", "resourcepack"):
                return _error(400, "不支持的扫描类型：" + scan_kind)
            try:
                progress = self.maps.start(
                    str(map_path),
                    include_world=include_world,
                    include_packs=include_packs,
                    kind=scan_kind,
                )
            except Exception as exc:  # noqa: BLE001
                return _json({"error": str(exc), "valid": False}, status=400)
            try:
                if hasattr(self.settings, "set_last_map"):
                    self.settings.set_last_map(progress["path"])
            except Exception:  # noqa: BLE001
                pass
            return _json(progress)
        if path.startswith("/api/map/scan/") and path.endswith("/cancel"):
            scan_id = path[len("/api/map/scan/"):-len("/cancel")].strip("/")
            return _json({"ok": self.maps.cancel(scan_id)})
        if path == "/api/map/reveal":
            scan_id = data.get("scanId") if isinstance(data, dict) else None
            file = data.get("file") if isinstance(data, dict) else None
            if not scan_id or not file:
                return _error(400, "缺少 scanId 或 file")
            try:
                return _json(self.maps.reveal(str(scan_id), str(file)))
            except Exception as exc:  # noqa: BLE001
                return _json({"ok": False, "error": str(exc)}, status=400)

        if path == "/api/workspace/open":
            open_path = data.get("path") if isinstance(data, dict) else None
            if not open_path or not str(open_path).strip():
                return _error(400, "缺少 path")
            info = self.session.open(str(open_path))
            if info.get("exists"):
                self.settings.set_last_workspace(info["path"])
            info["scanId"] = None
            return _json(info)
        if path == "/api/workspace/refresh":
            current = self.session.current_path()
            if not current:
                return _error(400, "尚未打开工作目录")
            info = self.session.open(current)
            info["scanId"] = None
            return _json(info)
        if path == "/api/workspace/pick":
            if not self._picker_lock.acquire(blocking=False):
                return _error(409, "系统文件夹选择框已经打开")
            try:
                initial = data.get("initial") if isinstance(data, dict) else ""
                description = data.get("description") if isinstance(data, dict) else ""
                selected = native_picker.pick_folder(
                    str(initial or ""),
                    str(description or "选择文件夹"),
                )
                if not selected:
                    return _json({"path": None, "cancelled": True})
                return _json({"path": selected, "cancelled": False})
            except Exception as exc:  # noqa: BLE001
                return _json({"path": None, "cancelled": False, "error": str(exc)}, status=500)
            finally:
                self._picker_lock.release()
        if path == "/api/workspace/pick-file":
            if not self._picker_lock.acquire(blocking=False):
                return _error(409, "系统文件选择框已经打开")
            try:
                initial = data.get("initial") if isinstance(data, dict) else ""
                description = data.get("description") if isinstance(data, dict) else ""
                filter_text = data.get("filter") if isinstance(data, dict) else ""
                selected = native_picker.pick_file(
                    str(initial or ""),
                    str(description or "选择文件"),
                    str(filter_text or ""),
                )
                if not selected:
                    return _json({"path": None, "cancelled": True})
                return _json({"path": selected, "cancelled": False})
            except Exception as exc:  # noqa: BLE001
                return _json({"path": None, "cancelled": False, "error": str(exc)}, status=500)
            finally:
                self._picker_lock.release()
        if path == "/api/workspace/reveal":
            reveal_path = data.get("path") if isinstance(data, dict) else None
            if not reveal_path or not str(reveal_path).strip():
                return _error(400, "缺少 path")
            try:
                target = os.path.normpath(str(reveal_path))
                if os.path.isfile(target):
                    subprocess.Popen(["explorer.exe", "/select," + target])
                else:
                    subprocess.Popen(["explorer.exe", target])
                return _json({"ok": True})
            except Exception as exc:  # noqa: BLE001
                return _json({"ok": False, "error": str(exc)})

        if path == "/api/document":
            if not isinstance(data, dict):
                return _error(400, "请求体不是合法的文档查询")
            doc = documents.load(self.session, data)
            return _json(doc["dto"])

        if path == "/api/translate":
            return self._start_translate(data)

        if path.startswith("/api/translate/") and path.endswith("/cancel"):
            job_id = path[len("/api/translate/"):-len("/cancel")].strip("/")
            return _json({"ok": self.engine.cancel(job_id)})
        if path.startswith("/api/translate/") and path.endswith("/answer"):
            job_id = path[len("/api/translate/"):-len("/answer")].strip("/")
            question_id = data.get("questionId") if isinstance(data, dict) else None
            if not question_id or not str(question_id).strip():
                return _error(400, "缺少 questionId")
            answer = data.get("answer") if isinstance(data, dict) else ""
            return _json({"ok": self.engine.answer(job_id, str(question_id), str(answer or ""))})

        if path == "/api/export/lang":
            if not isinstance(data, dict):
                return _error(400, "请求体不合法")
            payload, name = self.exports.build_lang_file(data)
            return _download(payload, name)
        if path == "/api/export/resourcepack":
            if not isinstance(data, dict):
                return _error(400, "请求体不合法")
            payload, name = self.exports.build_resource_pack(data)
            return _download(payload, name)
        if path == "/api/export/kubejs-overlay":
            if not isinstance(data, dict):
                return _error(400, "请求体不合法")
            payload, name = self.exports.build_kubejs_overlay(data)
            return _download(payload, name)
        if path == "/api/export/ftb-overlay":
            if not isinstance(data, dict):
                return _error(400, "请求体不合法")
            payload, name = self.exports.build_ftb_overlay(data)
            return _download(payload, name)
        if path == "/api/export/write":
            if not isinstance(data, dict):
                return _error(400, "请求体不合法")
            return _json({"written": self.exports.write_back(data)})

        return _error(404, "接口不存在: " + path)

    def _start_translate(self, data: Any) -> ApiResponse:
        if not isinstance(data, dict):
            return _error(400, "请求体不是合法的翻译请求")
        direct_entries = data.get("entries") if isinstance(data.get("entries"), list) else None
        if str(data.get("kind") or "").lower() == "map" and direct_entries is not None:
            dto = {
                "id": str(data.get("documentId") or "map-scan"),
                "kind": "map",
                "displayName": str(data.get("displayName") or "地图存档"),
                "sourceLanguage": str(data.get("sourceLanguage") or "en_us"),
                "targetLanguage": str(data.get("targetLanguage") or "zh_cn"),
                "entries": direct_entries,
                "existing": data.get("existing") if isinstance(data.get("existing"), dict) else {},
            }
        else:
            doc = documents.load(self.session, data)
            dto = doc["dto"]
        entries = direct_entries if direct_entries is not None else dto.get("entries") or []
        store = self.settings.current()
        target_language = str(data.get("targetLanguage") or "").strip() or dto.get("targetLanguage") or "zh_cn"
        mode = str(data.get("mode") or "").strip() or "llm"
        system_prompt = data.get("systemPrompt")
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            system_prompt = store.get("LlmSystemPrompt") or ""
        request = {
            "documentId": dto.get("id") or "",
            "kind": dto.get("kind") or "",
            "displayName": dto.get("displayName") or "",
            "sourceLanguage": dto.get("sourceLanguage") or "en_us",
            "targetLanguage": target_language,
            "mode": mode,
            "provider": data.get("provider"),
            "context": data.get("context") or dto.get("displayName") or "",
            "systemPrompt": system_prompt,
            "entries": entries,
            "glossary": data.get("glossary") if isinstance(data.get("glossary"), dict) else None,
            "askUserEnabled": bool(data.get("askUser")) if data.get("askUser") is not None else bool(store.get("AskUserEnabled")),
            "batchSize": data.get("batchSize") if data.get("batchSize") is not None else store.get("BatchSize"),
            "concurrency": data.get("concurrency") if data.get("concurrency") is not None else store.get("Concurrency"),
            "skipExisting": bool(data.get("skipExisting")) if data.get("skipExisting") is not None else True,
            "existing": dto.get("existing") or {},
        }
        job = self.engine.start(request, store)
        return _json({"jobId": job["id"]})

    # ----- static -------------------------------------------------------
    def _static(self, path: str) -> ApiResponse:
        if path in ("/", "/index.html"):
            return ApiResponse(200, self.assets.html.encode("utf-8"), "text/html; charset=utf-8")
        if path in ("/favicon.svg", "/favicon.ico"):
            return ApiResponse(200, self.assets.favicon.encode("utf-8"), "image/svg+xml")
        found = self.assets.lookup(path)
        if found is not None:
            data, content_type = found
            return ApiResponse(200, data, content_type)
        if path.startswith("/api"):
            return _error(404, "接口不存在: " + path)
        return ApiResponse(200, self.assets.html.encode("utf-8"), "text/html; charset=utf-8")


# ----- helpers ------------------------------------------------------------
def _parse_query(query: str) -> dict[str, str]:
    if not query:
        return {}
    values: dict[str, str] = {}
    for key, items in urllib.parse.parse_qs(query, keep_blank_values=True).items():
        if items:
            values[key] = items[0]
    return values


def _read_json(body: bytes) -> Any:
    if not body:
        return None
    try:
        text = body.decode("utf-8-sig", errors="replace")
        return jsonutil.loads(text)
    except Exception:  # noqa: BLE001
        return None


def _json(value: Any, status: int = 200) -> ApiResponse:
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return ApiResponse(status, body, "application/json; charset=utf-8")


def _error(status: int, message: str) -> ApiResponse:
    return _json({"error": message}, status=status)


def _download(payload: bytes, name: str) -> ApiResponse:
    safe = name.replace('"', "")
    ascii_name = "".join(ch if (32 <= ord(ch) < 127 and ch not in '"\\') else "_" for ch in safe) or "download.bin"
    encoded = urllib.parse.quote(safe, safe="")
    disposition = 'attachment; filename="' + ascii_name + '"; filename*=UTF-8\'\'' + encoded
    return ApiResponse(
        200,
        payload,
        "application/octet-stream",
        {
            "Content-Disposition": disposition,
        },
    )
