#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 DST MC I18n Tool 的测试夹具（一个假的 Minecraft 整合包目录）。

只依赖 Python 标准库（json / zipfile / hashlib / shutil），不需要第三方包。
可重复执行：每次都会先清空目标目录再重建；jar 内的时间戳与权限位固定，
因此同一版本脚本生成的文件是字节级一致的。

用法：
    python tests/make_fixtures.py
    python tests/make_fixtures.py --out "D:/tmp/instance"

生成的目录结构（默认 tests/fixtures/instance）：
    version.json
    mods/CreateFake-1.0.0.jar      Fabric 风格，assets/createfake/lang/en_us.json（29 条）
    mods/ForgeFake.jar             Forge 风格，mods.toml + en_us/zh_cn
    mods/BrokenMod.jar             损坏的 jar（非合法 zip），用于健壮性测试
    kubejs/assets/kubejs/lang/en_us.json
    kubejs/assets/custom_ns/lang/en_us.json + zh_cn.json
    config/ftbquests/quests/lang/en_us.snbt
    config/ftbquests/quests/chapters/chapter1.snbt
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES_ROOT = ROOT / "tests" / "fixtures"
DEFAULT_OUT = FIXTURES_ROOT / "instance"


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


MARKER = ".i18ntool-fixtures"
ZIP_DATE_TIME = (2024, 1, 1, 0, 0, 0)
ZIP_FILE_MODE = 0o100644 << 16


# ---------------------------------------------------------------------------
# 语言文件内容
# ---------------------------------------------------------------------------

CREATE_FAKE_EN_US = {
    "item.createfake.cogwheel": "Cogwheel",
    "item.createfake.large_cogwheel": "Large Cogwheel",
    "item.createfake.shaft": "Shaft",
    "item.createfake.andesite_alloy": "Andesite Alloy",
    "block.createfake.andesite_casing": "Andesite Casing",
    "block.createfake.brass_casing": "Brass Casing",
    "block.createfake.copper_casing": "Copper Casing",
    "block.createfake.mechanical_press": "Mechanical Press",
    "block.createfake.mechanical_mixer": "Mechanical Mixer",
    "block.createfake.water_wheel": "Water Wheel",
    "block.createfake.windmill_bearing": "Windmill Bearing",
    "item.createfake.creative_motor": "Creative Motor",
    "createfake.tooltip.kinetic": "Stress: %s SU",
    "createfake.tooltip.speed": "Speed: %s RPM at %s%% efficiency",
    "createfake.tooltip.positional": "Moved %1$s to %2$s",
    "createfake.gui.title": "Mechanical Mixer",
    "createfake.gui.status.working": "§aWorking§r",
    "createfake.gui.status.stopped": "§cStopped§r",
    "createfake.gui.status.overstressed": "§4Overstressed§r - reduce the load on this kinetic network",
    "createfake.message.line_break": "Line one\nLine two\nLine three",
    "createfake.message.long": (
        "This is a deliberately long description that keeps going for quite a while so that "
        "translators have to deal with a whole paragraph instead of a couple of words, and it "
        "also mentions a placeholder %s in the middle of the text to make sure that token "
        "protection keeps working even in long sentences."
    ),
    "createfake.message.percent": "Progress: %d%%",
    "createfake.advancement.root.title": "Welcome to Create Fake",
    "createfake.advancement.root.desc": "Place your first cogwheel and start building machines.",
    "createfake.command.give.success": "Gave %s to %s",
    "createfake.config.enabled": "Enable experimental features",
    "createfake.jei.category.pressing": "Pressing",
    "createfake.nested": {
        "advanced": {
            "hint": "Hold §eShift§r for more information"
        }
    },
}

