"""Translation job orchestration (LLM batches and machine translation)."""

from __future__ import annotations

import copy
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from . import jsonutil, llm, mt, placeholders, prompts

FENCE = "```"


class TranslationEngine:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._cancellations: dict[str, threading.Event] = {}
        self._pending_answers: dict[str, dict[str, Any]] = {}

    def start(self, request: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
        job: dict[str, Any] = {
            "id": uuid.uuid4().hex[:12],
            "documentId": request.get("documentId") or "",
            "displayName": request.get("displayName") or "",
            "mode": request.get("mode") or "llm",
            "provider": request.get("provider"),
            "status": "queued",
            "total": 0,
            "done": 0,
            "failed": 0,
            "skipped": 0,
            "error": None,
            "startedAt": int(time.time() * 1000),
            "finishedAt": 0,
            "items": [],
            "questions": [],
            "logTail": None,
            "_lock": threading.RLock(),
        }
        existing = request.get("existing") or {}
        skip_existing = bool(request.get("skipExisting", True))
        for entry in request.get("entries") or []:
            item = {
                "key": entry.get("key") or "",
                "source": entry.get("value") or "",
                "target": "",
                "status": "pending",
                "error": None,
            }
            if skip_existing and str(item["source"]).strip():
                zh = existing.get(item["key"])
                if zh and str(zh).strip():
                    item["target"] = str(zh)
                    item["status"] = "skipped"
            job["items"].append(item)

        job["total"] = len(job["items"])
        job["skipped"] = sum(1 for item in job["items"] if item["status"] == "skipped")
        job["done"] = job["skipped"]

        cancel = threading.Event()
        with self._lock:
            self._jobs[job["id"]] = job
            self._cancellations[job["id"]] = cancel

        thread = threading.Thread(
            target=self._run,
            args=(job, request, settings, cancel),
            name="translate-" + job["id"],
            daemon=True,
        )
        thread.start()
        return job

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return _snapshot(job)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            cancel = self._cancellations.get(job_id)
            if cancel is None:
                return False
            cancel.set()
            return True

    def answer(self, job_id: str, question_id: str, answer: str) -> bool:
        with self._lock:
            pending = self._pending_answers.get(question_id)
        if pending is None:
            return False
        with self._lock:
            self._pending_answers.pop(question_id, None)
            job = self._jobs.get(job_id)
            if job is not None:
                with job["_lock"]:
                    for question in job["questions"]:
                        if question["id"] == question_id:
                            question["answer"] = answer
                            question["status"] = "answered"
                            break
        pending["answer"] = answer
        pending["event"].set()
        return True

    # ----- worker -------------------------------------------------------
    def _run(self, job: dict[str, Any], request: dict[str, Any], settings: dict[str, Any], cancel: threading.Event) -> None:
        try:
            with job["_lock"]:
                job["status"] = "running"
            pending = [item for item in job["items"] if item["status"] == "pending"]
            if not pending:
                self._finish(job, "done")
                return

            if request.get("mode") == "mt":
                self._run_machine(job, pending, request, settings, cancel)
            else:
                self._run_llm(job, pending, request, settings, cancel)

            self._finish(job, "cancelled" if cancel.is_set() else "done")
        except _Cancelled:
            self._finish(job, "cancelled")
        except Exception as exc:  # noqa: BLE001
            with job["_lock"]:
                job["error"] = str(exc)
            self._finish(job, "error")
        finally:
            with self._lock:
                self._cancellations.pop(job["id"], None)
            with job["_lock"]:
                for item in job["items"]:
                    if item["status"] == "running":
                        item["status"] = "pending"

    def _finish(self, job: dict[str, Any], status: str) -> None:
        with job["_lock"]:
            job["status"] = status
            job["finishedAt"] = int(time.time() * 1000)
            job["done"] = sum(1 for i in job["items"] if i["status"] in ("done", "skipped"))
            job["failed"] = sum(1 for i in job["items"] if i["status"] == "error")

    def _run_machine(
        self,
        job: dict[str, Any],
        pending: list[dict[str, Any]],
        request: dict[str, Any],
        settings: dict[str, Any],
        cancel: threading.Event,
    ) -> None:
        provider = mt.resolve_provider(settings, request.get("provider"))
        with job["_lock"]:
            job["provider"] = provider
        batch = request.get("batchSize") or 0
        chunk_size = max(1, min(50, batch if batch > 0 else 20))

        for start in range(0, len(pending), chunk_size):
            _check_cancel(cancel)
            chunk = pending[start:start + chunk_size]
            with job["_lock"]:
                for item in chunk:
                    item["status"] = "running"
            prepared = [placeholders.extract(item["source"]) for item in chunk]
            try:
                translated = mt.translate(
                    settings,
                    provider,
                    [item[0] for item in prepared],
                    request.get("sourceLanguage") or "en_us",
                    request.get("targetLanguage") or "zh_cn",
                )
                with job["_lock"]:
                    for index, item in enumerate(chunk):
                        raw = translated[index] if index < len(translated) else ""
                        item["target"] = placeholders.restore(raw, prepared[index][1])
                        item["status"] = "done" if str(item["target"]).strip() else "error"
                        if item["status"] == "error":
                            item["error"] = "机翻返回空结果"
                    job["done"] = sum(1 for i in job["items"] if i["status"] in ("done", "skipped"))
                    job["failed"] = sum(1 for i in job["items"] if i["status"] == "error")
            except Exception as exc:  # noqa: BLE001
                with job["_lock"]:
                    for item in chunk:
                        item["status"] = "error"
                        item["error"] = str(exc)
                    if job["error"] is None:
                        job["error"] = str(exc)
                break

    def _run_llm(
        self,
        job: dict[str, Any],
        pending: list[dict[str, Any]],
        request: dict[str, Any],
        settings: dict[str, Any],
        cancel: threading.Event,
    ) -> None:
        glossary = dict(request.get("glossary") or {})
        glossary_lock = threading.RLock()
        batch = request.get("batchSize") or 0
        batch_size = max(4, min(60, batch if batch > 0 else 20))
        concurrency = max(1, min(8, _as_int(request.get("concurrency"), 4)))
        context = request.get("context") or job["displayName"]
        ask_user = bool(request.get("askUserEnabled")) and bool(settings.get("AskUserEnabled"))
        chunks = [pending[start:start + batch_size] for start in range(0, len(pending), batch_size)]
        if not chunks:
            return

        if concurrency <= 1 or len(chunks) == 1:
            for chunk in chunks:
                _check_cancel(cancel)
                self._run_llm_chunk(
                    job, chunk, request, settings, cancel,
                    glossary, glossary_lock, context, ask_user,
                )
            return

        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=min(concurrency, len(chunks)), thread_name_prefix="translate-llm") as pool:
            futures = {
                pool.submit(
                    self._run_llm_chunk,
                    job, chunk, request, settings, cancel,
                    glossary, glossary_lock, context, ask_user,
                ): chunk
                for chunk in chunks
            }
            for future in as_completed(futures):
                try:
                    future.result()
                except _Cancelled:
                    cancel.set()
                except Exception as exc:  # noqa: BLE001
                    errors.append(str(exc))
        if cancel.is_set():
            raise _Cancelled()
        if errors:
            with job["_lock"]:
                if job["error"] is None:
                    job["error"] = errors[0]

    def _run_llm_chunk(
        self,
        job: dict[str, Any],
        chunk: list[dict[str, Any]],
        request: dict[str, Any],
        settings: dict[str, Any],
        cancel: threading.Event,
        glossary: dict[str, Any],
        glossary_lock: threading.RLock,
        context: str,
        ask_user: bool,
    ) -> None:
        _check_cancel(cancel)
        with job["_lock"]:
            for item in chunk:
                item["status"] = "running"
        prepared = [(item, placeholders.extract(item["source"])) for item in chunk]

        def ask(prompt_glossary: dict[str, Any]) -> str:
            return llm.chat(
                settings,
                prompts.render(
                    request.get("systemPrompt"),
                    request.get("targetLanguage") or "zh_cn",
                    prompt_glossary,
                    ask_user,
                ),
                _build_user_prompt(context, [(item["key"], extracted[0]) for item, extracted in prepared]),
            )

        try:
            with glossary_lock:
                current_glossary = dict(glossary)
            reply = ask(current_glossary)
        except Exception as exc:  # noqa: BLE001
            self._mark_chunk_error(job, chunk, exc)
            return

        mapping, question = _parse_reply(reply, chunk)
        attempts = 0
        while question is not None and ask_user and attempts < 2:
            _check_cancel(cancel)
            answer = self._ask_user(job, question, cancel)
            if answer is None:
                break
            term = question["terms"][0] if question["terms"] else ""
            if term.strip() and answer.strip():
                with glossary_lock:
                    glossary[term] = answer
            attempts += 1
            try:
                with glossary_lock:
                    current_glossary = dict(glossary)
                reply = ask(current_glossary)
            except Exception as exc:  # noqa: BLE001
                self._mark_chunk_error(job, chunk, exc)
                return
            mapping, question = _parse_reply(reply, chunk)

        with job["_lock"]:
            for index, item in enumerate(chunk):
                translated = mapping.get(index)
                if translated and str(translated).strip():
                    item["target"] = placeholders.restore(translated, prepared[index][1][1])
                    item["status"] = "done"
                else:
                    item["status"] = "error"
                    item["error"] = "模型未返回该条目的译文"
            job["done"] = sum(1 for i in job["items"] if i["status"] in ("done", "skipped"))
            job["failed"] = sum(1 for i in job["items"] if i["status"] == "error")

    def _mark_chunk_error(self, job: dict[str, Any], chunk: list[dict[str, Any]], exc: Exception) -> None:
        with job["_lock"]:
            for item in chunk:
                item["status"] = "error"
                item["error"] = str(exc)
            if job["error"] is None:
                job["error"] = str(exc)
            job["done"] = sum(1 for i in job["items"] if i["status"] in ("done", "skipped"))
            job["failed"] = sum(1 for i in job["items"] if i["status"] == "error")

    def _ask_user(
        self,
        job: dict[str, Any],
        question: dict[str, Any],
        cancel: threading.Event,
    ) -> str | None:
        question_obj = {
            "id": uuid.uuid4().hex[:10],
            "question": question.get("question") or "请确认以下术语的译法",
            "terms": list(question.get("terms") or []),
            "options": list(question.get("options") or []),
            "status": "pending",
            "answer": None,
            "askedAt": int(time.time() * 1000),
        }
        with job["_lock"]:
            job["questions"].append(question_obj)
            job["status"] = "waiting_user"

        pending = {"event": threading.Event(), "answer": None}
        with self._lock:
            self._pending_answers[question_obj["id"]] = pending

        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if pending["event"].wait(0.1):
                with job["_lock"]:
                    self._refresh_job_status(job)
                return pending["answer"]
            if cancel.is_set():
                with job["_lock"]:
                    question_obj["status"] = "expired"
                    self._refresh_job_status(job)
                with self._lock:
                    self._pending_answers.pop(question_obj["id"], None)
                raise _Cancelled()
        with job["_lock"]:
            question_obj["status"] = "expired"
            self._refresh_job_status(job)
        with self._lock:
            self._pending_answers.pop(question_obj["id"], None)
        return None

    @staticmethod
    def _refresh_job_status(job: dict[str, Any]) -> None:
        pending = any(q.get("status") == "pending" for q in job.get("questions") or [])
        if pending:
            job["status"] = "waiting_user"
        elif job.get("status") == "waiting_user":
            job["status"] = "running"


