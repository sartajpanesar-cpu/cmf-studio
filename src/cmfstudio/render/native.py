"""Native (structured) watch faces: the firmware itself draws the live time and data from images in the file.

Format (refs/CMF-Watch-Pro-2-BLE-Protocol README §11.4–11.11; Gadgetbridge CmfFwHelper checks the same invariants):

    [36-byte header][scene TLV: 0x20 <u16 len> …][asset pool][header again]
    header: crc_tree u32 · magic 01 00 00 (00|02) · name[16] · size_a u32 (= file − 36) · size_b u32 (= pool length)
            · crc_assets u32
    asset:  dimsWord u32 (cf = &0x1f, w = >>10 & 0x7ff, h = >>21 & 0x7ff) · len u32 · LZ4 block (len bytes)

The scene is a nested TLV ``[tag u8][len u16][body]`` (§11.11 lists every tag; containers recurse). Nodes point at
their images through a **ref tail** ``61|71 · count u16 · base u32 (absolute file offset) · count × u16 block size``
at +0x12 of a 0x01 struct body (+5 of a 0x08 preview struct): the firmware reads the block at ``base``, then steps by
the listed sizes, so a node's images are consecutive. A 10/11-image tail on glyph sprites is a digit set.

Editing: replacements are collected, then ``build()`` lays the asset pool out again — every block packed in order,
each ref tail's base and sizes rewritten to the new positions, header sizes and both CRCs recomputed (crc_assets
first: it is inside crc_tree's range). The scene keeps its exact bytes otherwise, so the layout, data bindings and
conditions are the template's own. The result is re-parsed and every chain is checked before it is returned.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from functools import cached_property

import lz4.block
import numpy as np

HEADER = 36
# §11.11: tags whose bodies hold more TLVs. Everything else is a leaf (0x01 struct, 0x08 preview struct, …).
CONTAINERS = {0x20, 0x21, 0x22, 0x28, 0x68, 0x30, 0x60, 0x70, 0x80, 0x81, 0x85}
AOD_TAG = 0x22
TAIL_TYPES = (0x61, 0x71)
BPP = {4: 2, 5: 3, 24: 4}
MAX_FILE = 340_000  # the largest store dial in the spec's corpus is ~337 KB


class NativeFormatError(ValueError):
    pass


def crc32_raw(data: bytes) -> int:
    """Reflected 0xEDB88320, init 0, no final XOR (spec §11.4) — zlib's CRC with the inversions undone."""
    return zlib.crc32(data, 0xFFFFFFFF) ^ 0xFFFFFFFF


def decoded_size(cf: int, w: int, h: int) -> int | None:
    if cf == 13:
        return (w * h + 1) // 2
    return w * h * BPP[cf] if cf in BPP else None


@dataclass(frozen=True)
class Asset:
    off: int  # absolute offset of the dimsWord
    cf: int
    w: int
    h: int
    len: int  # stored LZ4 length


@dataclass(frozen=True)
class RefTail:
    at: int  # offset of the 0x61/0x71 byte
    count: int
    base: int
    aod: bool  # inside the always-on (0x22) screen

    def sizes(self, b: bytes | bytearray) -> list[int]:
        return [_u16(b, self.at + 7 + 2 * k) for k in range(self.count)]


def _u16(b: bytes | bytearray, o: int) -> int:
    return struct.unpack_from("<H", b, o)[0]


def _u32(b: bytes | bytearray, o: int) -> int:
    return struct.unpack_from("<I", b, o)[0]