FORGE_FAKE_EN_US = {
    "item.forgefake.rune": "Rune",
    "item.forgefake.magic_dust": "Magic Dust",
    "block.forgefake.altar": "Rune Altar",
    "block.forgefake.mana_pool": "Mana Pool",
    "forgefake.tooltip.charge": "Charge: %s",
    "forgefake.tooltip.tier": "Tier %d",
    "forgefake.gui.title": "Rune Altar",
    "forgefake.message.no_mana": "§cNot enough mana!§r",
    "forgefake.jei.rune_crafting": "Rune Crafting",
    "forgefake.advancement.root.title": "Forged in Fire",
}

FORGE_FAKE_ZH_CN = {
    "item.forgefake.rune": "符文",
    "block.forgefake.altar": "符文祭坛",
    "forgefake.tooltip.charge": "充能: %s",
    "forgefake.message.no_mana": "§c法力不足！§r",
}

KUBEJS_EN_US = {
    "item.kubejs.custom_sword": "Custom Sword",
    "item.kubejs.custom_pickaxe": "Custom Pickaxe",
    "kubejs.ui.welcome": "Welcome to KubeJS",
    "kubejs.ui.tip": "Tip: %s",
    "kubejs.message.golden": "§6Golden text§r",
    "kubejs.message.two_lines": "First line\nSecond line",
}

CUSTOM_NS_EN_US = {
    "item.custom_ns.magic_wand": "Magic Wand",
    "block.custom_ns.rune_stone": "Rune Stone",
    "custom_ns.tooltip.energy": "Energy: %d FE",
    "custom_ns.message.cast": "Cast %s",
    "custom_ns.jei.category": "Rune Infusion",
}

CUSTOM_NS_ZH_CN = {
    "item.custom_ns.magic_wand": "魔法杖",
    "block.custom_ns.rune_stone": "符文石",
}

FORGE_FAKE_MODS_TOML = '''modLoader="javafml"
loaderVersion="[47,)"
license="MIT"
issueTrackerURL="https://example.invalid/forgefake/issues"

[[mods]]
modId="forgefake"
version="2.3.4"
displayName="Forge Fake"
description="A fake Forge mod used by the DST MC I18n Tool test suite."
authors="DST"

[[dependencies.forgefake]]
    modId="forge"
    mandatory=true
    versionRange="[47,)"
    ordering="NONE"
    side="BOTH"
'''

CREATE_FAKE_FABRIC_MOD_JSON = {
    "schemaVersion": 1,
    "id": "createfake",
    "version": "1.0.0",
    "name": "Create Fake",
    "description": "A fake Create-like mod used by the DST MC I18n Tool test suite.",
    "authors": ["DST"],
    "license": "MIT",
    "environment": "*",
    "depends": {
        "fabricloader": ">=0.15.0",
        "minecraft": "1.20.1",
        "java": ">=17"
    },
    "entrypoints": {
        "main": ["com.example.createfake.CreateFakeMod"]
    }
}

FTB_LANG_SNBT = '''{
    "quest.1.title": "Hello",
    "quest.1.description": "Welcome to the pack!",
    "quest.2.title": "Gather Wood",
    "quest.2.description": "Punch some trees to collect logs.",
    "quest.3.title": "Craft a Pickaxe",
    "quest.3.description": "Use your new pickaxe to mine stone.",
    "chapter.1.title": "Getting Started",
    "chapter.1.subtitle": "Your first steps",
    "chapter.2.title": "Machines",
    "chapter.2.subtitle": "Automate everything"
}
'''

FTB_CHAPTER_SNBT = '''{
    id: "chapter1",
    title: "Getting Started",
    icon: "minecraft:crafting_table",
    quests: [
        {
            id: "quest1",
            title: "Hello",
            description: [
                "Welcome to the pack!",
                "This is the very first quest."
            ],
            tasks: [
                { id: "task1", type: "item", item: "minecraft:oak_log", count: 8L }
            ]
        },
        {
            id: "quest2",
            title: "Gather Wood",
            description: ["Punch some trees to collect logs."],
            tasks: [
                { id: "task2", type: "item", item: "minecraft:oak_log", count: 16L }
            ]
        }
    ]
}
'''

