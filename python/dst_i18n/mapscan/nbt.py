"""Minimal big-endian NBT reader for world saves and structure files."""

from __future__ import annotations

import gzip
import os
import struct
import zlib
from typing import Any

TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12

MAX_DEPTH = 128


class NbtError(ValueError):
    pass


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.size = len(data)

    def _take(self, count: int) -> bytes:
        if count < 0 or self.pos + count > self.size:
            raise NbtError("NBT 数据在偏移 " + str(self.pos) + " 处提前结束")
        chunk = self.data[self.pos:self.pos + count]
        self.pos += count
        return chunk

    def _u8(self) -> int:
        return self._take(1)[0]

    def _i16(self) -> int:
        return struct.unpack(">h", self._take(2))[0]

    def _u16(self) -> int:
        return struct.unpack(">H", self._take(2))[0]

    def _i32(self) -> int:
        return struct.unpack(">i", self._take(4))[0]

    def _i64(self) -> int:
        return struct.unpack(">q", self._take(8))[0]

    def _f32(self) -> float:
        return struct.unpack(">f", self._take(4))[0]

    def _f64(self) -> float:
        return struct.unpack(">d", self._take(8))[0]

    def _string(self) -> str:
        length = self._u16()
        if length == 0:
            return ""
        return self._take(length).decode("utf-8", errors="replace")

    def read_payload(self, tag_type: int, depth: int) -> Any:
        if depth > MAX_DEPTH:
            raise NbtError("NBT 嵌套层数超过限制")
        if tag_type == TAG_END:
            raise NbtError("TAG_End 不能作为独立值")
        if tag_type == TAG_BYTE:
            return struct.unpack(">b", self._take(1))[0]
        if tag_type == TAG_SHORT:
            return self._i16()
        if tag_type == TAG_INT:
            return self._i32()
        if tag_type == TAG_LONG:
            return self._i64()
        if tag_type == TAG_FLOAT:
            return self._f32()
        if tag_type == TAG_DOUBLE:
            return self._f64()
        if tag_type == TAG_BYTE_ARRAY:
            length = self._i32()
            return self._take(length)
        if tag_type == TAG_STRING:
            return self._string()
        if tag_type == TAG_LIST:
            element_type = self._u8()
            length = self._i32()
            if length < 0:
                raise NbtError("NBT 列表长度为负数")
            if element_type == TAG_END and length > 0:
                raise NbtError("NBT 列表元素类型为 TAG_End")
            return [self.read_payload(element_type, depth + 1) for _ in range(length)]
        if tag_type == TAG_COMPOUND:
            value: dict[str, Any] = {}
            while True:
                child_type = self._u8()
                if child_type == TAG_END:
                    return value
                name = self._string()
                value[name] = self.read_payload(child_type, depth + 1)
        if tag_type == TAG_INT_ARRAY:
            length = self._i32()
            return [self._i32() for _ in range(max(0, length))]
        if tag_type == TAG_LONG_ARRAY:
            length = self._i32()
            return [self._i64() for _ in range(max(0, length))]
        raise NbtError("未知 NBT 标签类型: " + str(tag_type))

    def read_root(self) -> Any:
        tag_type = self._u8()
        if tag_type != TAG_COMPOUND:
            raise NbtError("NBT 根标签不是 TAG_Compound")
        self._string()
        return self.read_payload(TAG_COMPOUND, 0)


def parse_nbt(data: bytes, decompress: bool = False) -> Any:
    """Parse raw NBT bytes. Set decompress=True for .dat files."""
    if decompress:
        data = decompress_nbt(data)
    return _Reader(data).read_root()


def decompress_nbt(data: bytes) -> bytes:
    if not data:
        raise NbtError("空的 NBT 文件")
    if data[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(data)
        except OSError as exc:
            raise NbtError("gzip 解压失败: " + str(exc)) from exc
    if len(data) >= 2 and data[0] == 0x78 and ((data[0] << 8) | data[1]) % 31 == 0:
        try:
            return zlib.decompress(data)
        except zlib.error:
            pass
    return data


def load_nbt(data: bytes) -> Any:
    return parse_nbt(decompress_nbt(data))


def read_nbt_file(path: str) -> Any:
    with open(path, "rb") as handle:
        return load_nbt(handle.read())


def iter_compounds(node: Any):
    """Yield every compound dict in an NBT tree."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from iter_compounds(value)
    elif isinstance(node, list):
        for value in node:
            yield from iter_compounds(value)


def file_kind(path: str) -> str:
    return os.path.splitext(path)[1].lower()
