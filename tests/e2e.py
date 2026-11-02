#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DST MC I18n Tool 端到端（e2e）验收脚本。

只用 Python 3 标准库（urllib / subprocess / zipfile / json / socket），
不依赖 requests、pytest 等第三方包。

它会：
  1. 确保测试夹具存在（不存在时自动调用 tests/make_fixtures.py 生成）；
  2. 以 --path <夹具> --port <随机空闲端口> --no-browser 启动发布后的 exe；
  3. 轮询 /api/health 直到服务就绪；
  4. 依次验证工作目录扫描、三类文档读取、四种导出产物与若干错误分支；
  5. 结束进程，打印 PASS / FAIL / WARN 汇总，并以退出码表示结果。

用法：
    python tests/e2e.py
    python tests/e2e.py --exe dist/DST-MC-I18n-Tool.exe
    python tests/e2e.py --exe "D:/build/DST-MC-I18n-Tool.exe" --timeout 90 --verbose

退出码：
    0  所有必需检查通过（可能有 WARN）
    1  存在 FAIL
    2  无法开始测试（exe 不存在 / 夹具生成失败 / 服务启动失败）
"""

from __future__ import annotations

import argparse
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "instance"
DEFAULT_EXE = ROOT / "dist" / "DST-MC-I18n-Tool.exe"
MAKE_FIXTURES = ROOT / "tests" / "make_fixtures.py"


def _setup_stdout() -> None:
    """输出被重定向（管道 / CI 日志）时强制 UTF-8，避免 Windows 代码页乱码；
    直接挂在控制台（tty）时保持系统默认编码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_setup_stdout()



# ---------------------------------------------------------------------------
# 结果收集
# ---------------------------------------------------------------------------

class CheckFailure(AssertionError):
    """单个检查项失败。"""


class SetupError(RuntimeError):
    """测试无法开始（exe 缺失、夹具生成失败等），对应退出码 2。"""


class Reporter:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.passed = 0
        self.failed = 0
        self.warned = 0
        self.failures = []
        self.warnings = []

    def ok(self, name: str, detail: str = "") -> None:
        self.passed += 1
        print("  [PASS] " + name + (("  " + detail) if detail else ""))

    def fail(self, name: str, detail: str) -> None:
        self.failed += 1
        self.failures.append((name, detail))
        print("  [FAIL] " + name + "  " + detail)

    def warn(self, name: str, detail: str) -> None:
        self.warned += 1
        self.warnings.append((name, detail))
        print("  [WARN] " + name + "  " + detail)

    def run(self, name: str, fn, *args, **kwargs) -> None:
        """执行一个检查；断言失败记 FAIL，其余异常记 FAIL（带堆栈摘要）。"""
        try:
            detail = fn(*args, **kwargs)
        except CheckFailure as exc:
            self.fail(name, str(exc))
        except Exception as exc:  # noqa: BLE001 - 测试脚本要收集所有异常
            if self.verbose:
                traceback.print_exc()
            self.fail(name, type(exc).__name__ + ": " + str(exc))
        else:
            self.ok(name, detail or "")


def require(condition, message: str) -> None:
    if not condition:
        raise CheckFailure(message)


def require_eq(actual, expected, label: str) -> None:
    if actual != expected:
        raise CheckFailure(label + " 期望 " + repr(expected) + "，实际 " + repr(actual))


# ---------------------------------------------------------------------------
# HTTP 辅助
# ---------------------------------------------------------------------------

class HttpResult:
    def __init__(self, status: int, headers, body: bytes, url: str):
        self.status = status
        self.headers = headers
        self.body = body
        self.url = url

    @property
    def content_disposition(self) -> str:
        return self.headers.get("Content-Disposition", "") or ""

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8-sig"))
        except Exception as exc:  # noqa: BLE001
            preview = self.body[:300]
            raise CheckFailure("响应不是合法 JSON: " + str(exc) + " body=" + repr(preview))


def http(method: str, url: str, payload=None, timeout: float = 30.0) -> HttpResult:
    data = None
    headers = {"Accept": "*/*"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return HttpResult(resp.status, dict(resp.headers), resp.read(), url)
    except urllib.error.HTTPError as exc:
        return HttpResult(exc.code, dict(exc.headers), exc.read(), url)


def http_ok(method: str, url: str, payload=None, timeout: float = 30.0) -> HttpResult:
    res = http(method, url, payload, timeout)
    if res.status != 200:
        raise CheckFailure("HTTP " + str(res.status) + " <- " + method + " " + url +
                           " body=" + res.body[:300].decode("utf-8", "replace"))
    return res


# ---------------------------------------------------------------------------
# 端口 / 进程
# ---------------------------------------------------------------------------

def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_health(base_url: str, proc: subprocess.Popen, timeout: float) -> dict:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                "exe 在服务就绪前退出（exit code " + str(proc.returncode) + "）。"
                "请检查是否缺少参数或端口被占用。"
            )
        try:
            res = http("GET", base_url + "/api/health", timeout=2.0)
            if res.status == 200:
                return res.json()
            last_error = "HTTP " + str(res.status)
        except Exception as exc:  # noqa: BLE001 - 连接失败是预期内的重试
            last_error = type(exc).__name__ + ": " + str(exc)
        time.sleep(0.25)
    raise RuntimeError("等待 /api/health 超时（" + str(timeout) + "s），最后错误: " + last_error)