VERSION_JSON = {"id": "1.20.1-forge-47.2.0"}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def json_text(obj) -> str:
    """序列化为 UTF-8 友好的缩进 JSON（中文不转义），结尾带换行。"""
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def write_jar(path: Path, entries) -> None:
    """写一个确定性的 zip/jar：固定时间戳、固定权限位。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name, data in entries:
            if isinstance(data, str):
                data = data.encode("utf-8")
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = ZIP_FILE_MODE
            zf.writestr(info, data)


def flatten_strings(node, prefix: str = ""):
    """把 JSON 结构里的字符串叶子展开成 (key, value)，与 C# JsonUtil.Flatten 对齐。"""
    out = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = prefix + "." + str(key) if prefix else str(key)
            out.extend(flatten_strings(value, child))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            out.extend(flatten_strings(value, prefix + "[" + str(index) + "]"))
    elif isinstance(node, str):
        out.append((prefix, node))
    return out


def reset_dir(target: Path) -> None:
    """清空并重建目标目录；只允许清空测试夹具目录，避免误删。"""
    target = target.resolve()
    under_fixtures = FIXTURES_ROOT.resolve() in target.parents
    has_marker = (target / MARKER).is_file()
    if target.exists():
        if not target.is_dir():
            raise SystemExit("目标已存在且不是目录: " + str(target))
        if not (under_fixtures or has_marker):
            raise SystemExit(
                "拒绝清空非测试夹具目录: " + str(target) + "\n"
                "（只有 tests/fixtures 下的目录，或带 " + MARKER + " 标记的目录才会被清空）"
            )
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    write_text(target / MARKER, "由 tests/make_fixtures.py 生成，可安全删除。\n")


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------

def build(out_dir: Path) -> None:
    reset_dir(out_dir)

    mods = out_dir / "mods"

    # 1) Fabric 风格 mod：fabric.mod.json + assets/<ns>/lang/en_us.json
    write_jar(mods / "CreateFake-1.0.0.jar", [
        ("fabric.mod.json", json_text(CREATE_FAKE_FABRIC_MOD_JSON)),
        ("assets/createfake/lang/en_us.json", json_text(CREATE_FAKE_EN_US)),
        ("assets/createfake/textures/item/cogwheel.png", b"\x89PNG\r\n\x1a\n fake png for fixture only"),
    ])

    # 2) Forge 风格 mod：META-INF/mods.toml + en_us/zh_cn
    write_jar(mods / "ForgeFake.jar", [
        ("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\nImplementation-Title: ForgeFake\n\n"),
        ("META-INF/mods.toml", FORGE_FAKE_MODS_TOML),
        ("assets/forgefake/lang/en_us.json", json_text(FORGE_FAKE_EN_US)),
        ("assets/forgefake/lang/zh_cn.json", json_text(FORGE_FAKE_ZH_CN)),
    ])

    # 3) 损坏的 jar：不是合法 zip，扫描时应被跳过并记录 warning
    write_bytes(
        mods / "BrokenMod.jar",
        b"This file pretends to be a jar but is definitely not a zip archive.\n" * 4,
    )

    # 非 jar 文件应被忽略
    write_text(mods / "optional-notes.txt", "这个文件不是 mod，扫描时应当被忽略。\n")

    # 4) KubeJS
    kubejs = out_dir / "kubejs" / "assets"
    write_text(kubejs / "kubejs" / "lang" / "en_us.json", json_text(KUBEJS_EN_US))
    write_text(kubejs / "custom_ns" / "lang" / "en_us.json", json_text(CUSTOM_NS_EN_US))
    write_text(kubejs / "custom_ns" / "lang" / "zh_cn.json", json_text(CUSTOM_NS_ZH_CN))

    # 5) FTB Quests（config/ftbquests）
    quests = out_dir / "config" / "ftbquests" / "quests"
    write_text(quests / "lang" / "en_us.snbt", FTB_LANG_SNBT)
    write_text(quests / "chapters" / "chapter1.snbt", FTB_CHAPTER_SNBT)
    write_text(quests / "data.snbt", '{ version: 12, default_quest_shape: "" }\n')

    # 6) 版本信息
    write_text(out_dir / "version.json", json_text(VERSION_JSON))

    self_check(out_dir)