class NativeDial:
    def __init__(self, data: bytes) -> None:
        """Input CRCs aren't checked: the firmware doesn't enforce them and re-skinned templates carry stale ones.
        ``build()`` always writes correct CRCs and validates strictly."""
        self.b = bytes(data)
        problems = validate(self.b, check_crc=False)
        if problems:
            raise NativeFormatError("; ".join(problems))
        self._new: dict[int, bytes] = {}  # asset offset -> new LZ4 block
        self._moves: dict[int, tuple[int, int]] = {}  # 0x01 struct body offset -> new (x, y)

    # -- structure ---------------------------------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self.b[8:24].split(b"\0", 1)[0].decode("utf-8", "replace")

    @property
    def first_asset(self) -> int:
        return len(self.b) - HEADER - _u32(self.b, 0x1C)

    @cached_property
    def assets(self) -> dict[int, Asset]:
        """Every decodable image block in the pool (blocks may be separated by zero padding)."""
        b, end = self.b, len(self.b) - HEADER
        out: dict[int, Asset] = {}
        i = self.first_asset
        while i + 8 <= end:
            dw = _u32(b, i)
            cf, w, h = dw & 0x1F, (dw >> 10) & 0x7FF, (dw >> 21) & 0x7FF
            need = decoded_size(cf, w, h)
            if need and 1 <= w <= 466 and 1 <= h <= 466:
                n = _u32(b, i + 4)
                if 4 <= n and i + 8 + n <= end and _decodes(b[i + 8 : i + 8 + n], need):
                    out[i] = Asset(i, cf, w, h, n)
                    i += 8 + n
                    continue
            i += 1
        return out

    @cached_property
    def tails(self) -> list[RefTail]:
        b, out = self.b, []

        def walk(off: int, end: int, aod: bool) -> None:
            while off < end:
                tag, n = b[off], _u16(b, off + 1)
                body = off + 3
                if tag in CONTAINERS:
                    walk(body, body + n, aod or tag == AOD_TAG)
                elif tag in (0x01, 0x08):
                    at = body + (0x12 if tag == 0x01 else 5)
                    if at + 7 <= body + n and b[at] in TAIL_TYPES:
                        out.append(RefTail(at, _u16(b, at + 1), _u32(b, at + 3), aod))
                off = body + n

        walk(HEADER, self.first_asset, False)
        return out

    def chain(self, tail: RefTail) -> list[Asset]:
        """The images a ref tail points at, following its block sizes like the firmware does."""
        out, p = [], tail.base
        for k, size in enumerate(tail.sizes(self.b)):
            asset = self.assets.get(p)
            if asset is None:
                raise NativeFormatError(f"ref tail at {tail.at:#x} entry {k} points at {p:#x}, not an image")
            out.append(asset)
            p += size
        return out

    def digit_sets(self) -> list[tuple[RefTail, list[Asset]]]:
        """Distinct digit sets ('0'..'9' [+ an extra glyph]) on glyph sprites, each with its first tail."""
        seen: dict[int, tuple[RefTail, list[Asset]]] = {}
        for t in self.tails:
            if t.count in (10, 11) and t.base not in seen:
                frames = self.chain(t)
                if all(f.cf in (5, 13) for f in frames):
                    seen[t.base] = (t, frames[:10])
        return list(seen.values())

    def backgrounds(self, aod: bool = False) -> list[Asset]:
        return self._single_images(466, aod)

    def thumbnails(self, aod: bool = False) -> list[Asset]:
        return self._single_images(270, aod)

    def _single_images(self, size: int, aod: bool) -> list[Asset]:
        found: dict[int, Asset] = {}
        for t in self.tails:
            if t.aod == aod and t.count == 1:
                a = self.assets.get(t.base)
                if a and a.cf == 4 and a.w == a.h == size:
                    found[a.off] = a
        return list(found.values())

    # -- pixels ---------------------------------------------------------------------------------------------------

    def decode(self, asset: Asset) -> np.ndarray:
        """RGBA uint8 (h, w, 4) — the replacement if one was set, else the template's image."""
        block = self._new.get(asset.off) or self.b[asset.off + 8 : asset.off + 8 + asset.len]
        raw = lz4.block.decompress(block, uncompressed_size=decoded_size(asset.cf, asset.w, asset.h))
        return raster_to_rgba(raw, asset.cf, asset.w, asset.h)

    def replace(self, asset: Asset, rgba: np.ndarray) -> int:
        """Use ``rgba`` (h, w, 4) for ``asset`` (same size and format). Returns the compressed size."""
        if rgba.shape[:2] != (asset.h, asset.w):
            raise NativeFormatError(f"image is {rgba.shape[1]}×{rgba.shape[0]}, slot is {asset.w}×{asset.h}")
        block = compress(rgba_to_raster(rgba, asset.cf))
        self._new[asset.off] = block
        return len(block)

    def move(self, struct_body: int, x: int, y: int) -> None:
        """Re-position a node (its 0x01 struct's x, y: i16 top-left). Same bytes, same length."""
        if not (-1000 <= x <= 1000 and -1000 <= y <= 1000):
            raise NativeFormatError(f"position {x},{y} is off the canvas")
        self._moves[struct_body] = (x, y)

    def position(self, struct_body: int) -> tuple[int, int]:
        return self._moves.get(struct_body) or struct.unpack_from("<hh", self.b, struct_body)

    # -- output ---------------------------------------------------------------------------------------------------

    def build(self) -> bytes:
        """Pack the pool, re-point every ref tail, fix the header and CRCs, then verify the result."""
        b = self.b
        old_first, end = self.first_asset, len(b) - HEADER
        pool = bytearray()
        moved: dict[int, tuple[int, int]] = {}  # old offset -> (new offset, new block size)
        pos = old_first
        for off in sorted(self.assets):
            if off > pos and any(b[pos:off]):  # non-zero bytes between blocks: unknown data, keep it in place
                raise NativeFormatError(f"unrecognised data in the asset pool at {pos:#x}")
            a = self.assets[off]
            block = self._new.get(off, b[off + 8 : off + 8 + a.len])
            moved[off] = (old_first + len(pool), 8 + len(block))
            pool += struct.pack("<II", _u32(b, off), len(block)) + block
            pos = off + 8 + a.len
        if any(b[pos:end]):
            raise NativeFormatError(f"unrecognised data at the end of the asset pool ({pos:#x})")

        out = bytearray(b[:old_first]) + pool + bytes(HEADER)
        for body, (x, y) in self._moves.items():
            struct.pack_into("<hh", out, body, x, y)
        for t in self.tails:
            frames = self.chain(t)
            struct.pack_into("<I", out, t.at + 3, moved[frames[0].off][0])
            for k, f in enumerate(frames):
                size = moved[f.off][1]
                if size > 0xFFFF:
                    if k < t.count - 1:  # only the last size is never followed (§11.11)
                        raise NativeFormatError(f"image {f.w}×{f.h} is {size} B; chained images must be < 64 KB")
                    size = 0xFFFF
                struct.pack_into("<H", out, t.at + 7 + 2 * k, size)

        struct.pack_into("<I", out, 0x18, len(out) - HEADER)
        struct.pack_into("<I", out, 0x1C, len(pool))
        struct.pack_into("<I", out, 0x20, crc32_raw(bytes(pool)))
        struct.pack_into("<I", out, 0x00, crc32_raw(bytes(out[4:old_first])))
        out[-HEADER:] = out[:HEADER]
        result = bytes(out)
        if len(result) > MAX_FILE:
            raise NativeFormatError(f"face is {len(result) // 1024} KB; keep it under {MAX_FILE // 1024} KB")
        self._verify(result)
        return result

    def _verify(self, data: bytes) -> None:
        problems = validate(data)
        if problems:
            raise NativeFormatError("; ".join(problems))
        check = NativeDial(data)
        if len(check.tails) != len(self.tails):
            raise NativeFormatError("rebuilt scene lost ref tails")
        for old, new in zip(self.tails, check.tails, strict=True):
            for a, z in zip(self.chain(old), check.chain(new), strict=True):  # chain() raises on a bad pointer
                if (a.cf, a.w, a.h) != (z.cf, z.w, z.h):
                    raise NativeFormatError(f"ref tail at {old.at:#x} now points at a different image")