def read_tail(path: Path, limit: int = 4000) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if len(data) > limit:
        data = data[-limit:]
    return data.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# 检查用的常量与辅助
# ---------------------------------------------------------------------------

MOD_FILE = "CreateFake-1.0.0.jar"
MOD_NS = "createfake"
KUBEJS_REL = "assets/custom_ns/lang/en_us.json"
# ftb 的 path 是相对于 config/ftbquests 的相对路径
FTB_REL = "quests/lang/en_us.snbt"
FTB_CHAPTER_REL = "quests/chapters/chapter1.snbt"

TRANSLATED = [
    {"key": "item.createfake.cogwheel", "value": "齿轮"},
    {"key": "item.createfake.shaft", "value": "传动轴"},
    {"key": "createfake.gui.title", "value": "机械搅拌器"},
    {"key": "createfake.message.line_break", "value": "第一行\n第二行\n第三行"},
    {"key": "createfake.tooltip.kinetic", "value": "应力：%s SU"},
]

KUBEJS_TRANSLATED = [
    {"key": "item.custom_ns.magic_wand", "value": "魔法杖"},
    {"key": "custom_ns.tooltip.energy", "value": "能量：%d FE"},
]

FTB_TRANSLATED = [
    {"key": "quest.1.title", "value": "你好"},
    {"key": "quest.2.title", "value": "收集木材"},
    {"key": "chapter.1.title", "value": "开始游戏"},
]


def read_zip(body: bytes, label: str) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile as exc:
        raise CheckFailure(label + " 不是合法 zip: " + str(exc)) from exc


def zip_names(zf: zipfile.ZipFile):
    return [info.filename.replace("\\", "/") for info in zf.infolist() if not info.is_dir()]


def zip_read_json(zf: zipfile.ZipFile, name: str):
    try:
        raw = zf.read(name)
    except KeyError as exc:
        raise CheckFailure("zip 中缺少 " + name + "，实际条目: " + ", ".join(zip_names(zf))) from exc
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except Exception as exc:  # noqa: BLE001
        raise CheckFailure(name + " 不是合法 JSON: " + str(exc) + " body=" + repr(raw[:200])) from exc


def assert_no_bom(raw: bytes, label: str) -> None:
    require(not raw.startswith(b"\xef\xbb\xbf"), label + " 不应带 UTF-8 BOM")


def assert_utf8_chinese(raw: bytes, chinese: str, label: str) -> None:
    require(chinese.encode("utf-8") in raw, label + " 应以 UTF-8 原文包含 " + repr(chinese) + "（中文不应被 \\u 转义）")


# ---------------------------------------------------------------------------
# 各项检查
# ---------------------------------------------------------------------------

def check_health(base_url: str, health: dict) -> str:
    require(health.get("ok") is True, "health.ok 应为 true，实际 " + repr(health.get("ok")))
    require(isinstance(health.get("name"), str) and health["name"], "health.name 缺失")
    require(isinstance(health.get("version"), str) and health["version"], "health.version 缺失")
    require(isinstance(health.get("pid"), int) and health["pid"] > 0, "health.pid 缺失或非法")
    return "name=" + str(health["name"]) + " version=" + str(health["version"]) + " pid=" + str(health["pid"])