def self_check(out_dir: Path) -> None:
    """基本自检：jar 可读、条目数达到预期。"""
    expected = [
        ("CreateFake en_us 条目", len(flatten_strings(CREATE_FAKE_EN_US)), 20),
        ("ForgeFake en_us 条目", len(flatten_strings(FORGE_FAKE_EN_US)), 8),
        ("KubeJS en_us 条目", len(flatten_strings(KUBEJS_EN_US)), 5),
        ("custom_ns en_us 条目", len(flatten_strings(CUSTOM_NS_EN_US)), 4),
    ]
    problems = []
    for label, actual, minimum in expected:
        if actual < minimum:
            problems.append(label + ": " + str(actual) + " < " + str(minimum))

    for jar in [out_dir / "mods" / "CreateFake-1.0.0.jar", out_dir / "mods" / "ForgeFake.jar"]:
        try:
            with zipfile.ZipFile(jar) as zf:
                if zf.testzip() is not None:
                    problems.append("jar 校验失败: " + jar.name)
        except zipfile.BadZipFile as exc:
            problems.append("jar 无法打开: " + jar.name + " (" + str(exc) + ")")

    try:
        with zipfile.ZipFile(out_dir / "mods" / "BrokenMod.jar"):
            problems.append("BrokenMod.jar 不应该是一个合法的 zip")
    except zipfile.BadZipFile:
        pass

    for required in [
        out_dir / "version.json",
        out_dir / "mods" / "CreateFake-1.0.0.jar",
        out_dir / "mods" / "ForgeFake.jar",
        out_dir / "mods" / "BrokenMod.jar",
        out_dir / "kubejs" / "assets" / "kubejs" / "lang" / "en_us.json",
        out_dir / "kubejs" / "assets" / "custom_ns" / "lang" / "en_us.json",
        out_dir / "config" / "ftbquests" / "quests" / "lang" / "en_us.snbt",
        out_dir / "config" / "ftbquests" / "quests" / "chapters" / "chapter1.snbt",
    ]:
        if not required.is_file():
            problems.append("缺少文件: " + str(required.relative_to(out_dir)))

    if problems:
        raise SystemExit("夹具自检失败:\n  - " + "\n  - ".join(problems))


def describe(out_dir: Path) -> None:
    files = sorted(p for p in out_dir.rglob("*") if p.is_file())
    total = sum(p.stat().st_size for p in files)
    print()
    print("生成的文件:")
    for path in files:
        rel = path.relative_to(out_dir).as_posix()
        size = path.stat().st_size
        extra = ""
        if path.suffix.lower() == ".jar" and path.name != "BrokenMod.jar":
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
            extra = "  sha256=" + digest
        print("  " + rel.ljust(54) + str(size).rjust(7) + " B" + extra)
    print("  共 " + str(len(files)) + " 个文件, " + str(total) + " 字节")
    print()
    print("语言条目统计:")
    print("  CreateFake en_us : " + str(len(flatten_strings(CREATE_FAKE_EN_US))))
    print("  ForgeFake en_us  : " + str(len(flatten_strings(FORGE_FAKE_EN_US))))
    print("  ForgeFake zh_cn  : " + str(len(flatten_strings(FORGE_FAKE_ZH_CN))))
    print("  KubeJS en_us     : " + str(len(flatten_strings(KUBEJS_EN_US))))
    print("  custom_ns en_us  : " + str(len(flatten_strings(CUSTOM_NS_EN_US))))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成 DST MC I18n Tool 的测试夹具")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录（默认 tests/fixtures/instance）")
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = (Path.cwd() / out_dir).resolve()

    build(out_dir)
    describe(out_dir)
    print()
    print("夹具生成完成: " + str(out_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