def _decodes(block: bytes, need: int) -> bool:
    try:
        return len(lz4.block.decompress(block, uncompressed_size=need)) == need
    except lz4.block.LZ4BlockError:
        return False


def compress(raw: bytes) -> bytes:
    return lz4.block.compress(raw, mode="high_compression", compression=12, store_size=False)


def validate(data: bytes, check_crc: bool = True) -> list[str]:
    """Everything the firmware is known to check (spec §11.4/§11.7, Gadgetbridge CmfFwHelper). [] = valid."""
    problems: list[str] = []
    if len(data) < HEADER * 2 + 3:
        return ["file too small"]
    if data[4:7] != b"\x01\x00\x00" or data[7] not in (0, 2):
        problems.append("not a structured dial (magic)")
    if data[:HEADER] != data[-HEADER:]:
        problems.append("footer differs from header")
    if _u32(data, 0x18) != len(data) - HEADER:
        problems.append("size_a doesn't match the file length")
    if data[HEADER] != 0x20:
        return [*problems, "body doesn't start with the 0x20 scene container"]
    first_asset = HEADER + 3 + _u16(data, HEADER + 1)
    if first_asset + _u32(data, 0x1C) != len(data) - HEADER:
        problems.append("asset pool doesn't line up with the footer")
    if check_crc and crc32_raw(data[first_asset : len(data) - HEADER]) != _u32(data, 0x20):
        problems.append("asset CRC mismatch")
    if check_crc and crc32_raw(data[4:first_asset]) != _u32(data, 0x00):
        problems.append("header CRC mismatch")

    def walk(off: int, end: int) -> None:
        while off < end:
            if off + 3 > end:
                problems.append(f"truncated TLV at {off:#x}")
                return
            tag, n = data[off], _u16(data, off + 1)
            if off + 3 + n > end:
                problems.append(f"TLV {tag:#04x} at {off:#x} overruns its parent")
                return
            if tag in CONTAINERS:
                walk(off + 3, off + 3 + n)
            off += 3 + n

    walk(HEADER, first_asset)
    return problems