def check_workspace(base_url: str, fixture: Path) -> str:
    data = http_ok("GET", base_url + "/api/workspace?path=" + urllib.parse.quote(str(fixture))).json()
    require(data.get("exists") is True, "workspace.exists 应为 true")
    require(Path(data.get("path", "")).resolve() == fixture.resolve(),
            "workspace.path 与夹具路径不一致: " + repr(data.get("path")))

    mods = data.get("mods") or {}
    require(mods.get("found") is True, "mods.found 应为 true")
    mod_list = mods.get("mods") or []
    by_name = {m.get("fileName"): m for m in mod_list}
    require(MOD_FILE in by_name, "mods 列表缺少 " + MOD_FILE + "，实际: " + ", ".join(map(str, by_name)))
    require("ForgeFake.jar" in by_name, "mods 列表缺少 ForgeFake.jar")
    require("BrokenMod.jar" not in by_name, "损坏的 BrokenMod.jar 不应出现在 mods 列表（应被跳过并记 warning）")

    create = by_name[MOD_FILE]
    require_eq(create.get("modId"), "createfake", MOD_FILE + " modId")
    require_eq(create.get("loader"), "fabric", MOD_FILE + " loader")
    require(create.get("entryCount", 0) >= 20, MOD_FILE + " entryCount 应 >= 20，实际 " + str(create.get("entryCount")))
    lang_files = create.get("langFiles") or []
    require(any(lf.get("namespace") == MOD_NS and lf.get("lang") == "en_us" for lf in lang_files),
            MOD_FILE + " 应包含 assets/" + MOD_NS + "/lang/en_us.json")

    forge = by_name["ForgeFake.jar"]
    require_eq(forge.get("modId"), "forgefake", "ForgeFake.jar modId")
    require_eq(forge.get("loader"), "forge", "ForgeFake.jar loader")
    require(any(lf.get("lang") == "zh_cn" for lf in (forge.get("langFiles") or [])),
            "ForgeFake.jar 应包含 zh_cn 语言文件")

    kubejs = data.get("kubejs") or {}
    require(kubejs.get("found") is True, "kubejs.found 应为 true")
    namespaces = kubejs.get("namespaces") or []
    require("kubejs" in namespaces and "custom_ns" in namespaces,
            "kubejs.namespaces 应包含 kubejs 与 custom_ns，实际 " + repr(namespaces))

    ftb = data.get("ftb") or {}
    require(ftb.get("found") is True, "ftb.found 应为 true")
    ftb_paths = [f.get("relativePath") for f in (ftb.get("files") or [])]
    require("quests/lang/en_us.snbt" in ftb_paths,
            "ftb.files 应包含 quests/lang/en_us.snbt（相对 config/ftbquests），实际 " + repr(ftb_paths))

    require("1.20.1" in (data.get("gameVersion") or ""),
            "gameVersion 应识别出 1.20.1，实际 " + repr(data.get("gameVersion")))
    return ("mods=" + str(len(mod_list)) + " kubejsFiles=" + str(len(kubejs.get("files") or [])) +
            " ftbFiles=" + str(len(ftb.get("files") or [])) + " version=" + str(data.get("gameVersion")))


def check_document_mod(base_url: str) -> str:
    # API.md 写的是 namespace=，实现里同时接受 ns=；两个都传，避免契约/实现差异导致误判。
    query = urllib.parse.urlencode({"kind": "mod", "file": MOD_FILE, "ns": MOD_NS,
                                    "namespace": MOD_NS, "lang": "en_us"})
    doc = http_ok("GET", base_url + "/api/document?" + query).json()
    require_eq(doc.get("kind"), "mod", "document.kind")
    require_eq(doc.get("format"), "json", "document.format")
    require(doc.get("id"), "document.id 不能为空")
    require_eq(doc.get("namespaceName"), MOD_NS, "document.namespaceName")
    require_eq(doc.get("modFileName"), MOD_FILE, "document.modFileName")
    entries = doc.get("entries") or []
    require(len(entries) >= 20, "entries 应 >= 20，实际 " + str(len(entries)))
    table = {e.get("key"): e.get("value") for e in entries}
    require_eq(table.get("item.createfake.cogwheel"), "Cogwheel", "cogwheel 原文")
    require(any("%s" in (v or "") for v in table.values()), "entries 应保留 %s 占位符")
    require(any("§" in (v or "") for v in table.values()), "entries 应保留 § 颜色码")
    require(any("\n" in (v or "") for v in table.values()), "entries 应保留 \n 换行")
    require(isinstance(doc.get("existing"), dict), "document.existing 应为对象")
    return "entries=" + str(len(entries)) + " hasExisting=" + str(doc.get("hasExisting"))


def check_document_kubejs(base_url: str) -> str:
    query = urllib.parse.urlencode({"kind": "kubejs", "path": KUBEJS_REL})
    doc = http_ok("GET", base_url + "/api/document?" + query).json()
    require_eq(doc.get("kind"), "kubejs", "document.kind")
    require_eq(doc.get("format"), "json", "document.format")
    require_eq(doc.get("ownerId"), "custom_ns", "document.ownerId")
    entries = doc.get("entries") or []
    require(len(entries) >= 5, "kubejs entries 应 >= 5，实际 " + str(len(entries)))
    require(doc.get("hasExisting") is True, "custom_ns 已有 zh_cn.json，hasExisting 应为 true")
    existing = doc.get("existing") or {}
    require(existing.get("item.custom_ns.magic_wand") == "魔法杖",
            "existing 应包含已有中文翻译，实际 " + repr(existing))
    return "entries=" + str(len(entries)) + " existing=" + str(len(existing))