class _Cancelled(Exception):
    pass


def _check_cancel(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise _Cancelled()


def _as_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _snapshot(job: dict[str, Any]) -> dict[str, Any]:
    lock = job.get("_lock")
    if lock is None:
        data = {k: v for k, v in job.items() if not k.startswith("_")}
        return copy.deepcopy(data)
    with lock:
        data = {k: v for k, v in job.items() if not k.startswith("_")}
        return copy.deepcopy(data)


def _build_user_prompt(context: str, items: list[tuple[str, str]]) -> str:
    payload: dict[str, str] = {}
    for key, text in items:
        payload[key] = text
    lines = [
        "Document: " + context,
        "Translate the values of this JSON object:",
        jsonutil.dumps(payload, indented=True),
    ]
    return "\n".join(lines) + "\n"


def _extract_json_object(reply: str) -> str | None:
    if not reply or not reply.strip():
        return None
    text = reply.strip()
    if text.startswith(FENCE):
        first_newline = text.find("\n")
        if first_newline > 0:
            text = text[first_newline + 1:]
        last_fence = text.rfind(FENCE)
        if last_fence >= 0:
            text = text[:last_fence]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    return text[start:end + 1]


def _parse_reply(
    reply: str, chunk: list[dict[str, Any]]
) -> tuple[dict[int, str], dict[str, Any] | None]:
    mapping: dict[int, str] = {}
    json_text = _extract_json_object(reply)
    if json_text is None:
        return mapping, None
    try:
        obj = jsonutil.loads(json_text)
    except Exception:  # noqa: BLE001
        return mapping, None
    if not isinstance(obj, dict):
        return mapping, None

    ask = obj.get("__ask__")
    if isinstance(ask, dict):
        question = ask.get("question")
        question_text = question if isinstance(question, str) else "请确认以下术语的译法"
        terms = [t for t in (ask.get("terms") or []) if isinstance(t, str) and t]
        options = [t for t in (ask.get("options") or []) if isinstance(t, str) and t]
        return mapping, {"question": question_text, "terms": terms, "options": options}

    for index, item in enumerate(chunk):
        key = item["key"]
        value = obj.get(key)
        if isinstance(value, str):
            mapping[index] = value
            continue
        for candidate_key, candidate_value in obj.items():
            if isinstance(candidate_key, str) and candidate_key.endswith(key) and isinstance(candidate_value, str):
                mapping[index] = candidate_value
                break
    return mapping, None
