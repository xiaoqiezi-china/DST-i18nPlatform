"""Command line entry point for the map localization engine."""

from __future__ import annotations

import argparse
import json
import os
import sys

from dst_i18n.mapscan import scan_map


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="map_scan",
        description="扫描 Minecraft 地图存档，提取可能需要汉化的文本。",
    )
    parser.add_argument("map", help="地图/存档目录（包含 level.dat、dimensions/**/region 或 datapacks/）")
    parser.add_argument("--json", dest="json_path", help="把完整结果写入 JSON 文件")
    parser.add_argument("--source", help="只显示指定来源，逗号分隔，例如 lang,function,sign")
    parser.add_argument("--no-world", action="store_true", help="跳过 region/NBT，只扫描数据包")
    parser.add_argument("--no-packs", action="store_true", help="跳过数据包/资源包")
    parser.add_argument("--examples", type=int, default=8, help="每种来源显示多少条示例（默认 8）")
    parser.add_argument("--quiet", action="store_true", help="只输出统计，不打印示例")
    args = parser.parse_args(argv)

    root = os.path.abspath(args.map)
    if not os.path.isdir(root):
        print("目录不存在: " + root, file=sys.stderr)
        return 2

    result = scan_map(root, include_world=not args.no_world, include_packs=not args.no_packs)
    sources = None
    if args.source:
        sources = {item.strip() for item in args.source.split(",") if item.strip()}
        result.candidates = [c for c in result.candidates if c.source in sources]
        result.finalize()

    if args.json_path:
        output = os.path.abspath(args.json_path)
        with open(output, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(result.to_dict(), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        print("JSON 已写入: " + output)

    print("地图目录: " + result.root)
    print("候选文本: " + str(len(result.candidates)))
    for source, count in sorted(result.stats.items()):
        if source in ("total", "translated", "untranslated"):
            continue
        print("  " + source + ": " + str(count))
    print("语言文件: " + str(len(result.language_files)))
    print("数据包: " + str(len(result.packs)))
    if result.warnings:
        print("警告: " + str(len(result.warnings)))
        for warning in result.warnings[:5]:
            print("  - " + warning)

    if not args.quiet and result.candidates:
        print("\n示例:")
        shown: dict[str, int] = {}
        for candidate in result.candidates:
            if shown.get(candidate.source, 0) >= args.examples:
                continue
            shown[candidate.source] = shown.get(candidate.source, 0) + 1
            location = candidate.file
            if candidate.line:
                location += ":" + str(candidate.line)
            if candidate.nbt_path:
                location += " " + candidate.nbt_path
            print("  [" + candidate.source + "] " + candidate.text + "  <- " + location)
    return 0


if __name__ == "__main__":
    sys.exit(main())