def check_document_ftb(base_url: str) -> str:
    query = urllib.parse.urlencode({"kind": "ftb", "path": FTB_REL})
    doc = http_ok("GET", base_url + "/api/document?" + query).json()
    require_eq(doc.get("kind"), "ftb", "document.kind")
    require_eq(doc.get("format"), "snbt", "document.format")
    require_eq(doc.get("ownerId"), "ftbquests", "document.ownerId")
    table = {e.get("key"): e.get("value") for e in (doc.get("entries") or [])}
    require_eq(table.get("quest.1.title"), "Hello", "SNBT quest.1.title")
    require(len(table) >= 10, "ftb entries 应 >= 10，实际 " + str(len(table)))

    chapter_query = urllib.parse.urlencode({"kind": "ftb", "path": FTB_CHAPTER_REL})
    chapter = http_ok("GET", base_url + "/api/document?" + chapter_query).json()
    require_eq(chapter.get("format"), "snbt", "chapter1.snbt format")
    chapter_entries = chapter.get("entries") or []
    # 嵌套结构会被扁平化（数组下标形如 quests.[0].id），这里只断言关键文本被正确解析出来
    chapter_values = [e.get("value") for e in chapter_entries]
    require("Getting Started" in chapter_values, "chapter1.snbt 应解析出 title=Getting Started")
    require("chapter1" in chapter_values, "chapter1.snbt 应解析出 id=chapter1")
    require(len(chapter_entries) >= 5, "chapter1.snbt 条目数应 >= 5，实际 " + str(len(chapter_entries)))
    return "entries=" + str(len(table)) + " chapterEntries=" + str(len(chapter_entries))


def check_document_error(base_url: str) -> str:
    res = http("GET", base_url + "/api/document?kind=mod&file=NoSuchMod.jar&namespace=x")
    require(res.status >= 400, "不存在的 mod 应返回 4xx，实际 " + str(res.status))
    data = res.json()
    require(isinstance(data.get("error"), str) and data["error"],
            "错误响应应为 {\"error\": \"message\"}，实际 " + repr(data)[:200])
    return "HTTP " + str(res.status) + " error=" + data["error"][:60]


def check_export_lang(base_url: str) -> str:
    res = http_ok("POST", base_url + "/api/export/lang", {
        "kind": "mod",
        "file": MOD_FILE,
        "namespace": MOD_NS,
        "lang": "en_us",
        "entries": TRANSLATED,
        "targetLanguage": "zh_cn",
        "fileName": "createfake-zh_cn.json",
    })
    require("attachment" in res.content_disposition.lower(),
            "响应应带 Content-Disposition: attachment，实际 " + repr(res.content_disposition))
    assert_no_bom(res.body, "export/lang")
    assert_utf8_chinese(res.body, "齿轮", "export/lang")
    data = res.json()
    require_eq(data.get("item.createfake.cogwheel"), "齿轮", "导出 json 译文")
    require_eq(data.get("createfake.tooltip.kinetic"), "应力：%s SU", "导出 json 占位符")
    require_eq(data.get("createfake.message.line_break"), "第一行\n第二行\n第三行", "导出 json 换行")
    # 导出的是「原文全文 + 译文覆盖」，因此未翻译的键应保持英文原文
    require(len(data) >= 20, "导出 json 应包含完整语言表（>=20 条），实际 " + str(len(data)))
    require_eq(data.get("item.createfake.shaft"), "传动轴", "导出 json 第二条译文")
    require_eq(data.get("item.createfake.andesite_alloy"), "Andesite Alloy", "未翻译的键应保留原文")
    return "bytes=" + str(len(res.body)) + " entries=" + str(len(data))


def check_export_resourcepack(base_url: str) -> str:
    res = http_ok("POST", base_url + "/api/export/resourcepack", {
        "kind": "mod",
        "file": MOD_FILE,
        "namespace": MOD_NS,
        "lang": "en_us",
        "entries": TRANSLATED,
        "targetLanguage": "zh_cn",
        "packName": "测试汉化资源包",
        "packVersion": "1.0.0",
        "mcVersion": "1.20.1",
        "description": "e2e 测试生成的资源包",
    })
    require("attachment" in res.content_disposition.lower(), "资源包应作为附件下载")
    zf = read_zip(res.body, "resourcepack")
    names = zip_names(zf)
    require("pack.mcmeta" in names, "资源包缺少 pack.mcmeta，实际: " + ", ".join(names))
    require(any(n.startswith("assets/" + MOD_NS + "/lang/") for n in names),
            "资源包缺少 assets/" + MOD_NS + "/lang/*.json，实际: " + ", ".join(names))

    meta = zip_read_json(zf, "pack.mcmeta")
    pack = meta.get("pack") or {}
    require_eq(pack.get("pack_format"), 15, "1.20.1 的 pack_format")
    require(isinstance(pack.get("description"), str) and pack["description"],
            "pack.mcmeta description 缺失")

    lang_name = next(n for n in names if n.startswith("assets/" + MOD_NS + "/lang/"))
    require_eq(lang_name, "assets/" + MOD_NS + "/lang/zh_cn.json", "资源包内语言文件路径")
    lang = zip_read_json(zf, lang_name)
    require_eq(lang.get("item.createfake.cogwheel"), "齿轮", "资源包 lang 译文")
    require(len(lang) >= 20, "资源包 lang 应包含完整语言表，实际 " + str(len(lang)))
    detail = "pack_format=" + str(pack.get("pack_format")) + " files=" + str(len(names))
    if "pack.png" in names:
        detail += " pack.png=yes"
    else:
        detail += " pack.png=no"
    return detail