# -- rasters (cf 4: RGB565-LE · 5: RGB565-LE + alpha · 13: 4-bit alpha, even pixel = low nibble · 24: RGBA) -----


def raster_to_rgba(raw: bytes, cf: int, w: int, h: int) -> np.ndarray:
    n = w * h
    a = np.frombuffer(raw, dtype=np.uint8)
    out = np.zeros((n, 4), dtype=np.uint8)
    if cf == 13:
        nib = np.empty(len(a) * 2, dtype=np.uint8)
        nib[0::2], nib[1::2] = a & 0x0F, a >> 4
        out[:, :3] = 255
        out[:, 3] = nib[:n] * 17
    elif cf == 24:
        out[:] = a.reshape(n, 4)
    else:
        bpp = BPP[cf]
        px = a.reshape(n, bpp)
        v = px[:, 0].astype(np.uint16) | (px[:, 1].astype(np.uint16) << 8)
        out[:, 0] = ((v >> 11) & 0x1F) * 255 // 31
        out[:, 1] = ((v >> 5) & 0x3F) * 255 // 63
        out[:, 2] = (v & 0x1F) * 255 // 31
        out[:, 3] = px[:, 2] if cf == 5 else 255
    return out.reshape(h, w, 4)


def rgba_to_raster(rgba: np.ndarray, cf: int) -> bytes:
    px = rgba.reshape(-1, 4).astype(np.uint16)
    if cf == 13:
        nib = (px[:, 3] + 8) // 17
        if len(nib) % 2:
            nib = np.append(nib, 0)
        return ((nib[0::2] & 0x0F) | ((nib[1::2] & 0x0F) << 4)).astype(np.uint8).tobytes()
    if cf == 24:
        return rgba.astype(np.uint8).tobytes()
    v = ((px[:, 0] >> 3) << 11) | ((px[:, 1] >> 2) << 5) | (px[:, 2] >> 3)
    cols = [v & 0xFF, v >> 8] + ([px[:, 3]] if cf == 5 else [])
    return np.stack(cols, axis=1).astype(np.uint8).tobytes()
