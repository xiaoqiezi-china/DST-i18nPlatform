"""Anvil region (.mca) reader for block entities and entities."""

from __future__ import annotations

import gzip
import os
import re
import struct
import zlib
from typing import Any

from . import nbt

REGION_NAME = re.compile(r"^r\.(-?\d+)\.(-?\d+)\.mca$", re.IGNORECASE)


class RegionError(ValueError):
    pass


class RegionFile:
    def __init__(self, path: str):
        self.path = path
        match = REGION_NAME.match(os.path.basename(path))
        if not match:
            raise RegionError("不是有效的区域文件: " + path)
        self.region_x = int(match.group(1))
        self.region_z = int(match.group(2))
        with open(path, "rb") as handle:
            self.header = handle.read(4096)
        if len(self.header) < 4096:
            raise RegionError("区域文件头不完整: " + path)

    def iter_chunks(self):
        for index in range(1024):
            entry = self.header[index * 4:index * 4 + 4]
            if len(entry) < 4:
                continue
            value = struct.unpack(">I", entry)[0]
            offset = (value >> 8) & 0xFFFFFF
            sector_count = value & 0xFF
            if offset == 0 or sector_count == 0:
                continue
            local_x = index % 32
            local_z = index // 32
            chunk_x = self.region_x * 32 + local_x
            chunk_z = self.region_z * 32 + local_z
            try:
                chunk = self._read_chunk(offset, chunk_x, chunk_z)
            except RegionError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise RegionError("读取区块 " + str(chunk_x) + "," + str(chunk_z) + " 失败: " + str(exc)) from exc
            if chunk is not None:
                yield chunk_x, chunk_z, chunk

    def _read_chunk(self, offset: int, chunk_x: int, chunk_z: int) -> Any | None:
        byte_offset = offset * 4096
        if byte_offset >= os.path.getsize(self.path):
            return None
        with open(self.path, "rb") as handle:
            handle.seek(byte_offset)
            header = handle.read(5)
            if len(header) < 5:
                return None
            length = struct.unpack(">I", header[:4])[0]
            if length == 0:
                return None
            compression = header[4]
            if length & 0x80000000:
                return self._read_external(chunk_x, chunk_z)
            if length < 1 or length > os.path.getsize(self.path) - byte_offset:
                raise RegionError("区块长度超出文件范围")
            payload = handle.read(length - 1)
        return _decode_chunk(payload, compression)

    def _read_external(self, chunk_x: int, chunk_z: int) -> Any | None:
        folder = os.path.dirname(self.path)
        name = "c." + str(chunk_x) + "." + str(chunk_z) + ".mcc"
        external = os.path.join(folder, name)
        if not os.path.isfile(external):
            raise RegionError("缺少外部区块文件: " + name)
        with open(external, "rb") as handle:
            data = handle.read()
        # External chunks are stored as a complete NBT payload, usually zlib-compressed.
        return _decode_chunk(data, 2)


def _decode_chunk(payload: bytes, compression: int) -> Any:
    if compression == 1:
        try:
            raw = gzip.decompress(payload)
        except OSError as exc:
            raise RegionError("gzip 区块解压失败: " + str(exc)) from exc
    elif compression == 2:
        try:
            raw = zlib.decompress(payload)
        except zlib.error as exc:
            raise RegionError("zlib 区块解压失败: " + str(exc)) from exc
    elif compression == 3:
        raw = payload
    elif compression == 4:
        raise RegionError("暂不支持 LZ4 压缩的区块")
    else:
        raise RegionError("未知区块压缩类型: " + str(compression))
    return nbt.parse_nbt(raw)