def check_export_kubejs_overlay(base_url: str) -> str:
    # 覆盖包不再需要版本号：请求体里只有 files + targetLanguage
    res = http_ok("POST", base_url + "/api/export/kubejs-overlay", {
        # path 传源语言文件（en_us），导出时服务端会换成 zh_cn
        "files": [{"path": KUBEJS_REL, "entries": KUBEJS_TRANSLATED}],
        "targetLanguage": "zh_cn",
    })
    require("attachment" in res.content_disposition.lower(), "kubejs 覆盖包应作为附件下载")
    zf = read_zip(res.body, "kubejs-overlay")
    names = zip_names(zf)
    # 覆盖包直接解压到整合包根目录，因此路径以 kubejs/ 开头
    target = "kubejs/assets/custom_ns/lang/zh_cn.json"
    require(target in names, "覆盖包缺少 " + target + "，实际: " + ", ".join(names))
    data = zip_read_json(zf, target)
    require_eq(data.get("item.custom_ns.magic_wand"), "魔法杖", "kubejs 覆盖包译文")
    require(len(data) >= 5, "kubejs 覆盖包应保留完整语言表，实际 " + str(len(data)))
    require(not any("v1" in n for n in names), "覆盖包内不应出现版本号目录")
    return "files=" + str(len(names)) + " hasReadme=" + str(any(n.lower() == "readme.txt" for n in names))


def check_export_kubejs_overlay_all(base_url: str) -> str:
    # files 省略 = 导出全部 kubejs 语言文件
    res = http_ok("POST", base_url + "/api/export/kubejs-overlay", {"targetLanguage": "zh_cn"})
    zf = read_zip(res.body, "kubejs-overlay-all")
    names = [n for n in zip_names(zf) if n.endswith(".json")]
    require(len(names) >= 2, "全部导出应包含多个 kubejs 语言文件，实际: " + ", ".join(names))
    require(all(n.startswith("kubejs/") for n in names), "路径应以 kubejs/ 开头: " + ", ".join(names))
    require(all(n.endswith("zh_cn.json") for n in names), "语言名应换成 zh_cn: " + ", ".join(names))
    return "files=" + str(len(names))


def check_export_ftb_overlay(base_url: str) -> str:
    res = http_ok("POST", base_url + "/api/export/ftb-overlay", {
        "files": [{"path": FTB_REL, "entries": FTB_TRANSLATED}],
        "targetLanguage": "zh_cn",
    })
    require("attachment" in res.content_disposition.lower(), "ftb 覆盖包应作为附件下载")
    zf = read_zip(res.body, "ftb-overlay")
    names = zip_names(zf)
    target = "config/ftbquests/quests/lang/zh_cn.snbt"
    require(target in names, "覆盖包缺少 " + target + "，实际: " + ", ".join(names))
    raw = zf.read(target).decode("utf-8-sig")
    require("quest.1.title" in raw, "ftb 覆盖包应包含 quest.1.title 键，实际内容: " + raw[:200])
    require("你好" in raw, "ftb 覆盖包应包含中文译文 你好")
    require("chapter.2.subtitle" in raw, "ftb 覆盖包应保留完整语言表")
    return "files=" + str(len(names)) + " bytes=" + str(len(raw))


def check_export_ftb_overlay_all(base_url: str) -> str:
    res = http_ok("POST", base_url + "/api/export/ftb-overlay", {"targetLanguage": "zh_cn"})
    zf = read_zip(res.body, "ftb-overlay-all")
    names = [n for n in zip_names(zf) if n.endswith(".snbt")]
    require(len(names) >= 1, "全部导出应包含 ftbquests 语言文件")
    require(all(n.startswith("config/ftbquests/") for n in names), "路径应以 config/ftbquests/ 开头: " + ", ".join(names))
    return "files=" + str(len(names))


def check_export_write(base_url: str, workdir: Path) -> str:
    out_dir = workdir / "dst_i18n_output"
    res = http_ok("POST", base_url + "/api/export/write", {
        "kind": "mod",
        "file": MOD_FILE,
        "namespace": MOD_NS,
        "lang": "en_us",
        "entries": TRANSLATED,
        "targetLanguage": "zh_cn",
        "outputDirectory": str(out_dir),
    })
    data = res.json()
    written = data.get("written") or []
    require(isinstance(written, list) and written, "export/write 应返回非空 written 数组，实际 " + repr(data)[:200])
    target = Path(written[0])
    require(target.is_file(), "写回的文件不存在: " + str(target))
    payload = json.loads(target.read_text(encoding="utf-8-sig"))
    require_eq(payload.get("item.createfake.cogwheel"), "齿轮", "写回文件译文")
    target.unlink(missing_ok=True)
    try:
        target.parent.rmdir()
    except OSError:
        pass
    return "written=" + str(len(written))


