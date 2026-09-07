"""Чистая реализация MD4 (RFC 1320) — запасной вариант для NTLM на OpenSSL 3.

Проверена на всех тестовых векторах RFC (см. tests/test_md4.py).
"""
from __future__ import annotations

import copy
import struct

_MASK = 0xFFFFFFFF


def _f(x, y, z):
    return (x & y) | (~x & z)


def _g(x, y, z):
    return (x & y) | (x & z) | (y & z)


def _h(x, y, z):
    return x ^ y ^ z


def _rol(v, n):
    return ((v << n) & _MASK) | (v >> (32 - n))


class PureMD4:
    name = "md4"
    digest_size = 16
    block_size = 64

    def __init__(self, data: bytes | str = b""):
        self._buf = bytearray()
        self.update(data)

    def update(self, data: bytes | str) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._buf.extend(data)

    def digest(self) -> bytes:
        msg = bytearray(self._buf)
        bit_len = (len(msg) * 8) & 0xFFFFFFFFFFFFFFFF
        msg.append(0x80)
        while len(msg) % 64 != 56:
            msg.append(0)
        msg += struct.pack("<Q", bit_len)

        a, b, c, d = 0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476
        for off in range(0, len(msg), 64):
            x = struct.unpack("<16I", msg[off:off + 64])
            aa, bb, cc, dd = a, b, c, d
            for s, k in zip([3, 7, 11, 19] * 4, range(16)):
                a = _rol((a + _f(b, c, d) + x[k]) & _MASK, s)
                a, b, c, d = d, a, b, c
            for s, k in zip([3, 5, 9, 13] * 4, [0, 4, 8, 12, 1, 5, 9, 13, 2, 6, 10, 14, 3, 7, 11, 15]):
                a = _rol((a + _g(b, c, d) + x[k] + 0x5A827999) & _MASK, s)
                a, b, c, d = d, a, b, c
            for s, k in zip([3, 9, 11, 15] * 4, [0, 8, 4, 12, 2, 10, 6, 14, 1, 9, 5, 13, 3, 11, 7, 15]):
                a = _rol((a + _h(b, c, d) + x[k] + 0x6ED9EBA1) & _MASK, s)
                a, b, c, d = d, a, b, c
            a, b, c, d = (a + aa) & _MASK, (b + bb) & _MASK, (c + cc) & _MASK, (d + dd) & _MASK
        return struct.pack("<4I", a, b, c, d)

    def hexdigest(self) -> str:
        return self.digest().hex()

    def copy(self) -> "PureMD4":
        return copy.deepcopy(self)
