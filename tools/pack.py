#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把发布目录打成可分发的 zip，并在写出后立刻自检。

为什么不用 Compress-Archive：PowerShell 那个实现在文件数多、体积大时会产出
没有中央目录结尾记录（EOCD）的截断包——2026-11 发 1.1.0 时就是这么坏掉的，
附件上传成功、下载也返回 200，解压时才炸。这里用标准库 zipfile，写完必须
通过四项检查才算成功，任何一项不过就以非零码退出。

用法：
    python tools/pack.py <源目录> <输出 zip>

退出码：0 成功；1 打包或自检失败。
"""

from __future__ import annotations

import hashlib
import os
import sys
import zipfile
from typing import NoReturn

# 打包时的压缩级别：1 是「快」而不是「小」。这个包 100MB 出头、三百多个文件，
# 主要是 dll，压缩率本来就不高，用 1 能把打包时间压到几秒。
COMPRESS_LEVEL = 1

# 中央目录结尾记录固定 22 字节（无注释时），向前找 66 KB 足够覆盖。
EOCD_MAGIC = b"PK\x05\x06"
EOCD_SEARCH = 66 * 1024

# 一个正常的自包含发布至少有几百个文件，低于这个数多半是 publish 没跑完。
MIN_ENTRIES = 100


def fail(message: str) -> NoReturn:
    print("打包失败：" + message, file=sys.stderr)
    sys.exit(1)


def collect(source: str) -> list[tuple[str, str]]:
    """返回 (磁盘路径, 包内路径) 列表。包内路径统一用 /，且不含外层目录。"""
    items: list[tuple[str, str]] = []
    for root, _dirs, files in os.walk(source):
        for name in files:
            full = os.path.join(root, name)
            arc = os.path.relpath(full, source).replace(os.sep, "/")
            items.append((full, arc))
    # 固定顺序：同样的输入永远得到同样的条目顺序，便于比对
    items.sort(key=lambda pair: pair[1])
    return items


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2

    source = os.path.abspath(sys.argv[1])
    target = os.path.abspath(sys.argv[2])

    if not os.path.isdir(source):
        fail("源目录不存在：" + source)

    items = collect(source)
    if not items:
        fail("源目录是空的：" + source)

    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    if os.path.exists(target):
        os.remove(target)

    total_bytes = 0
    print(f"==> 打包 {len(items)} 个文件 -> {target}")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=COMPRESS_LEVEL) as archive:
        for full, arc in items:
            archive.write(full, arc)
            total_bytes += os.path.getsize(full)

    # ---- 自检 ----
    size = os.path.getsize(target)
    print(f"==> 源文件共 {total_bytes:,} 字节，压缩后 {size:,} 字节")

    # 1) 尾部必须有 EOCD。没有就是截断包，绝不能发出去。
    with open(target, "rb") as handle:
        handle.seek(max(0, size - EOCD_SEARCH))
        tail = handle.read()
    if EOCD_MAGIC not in tail:
        fail("找不到中央目录结尾记录，产出的 zip 是截断的")

    # 2) 能正常打开，且条目数对得上
    try:
        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
            if len(names) != len(items):
                fail(f"条目数不一致：写入 {len(items)}，读回 {len(names)}")
            if len(names) < MIN_ENTRIES:
                fail(f"条目只有 {len(names)} 个，少于预期的 {MIN_ENTRIES}，发布目录可能不完整")
            # 3) 逐条解压校验 CRC
            broken = archive.testzip()
            if broken is not None:
                fail("条目损坏：" + broken)
            # 4) 主程序必须在包根目录
            if "DST-MC-I18n-Tool.exe" not in names:
                fail("包根目录里没有 DST-MC-I18n-Tool.exe")
    except zipfile.BadZipFile as error:
        fail(f"产出的 zip 无法读取：{error}")

    digest = hashlib.sha256()
    with open(target, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    print(f"==> 自检通过：{len(names)} 个条目")
    print(f"==> SHA-256：{digest.hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