def check_browse(base_url: str, fixture: Path) -> str:
    # 浏览夹具的父目录：夹具本身应被标记为含 mods/kubejs/config，方便用户在界面上识别
    parent = fixture.parent
    res = http_ok("GET", base_url + "/api/workspace/browse?path=" + urllib.parse.quote(str(parent)))
    data = res.json()
    require_eq(Path(data.get("path", "")).resolve(), parent.resolve(), "browse.path")
    dirs = data.get("dirs") or []
    flagged = {d.get("name"): d for d in dirs}
    require(fixture.name in flagged, "browse 应列出 " + fixture.name + "，实际 " + repr(list(flagged)))
    entry = flagged[fixture.name]
    require(entry.get("hasMods") is True, "browse 应把夹具标记为 hasMods")
    require(entry.get("hasKubejs") is True, "browse 应把夹具标记为 hasKubejs")
    require(entry.get("hasConfig") is True, "browse 应把夹具标记为 hasConfig")

    inside = http_ok("GET", base_url + "/api/workspace/browse?path=" + urllib.parse.quote(str(fixture))).json()
    names = [d.get("name") for d in (inside.get("dirs") or [])]
    for expected in ("mods", "kubejs", "config"):
        require(expected in names, "browse 夹具目录时应列出 " + expected + "，实际 " + repr(names))
    return "dirs=" + str(len(dirs)) + " children=" + str(len(names))


def check_translate_error(base_url: str) -> str:
    res = http("POST", base_url + "/api/translate", {
        "kind": "mod", "file": "NoSuchMod.jar", "namespace": "x", "mode": "llm",
    })
    require(res.status >= 400, "翻译不存在的文档应返回 4xx，实际 " + str(res.status))
    data = res.json()
    require(isinstance(data.get("error"), str) and data["error"], "错误响应应为 {\"error\": ...}")
    return "HTTP " + str(res.status)


def check_settings(base_url: str) -> str:
    data = http_ok("GET", base_url + "/api/settings").json()
    require_eq(data.get("llmApiKey"), "", "settings.llmApiKey 不应回传明文")
    require(isinstance(data.get("llmApiKeySet"), bool), "settings.llmApiKeySet 应为布尔值")
    require(isinstance(data.get("llmBaseUrl"), str) and data["llmBaseUrl"], "settings.llmBaseUrl 缺失")
    require(isinstance(data.get("llmModel"), str) and data["llmModel"], "settings.llmModel 缺失")
    # 默认模型固定为 deepseek-flash，候选里同时提供 deepseek-v4-pro
    require_eq(data.get("llmModel"), "deepseek-flash", "默认模型应为 deepseek-flash")
    models = data.get("suggestedModels") or []
    require("deepseek-flash" in models and "deepseek-v4-pro" in models,
            "suggestedModels 应包含 deepseek-flash 与 deepseek-v4-pro，实际 " + repr(models))
    require_eq(data.get("llmTemperature"), 1, "默认温度应为 1")
    # 默认提示词必须包含“非文本内容原样返回”的约束
    default_prompt = data.get("llmSystemPromptDefault") or ""
    require("item ID" in default_prompt or "物品ID" in default_prompt or "item id" in default_prompt.lower(),
            "默认提示词应包含“定义物品 ID 时原样返回”的说明")
    return "model=" + str(data.get("llmModel")) + " apiKeySet=" + str(data.get("llmApiKeySet"))


def check_settings_prompt(base_url: str) -> str:
    before = http_ok("GET", base_url + "/api/settings").json()
    default_prompt = before.get("llmSystemPromptDefault") or ""
    require(len(default_prompt) > 50, "默认提示词不应为空")

    saved = http_ok("POST", base_url + "/api/settings", {
        "llmBaseUrl": before["llmBaseUrl"],
        "llmModel": before["llmModel"],
        "llmSystemPrompt": "CUSTOM-PROMPT-测试",
        "llmTemperature": before.get("llmTemperature", 0.3),
        "llmMaxTokens": before.get("llmMaxTokens", 4096),
        "targetLanguage": before.get("targetLanguage", "zh_cn"),
        "askUserEnabled": before.get("askUserEnabled", True),
        "requestTimeoutSeconds": before.get("requestTimeoutSeconds", 120),
        "batchSize": before.get("batchSize", 24),
        "mtProvider": before.get("mtProvider", "auto"),
    }).json()
    require_eq(saved.get("llmSystemPrompt"), "CUSTOM-PROMPT-测试", "保存后的自定义提示词")

    reset = http_ok("POST", base_url + "/api/settings/reset-prompt", {}).json()
    require_eq(reset.get("llmSystemPrompt"), "", "恢复默认后提示词应为空")
    require_eq(reset.get("llmSystemPromptDefault"), default_prompt, "恢复默认后仍应返回默认提示词")
    return "prompt len=" + str(len(default_prompt))


def check_workspace_scan(base_url: str, fixture: Path) -> str:
    # 非法目录：400 + valid=false
    res = http("POST", base_url + "/api/workspace/scan", {"path": "C:/Windows"})
    require(res.status == 400, "非法目录应返回 400，实际 " + str(res.status))
    bad = res.json()
    require(bad.get("valid") is False, "非法目录应返回 valid=false，实际 " + repr(bad)[:200])

    # 合法目录：200 + scanId，随后可轮询到 done
    res = http_ok("POST", base_url + "/api/workspace/scan", {"path": str(fixture)})
    started = res.json()
    require(isinstance(started.get("scanId"), str) and started["scanId"], "应返回 scanId，实际 " + repr(started)[:200])
    scan_id = started["scanId"]

    phases = []
    last = None
    for _ in range(300):
        time.sleep(0.05)
        last = http_ok("GET", base_url + "/api/workspace/scan/" + scan_id).json()
        phases.append(last.get("phase"))
        if last.get("status") in ("done", "error", "cancelled"):
            break
    require_eq(last.get("status"), "done", "扫描应完成，实际 " + repr(last)[:300])
    require_eq(last.get("percent"), 100, "完成后 percent 应为 100")
    # 夹具很小，扫描可能在第一次轮询前就结束，因此只要求阶段合法即可。
    require(set(phases) <= {"prepare", "mods", "kubejs", "ftb", "finish"},
            "扫描阶段取值非法，实际 " + repr(sorted(set(phases))))
    require_eq(last.get("phase"), "finish", "完成后 phase 应为 finish")

    ws = http_ok("GET", base_url + "/api/workspace").json()
    require("scanning" in ws, "GET /api/workspace 应带 scanning 字段")
    require(len(ws.get("mods", {}).get("mods") or []) >= 2, "扫描后应找到模组")
    return "scanId=" + scan_id[:6] + " phases=" + str(len(set(phases)))


def check_static_index(base_url: str) -> str:
    res = http_ok("GET", base_url + "/")
    require(b"<html" in res.body.lower() or b"<!doctype" in res.body.lower(),
            "GET / 应返回内置 index.html")
    return "bytes=" + str(len(res.body))


def optional_pack_png(base_url: str, reporter: Reporter) -> None:
    """pack.png 是 exporter 规范要求项，但 API.md 未强制；缺失只记 WARN。"""
    try:
        res = http_ok("POST", base_url + "/api/export/resourcepack", {
            "kind": "mod",
            "file": MOD_FILE,
            "namespace": MOD_NS,
            "entries": TRANSLATED,
            "targetLanguage": "zh_cn",
            "mcVersion": "1.20.1",
        })
        zf = read_zip(res.body, "resourcepack")
        if "pack.png" not in zip_names(zf):
            reporter.warn("资源包 pack.png", "zip 中没有 pack.png（exporter 规范建议包含）")
        else:
            reporter.ok("资源包 pack.png", "包含内置图标")
    except CheckFailure as exc:
        reporter.warn("资源包 pack.png", str(exc))


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def ensure_fixtures(verbose: bool) -> None:
    if (FIXTURES / "version.json").is_file() and (FIXTURES / "mods" / MOD_FILE).is_file():
        return
    print("[setup] 测试夹具不存在，正在生成: " + str(FIXTURES))
    result = subprocess.run([sys.executable, str(MAKE_FIXTURES)], cwd=str(ROOT))
    if result.returncode != 0:
        raise SetupError("生成测试夹具失败（exit code " + str(result.returncode) + "）")


def resolve_exe(explicit: str | None) -> Path:
    exe = Path(explicit) if explicit else DEFAULT_EXE
    if not exe.is_absolute():
        exe = (Path.cwd() / exe).resolve()
    if not exe.is_file():
        raise SetupError(
            "找不到可执行文件: " + str(exe) + "\n"
            "请先构建（Lead 负责）或使用 --exe 指定路径，例如：\n"
            "    python tests/e2e.py --exe dist/DST-MC-I18n-Tool.exe\n"
            "注意：本脚本只验证发布产物，不负责 dotnet build/publish。"
        )
    return exe


def run_checks(base_url: str, fixture: Path, health: dict, reporter: Reporter) -> None:
    print("\n[1/6] 基础与工作目录")
    reporter.run("GET /api/health", check_health, base_url, health)
    reporter.run("GET /api/workspace", check_workspace, base_url, fixture)
    reporter.run("GET /api/settings", check_settings, base_url)
    reporter.run("GET / (内置前端)", check_static_index, base_url)

    print("\n[2/6] 异步扫描与提示词")
    reporter.run("POST /api/workspace/scan", check_workspace_scan, base_url, fixture)
    reporter.run("POST /api/settings/reset-prompt", check_settings_prompt, base_url)

    print("\n[3/6] 文档读取")
    reporter.run("GET /api/document?kind=mod", check_document_mod, base_url)
    reporter.run("GET /api/document?kind=kubejs", check_document_kubejs, base_url)
    reporter.run("GET /api/document?kind=ftb", check_document_ftb, base_url)
    reporter.run("GET /api/workspace/browse", check_browse, base_url, fixture)
    reporter.run("GET /api/document 错误分支", check_document_error, base_url)
    reporter.run("POST /api/translate 错误分支", check_translate_error, base_url)

    print("\n[4/6] 导出（下载）")
    reporter.run("POST /api/export/lang", check_export_lang, base_url)
    reporter.run("POST /api/export/resourcepack", check_export_resourcepack, base_url)
    optional_pack_png(base_url, reporter)

    print("\n[5/6] 覆盖包导出（无版本号）")
    reporter.run("POST /api/export/kubejs-overlay", check_export_kubejs_overlay, base_url)
    reporter.run("POST /api/export/kubejs-overlay 全部文件", check_export_kubejs_overlay_all, base_url)
    reporter.run("POST /api/export/ftb-overlay", check_export_ftb_overlay, base_url)
    reporter.run("POST /api/export/ftb-overlay 全部文件", check_export_ftb_overlay_all, base_url)

    print("\n[6/6] 写回工作目录")
    reporter.run("POST /api/export/write", check_export_write, base_url, fixture)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="DST MC I18n Tool 端到端验收")
    parser.add_argument("--exe", default=None, help="发布后的 exe 路径（默认 dist/DST-MC-I18n-Tool.exe）")
    parser.add_argument("--path", default=str(FIXTURES), help="工作目录（默认 tests/fixtures/instance）")
    parser.add_argument("--port", type=int, default=0, help="监听端口（默认随机空闲端口）")
    parser.add_argument("--timeout", type=float, default=60.0, help="等待服务就绪的秒数（默认 60）")
    parser.add_argument("--verbose", action="store_true", help="打印完整异常堆栈与进程日志")
    args = parser.parse_args(argv)

    fixture = Path(args.path).resolve()
    try:
        exe = resolve_exe(args.exe)
        ensure_fixtures(args.verbose)
    except SetupError as exc:
        print("[setup] " + str(exc), file=sys.stderr)
        return 2

    port = args.port or find_free_port()
    base_url = "http://127.0.0.1:" + str(port)

    print("DST MC I18n Tool e2e")
    print("  exe      : " + str(exe))
    print("  工作目录 : " + str(fixture))
    print("  监听     : " + base_url)

    reporter = Reporter(args.verbose)
    stdout_file = tempfile.NamedTemporaryFile(prefix="i18ntool-e2e-", suffix=".out", delete=False)
    stderr_file = tempfile.NamedTemporaryFile(prefix="i18ntool-e2e-", suffix=".err", delete=False)
    stdout_path = Path(stdout_file.name)
    stderr_path = Path(stderr_file.name)

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    proc = None
    started = time.time()
    try:
        proc = subprocess.Popen(
            [str(exe), "--path", str(fixture), "--port", str(port), "--no-browser"],
            cwd=str(exe.parent),
            stdout=stdout_file,
            stderr=stderr_file,
            creationflags=creationflags,
        )
        print("  进程     : pid=" + str(proc.pid))
        health = wait_for_health(base_url, proc, args.timeout)
        print("  就绪耗时 : " + format(time.time() - started, ".2f") + "s")
        run_checks(base_url, fixture, health, reporter)
    except SetupError as exc:
        print("[setup] " + str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        if args.verbose:
            traceback.print_exc()
        reporter.fail("服务启动", type(exc).__name__ + ": " + str(exc))
        out = read_tail(stdout_path)
        err = read_tail(stderr_path)
        if out:
            print("\n--- 进程 stdout（尾部）---\n" + out)
        if err:
            print("\n--- 进程 stderr（尾部）---\n" + err)
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        stdout_file.close()
        stderr_file.close()

    elapsed = time.time() - started
    print("\n" + "=" * 64)
    print("汇总: PASS=" + str(reporter.passed) + "  FAIL=" + str(reporter.failed) +
          "  WARN=" + str(reporter.warned) + "  用时=" + format(elapsed, ".2f") + "s")
    if reporter.failures:
        print("失败项:")
        for name, detail in reporter.failures:
            print("  - " + name + ": " + detail)
    if reporter.warnings:
        print("警告项:")
        for name, detail in reporter.warnings:
            print("  - " + name + ": " + detail)

    if args.verbose or reporter.failed:
        out = read_tail(stdout_path)
        err = read_tail(stderr_path)
        if out:
            print("\n--- 进程 stdout（尾部）---\n" + out)
        if err:
            print("\n--- 进程 stderr（尾部）---\n" + err)

    try:
        stdout_path.unlink()
        stderr_path.unlink()
    except OSError:
        pass

    if reporter.failed:
        print("\n结果: FAIL")
        return 1
    print("\n结果: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
