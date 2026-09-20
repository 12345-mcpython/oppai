"""标准 DES（ECB）：纯 Python 参考实现 + 可选的 libcrypto 加速。

客户端 `util/crypt.js` 里的 desEncode/desDecode 就是标准 DES：
密钥 8 字节，明文按 8 字节分组，不足补位（填充方式见 `des_encode`）。

## 为什么 DES 是这套服务端的性能命门

响应是 `base64(des(JSON))`，**每个请求都要把整包加密一遍**，而登录包很大：
本档明文 219.8 KB（`instance` 一个块就 92 KB）。实测（`script/bench_login.py --parts`）：
加密占 98%，组包 + JSON + base64 加起来不到 2%。所以「界面能点多快」基本就是
「DES 能跑多快」。

## 两条路线

1. **纯 Python**（本文件的 `DES`）：查表写法（S 盒 + P 置换预合成，见下面 `_SP`），
   本机 ~157 KB/s。这是**参考实现**，也是没有 C 实现时的兜底。
2. **libcrypto**（`ctypes` 加载 CPython 自带的 `libcrypto-3.dll` 或系统 OpenSSL）：
   本机 ~17 MB/s（220 KB 只要 13 ms），比纯 Python 快两个数量级。

libcrypto 是**可选加速**，不是硬依赖：第一次用到时用一条公开的已知答案向量
（`_KAT_*`）验一遍加解密，验不过就自动退回纯 Python，只写一行日志。
`GS_DES_PURE=1` 可以强制走纯 Python（`script/check_des.py` 会两条都验）。

⚠️ **两条路线必须逐字节等价**（客户端按标准 DES 解，错一位整个协议就崩）：
`out/des_ref.json` 存了参考向量，`script/check_des.py` 拿它比对 + 测速率。
"""

from __future__ import annotations

import ctypes
import glob
import os
import sys

_IP = [
    58, 50, 42, 34, 26, 18, 10, 2, 60, 52, 44, 36, 28, 20, 12, 4,
    62, 54, 46, 38, 30, 22, 14, 6, 64, 56, 48, 40, 32, 24, 16, 8,
    57, 49, 41, 33, 25, 17, 9, 1, 59, 51, 43, 35, 27, 19, 11, 3,
    61, 53, 45, 37, 29, 21, 13, 5, 63, 55, 47, 39, 31, 23, 15, 7,
]

_FP = [
    40, 8, 48, 16, 56, 24, 64, 32, 39, 7, 47, 15, 55, 23, 63, 31,
    38, 6, 46, 14, 54, 22, 62, 30, 37, 5, 45, 13, 53, 21, 61, 29,
    36, 4, 44, 12, 52, 20, 60, 28, 35, 3, 43, 11, 51, 19, 59, 27,
    34, 2, 42, 10, 50, 18, 58, 26, 33, 1, 41, 9, 49, 17, 57, 25,
]

_E = [
    32, 1, 2, 3, 4, 5, 4, 5, 6, 7, 8, 9, 8, 9, 10, 11, 12, 13, 12, 13, 14, 15, 16, 17,
    16, 17, 18, 19, 20, 21, 20, 21, 22, 23, 24, 25, 24, 25, 26, 27, 28, 29, 28, 29, 30, 31, 32, 1,
]

_P = [
    16, 7, 20, 21, 29, 12, 28, 17, 1, 15, 23, 26, 5, 18, 31, 10,
    2, 8, 24, 14, 32, 27, 3, 9, 19, 13, 30, 6, 22, 11, 4, 25,
]

_PC1 = [
    57, 49, 41, 33, 25, 17, 9, 1, 58, 50, 42, 34, 26, 18,
    10, 2, 59, 51, 43, 35, 27, 19, 11, 3, 60, 52, 44, 36,
    63, 55, 47, 39, 31, 23, 15, 7, 62, 54, 46, 38, 30, 22,
    14, 6, 61, 53, 45, 37, 29, 21, 13, 5, 28, 20, 12, 4,
]

_PC2 = [
    14, 17, 11, 24, 1, 5, 3, 28, 15, 6, 21, 10,
    23, 19, 12, 4, 26, 8, 16, 7, 27, 20, 13, 2,
    41, 52, 31, 37, 47, 55, 30, 40, 51, 45, 33, 48,
    44, 49, 39, 56, 34, 53, 46, 42, 50, 36, 29, 32,
]

_SHIFTS = [1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1]

_SBOX = [
    [
        14, 4, 13, 1, 2, 15, 11, 8, 3, 10, 6, 12, 5, 9, 0, 7,
        0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11, 9, 5, 3, 8,
        4, 1, 14, 8, 13, 6, 2, 11, 15, 12, 9, 7, 3, 10, 5, 0,
        15, 12, 8, 2, 4, 9, 1, 7, 5, 11, 3, 14, 10, 0, 6, 13,
    ],
    [
        15, 1, 8, 14, 6, 11, 3, 4, 9, 7, 2, 13, 12, 0, 5, 10,
        3, 13, 4, 7, 15, 2, 8, 14, 12, 0, 1, 10, 6, 9, 11, 5,
        0, 14, 7, 11, 10, 4, 13, 1, 5, 8, 12, 6, 9, 3, 2, 15,
        13, 8, 10, 1, 3, 15, 4, 2, 11, 6, 7, 12, 0, 5, 14, 9,
    ],
    [
        10, 0, 9, 14, 6, 3, 15, 5, 1, 13, 12, 7, 11, 4, 2, 8,
        13, 7, 0, 9, 3, 4, 6, 10, 2, 8, 5, 14, 12, 11, 15, 1,
        13, 6, 4, 9, 8, 15, 3, 0, 11, 1, 2, 12, 5, 10, 14, 7,
        1, 10, 13, 0, 6, 9, 8, 7, 4, 15, 14, 3, 11, 5, 2, 12,
    ],
    [
        7, 13, 14, 3, 0, 6, 9, 10, 1, 2, 8, 5, 11, 12, 4, 15,
        13, 8, 11, 5, 6, 15, 0, 3, 4, 7, 2, 12, 1, 10, 14, 9,
        10, 6, 9, 0, 12, 11, 7, 13, 15, 1, 3, 14, 5, 2, 8, 4,
        3, 15, 0, 6, 10, 1, 13, 8, 9, 4, 5, 11, 12, 7, 2, 14,
    ],
    [
        2, 12, 4, 1, 7, 10, 11, 6, 8, 5, 3, 15, 13, 0, 14, 9,
        14, 11, 2, 12, 4, 7, 13, 1, 5, 0, 15, 10, 3, 9, 8, 6,
        4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14,
        11, 8, 12, 7, 1, 14, 2, 13, 6, 15, 0, 9, 10, 4, 5, 3,
    ],
    [
        12, 1, 10, 15, 9, 2, 6, 8, 0, 13, 3, 4, 14, 7, 5, 11,
        10, 15, 4, 2, 7, 12, 9, 5, 6, 1, 13, 14, 0, 11, 3, 8,
        9, 14, 15, 5, 2, 8, 12, 3, 7, 0, 4, 10, 1, 13, 11, 6,
        4, 3, 2, 12, 9, 5, 15, 10, 11, 14, 1, 7, 6, 0, 8, 13,
    ],
    [
        4, 11, 2, 14, 15, 0, 8, 13, 3, 12, 9, 7, 5, 10, 6, 1,
        13, 0, 11, 7, 4, 9, 1, 10, 14, 3, 5, 12, 2, 15, 8, 6,
        1, 4, 11, 13, 12, 3, 7, 14, 10, 15, 6, 8, 0, 5, 9, 2,
        6, 11, 13, 8, 1, 4, 10, 7, 9, 5, 0, 15, 14, 2, 3, 12,
    ],
    [
        13, 2, 8, 4, 6, 15, 11, 1, 10, 9, 3, 14, 5, 0, 12, 7,
        1, 15, 13, 8, 10, 3, 7, 4, 12, 5, 6, 11, 0, 14, 9, 2,
        7, 11, 4, 1, 9, 12, 14, 2, 0, 6, 10, 13, 15, 3, 5, 8,
        2, 1, 14, 7, 4, 10, 8, 13, 15, 12, 9, 0, 3, 5, 6, 11,
    ],
]


def _inverse(table: list) -> list:
    """「输入第 j 位该摆到输出的第几位」。

    `_permute(_, table)`（以及 `_perm_int`）的语义是「输出第 k 位 = 输入第 table[k] 位」，
    所以要反着查：`inv[table[k]] = k`。
    """
    inv = [0] * (len(table) + 1)
    for k, src in enumerate(table, 1):
        inv[src] = k
    return inv


def _build_sp() -> list:
    """把「S 盒 + P 置换」预合成 8 张 64 项表：一轮 Feistel 就是 8 次查表。"""
    inv_p = _inverse(_P)
    tables = []
    for i in range(8):
        box = _SBOX[i]
        table = [0] * 64
        for g in range(64):
            row = ((g >> 5) << 1) | (g & 1)     # 第 1、6 位
            col = (g >> 1) & 0xF                # 中间 4 位
            value = box[row * 16 + col]
            out = 0
            for j in range(4):
                # 这个 S 盒的 4 bit 在 P 的输入里排第 i*4+1 .. i*4+4 位（1 起）
                if (value >> (3 - j)) & 1:
                    out |= 1 << (32 - inv_p[i * 4 + j + 1])
            table[g] = out
        tables.append(table)
    return tables


_SP = _build_sp()
# E 表每项是「取右半 32 bit 的第几位（1 起，MSB 在前）」→ 取那一位要右移多少
_E_SHIFT = [32 - p for p in _E]


def _perm_int(value: int, table: list, src_bits: int) -> int:
    """按 `table`（1 起的位号，源数据 `src_bits` 位）对整数做位置换。"""
    out = 0
    for pos in table:
        out = (out << 1) | ((value >> (src_bits - pos)) & 1)
    return out


def _subkeys_ints(key: bytes) -> list:
    """16 个 48 bit 子密钥（整数）。"""
    key_bits = _perm_int(int.from_bytes(key, "big"), _PC1, 64)
    left = (key_bits >> 28) & 0xFFFFFFF
    right = key_bits & 0xFFFFFFF
    keys = []
    for shift in _SHIFTS:
        left = ((left << shift) | (left >> (28 - shift))) & 0xFFFFFFF
        right = ((right << shift) | (right >> (28 - shift))) & 0xFFFFFFF
        keys.append(_perm_int((left << 28) | right, _PC2, 56))
    return keys


def _f(right: int, subkey: int) -> int:
    """一轮 Feistel：E 扩展 → 异或子密钥 → 8 个 S 盒 → P 置换（后两步并进了 `_SP`）。

    ⚠️ 不能写成 `right ^ subkey`：right 是 32 位、subkey 是 48 位，
    必须先用 E 表取出 6 bit 分组，再和子密钥里对应的那 6 bit 异或。
    """
    out = 0
    shifts = _E_SHIFT
    for i in range(8):
        base = i * 6
        g = ((((right >> shifts[base]) & 1) << 5) |
             (((right >> shifts[base + 1]) & 1) << 4) |
             (((right >> shifts[base + 2]) & 1) << 3) |
             (((right >> shifts[base + 3]) & 1) << 2) |
             (((right >> shifts[base + 4]) & 1) << 1) |
             ((right >> shifts[base + 5]) & 1))
        g ^= (subkey >> (42 - base)) & 0x3F
        out |= _SP[i][g]
    return out


def _crypt_block_int(block: int, subkeys: list, decrypt: bool) -> int:
    value = _perm_int(block, _IP, 64)
    left = (value >> 32) & 0xFFFFFFFF
    right = value & 0xFFFFFFFF
    order = reversed(subkeys) if decrypt else subkeys
    for subkey in order:
        left, right = right, left ^ _f(right, subkey)
    return _perm_int((right << 32) | left, _FP, 64)


class DES:
    """纯 Python 实现（参考实现 / 兜底）。"""

    def __init__(self, key: bytes):
        if len(key) != 8:
            raise ValueError("DES key must be 8 bytes")
        self.key = key
        self._enc = _subkeys_ints(key)

    def encrypt_block(self, block: bytes) -> bytes:
        return _crypt_block_int(int.from_bytes(block, "big"), self._enc, False).to_bytes(8, "big")

    def decrypt_block(self, block: bytes) -> bytes:
        return _crypt_block_int(int.from_bytes(block, "big"), self._enc, True).to_bytes(8, "big")

    def encrypt(self, data: bytes) -> bytes:
        enc = self._enc
        frm = int.from_bytes
        out = bytearray()
        for i in range(0, len(data) - 7, 8):
            out += _crypt_block_int(frm(data[i:i + 8], "big"), enc, False).to_bytes(8, "big")
        return bytes(out)

    def decrypt(self, data: bytes) -> bytes:
        enc = self._enc
        frm = int.from_bytes
        out = bytearray()
        for i in range(0, len(data) - 7, 8):
            out += _crypt_block_int(frm(data[i:i + 8], "big"), enc, True).to_bytes(8, "big")
        return bytes(out)


# ---------------------------------------------------------------------------
# 可选加速：libcrypto（OpenSSL）
# ---------------------------------------------------------------------------

# 公开的已知答案向量（教科书那条：key 133457799BBCDFF1 / pt 0123456789ABCDEF）
_KAT_KEY = bytes.fromhex("133457799bbcdff1")
_KAT_PT = bytes.fromhex("0123456789abcdef")
_KAT_CT = bytes.fromhex("85e813540f0ab405")

# DES_key_schedule 在 OpenSSL 里是 32 个 DES_LONG（128 字节），给 256 字节留余量
_KS_SIZE = 256

_LIB_NAMES = (
    "libcrypto.so.3", "libcrypto.so.1.1", "libcrypto.so",     # Linux
    "libcrypto-3-x64.dll", "libcrypto-3.dll",                 # Windows（CPython 自带）
    "libcrypto-1_1-x64.dll", "libcrypto-1_1.dll",
    "libcrypto.dylib",                                        # macOS
)
_LIB_GLOBS = ("DLLs/libcrypto*.dll", "bin/libcrypto*",
              "lib/libcrypto.so*", "lib64/libcrypto.so*")

_c_state = {"ready": False, "lib": None, "reason": "", "kat": False}
_force = None  # None=自动 / "python" / "libcrypto"（自检用）


def _candidates():
    """先找「和当前解释器一起装的那份」（Windows 上就是 DLLs\\libcrypto-3.dll），
    再退回让动态加载器按名字找（Linux 发行版装的 OpenSSL）。"""
    seen = set()
    for base in dict.fromkeys((sys.base_prefix, sys.prefix)):
        for pat in _LIB_GLOBS:
            for path in sorted(glob.glob(os.path.join(base, pat))):
                if os.path.isfile(path) and path not in seen:
                    seen.add(path)
                    yield path
    for name in _LIB_NAMES:
        if name not in seen:
            seen.add(name)
            yield name


def _c_kat_ok(lib) -> bool:
    """用已知答案向量验一遍这个 libcrypto 到底能不能按标准 DES 干活。

    不验的话，万一某个构建把 DES 换成了别的（或者 ABI 对不上导致读到垃圾），
    就会**静默地发出客户端解不开的包** —— 那是整个协议崩掉级别的故障。
    """
    ks = ctypes.create_string_buffer(_KS_SIZE)
    lib.DES_set_key_unchecked(_KAT_KEY, ks)
    buf = ctypes.create_string_buffer(8)
    lib.DES_ecb_encrypt(_KAT_PT, buf, ks, 1)
    if buf.raw != _KAT_CT:
        return False
    lib.DES_ecb_encrypt(_KAT_CT, buf, ks, 0)
    return buf.raw == _KAT_PT


def _load_c_des():
    """惰性加载 libcrypto。返回 (lib, 失败原因)；成功时原因为空串。"""
    if os.environ.get("GS_DES_PURE"):
        return None, "GS_DES_PURE=1 要求走纯 Python"
    bad = []
    for cand in _candidates():
        try:
            lib = ctypes.CDLL(cand)
        except OSError:
            continue
        if not (hasattr(lib, "DES_ecb_encrypt") and hasattr(lib, "DES_set_key_unchecked")):
            bad.append(f"{cand} 没有 DES_ecb_encrypt")
            continue
        lib.DES_set_key_unchecked.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
        lib.DES_set_key_unchecked.restype = None
        lib.DES_ecb_encrypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                                        ctypes.c_void_p, ctypes.c_int]
        lib.DES_ecb_encrypt.restype = None
        if not _c_kat_ok(lib):
            bad.append(f"{cand} 加解密结果不对（和标准 DES 不一致）")
            continue
        if DES(_KAT_KEY).encrypt_block(_KAT_PT) != _KAT_CT:  # 纯 Python 自检
            bad.append("纯 Python 实现和已知答案向量不一致")
            continue
        return lib, ""
    return None, "；".join(bad) if bad else "没找到 libcrypto"


def _log(msg: str) -> None:
    try:
        from .. import logx
        logx.get("des").info("%s", msg)
    except Exception:  # 日志失败绝不影响加解密
        pass


def _c_lib():
    if not _c_state["ready"]:
        _c_state["ready"] = True
        lib, reason = _load_c_des()
        _c_state["lib"] = lib
        _c_state["reason"] = reason
        if lib is None:
            _log(f"DES 走纯 Python（~157 KB/s）：{reason}")
        else:
            _log("DES 走 libcrypto（~17 MB/s）")
    return _c_state["lib"]


class _CDES:
    """libcrypto 的 `DES_ecb_encrypt` 包装（一次一个 8 字节块，ECB 用不着串行状态）。"""

    __slots__ = ("_lib", "_ks")

    def __init__(self, lib, key: bytes):
        self._lib = lib
        self._ks = ctypes.create_string_buffer(_KS_SIZE)
        lib.DES_set_key_unchecked(key, self._ks)

    def _run(self, data: bytes, enc: int) -> bytes:
        fn = self._lib.DES_ecb_encrypt
        ks = self._ks
        buf = ctypes.create_string_buffer(8)
        out = bytearray()
        for i in range(0, len(data) - 7, 8):
            fn(data[i:i + 8], buf, ks, enc)
            out += buf.raw
        return bytes(out)

    def encrypt(self, data: bytes) -> bytes:
        return self._run(data, 1)

    def decrypt(self, data: bytes) -> bytes:
        return self._run(data, 0)


def backend() -> str:
    """当前实际用哪条路线（"libcrypto" / "python"）—— 自检和文档用。"""
    if _force == "python":
        return "python"
    if _force == "libcrypto" and _c_lib() is None:
        return "python"
    return "python" if _c_lib() is None else "libcrypto"


def force_backend(name: str | None) -> None:
    """强制某个实现（None = 恢复自动）。只给自检用。"""
    global _force
    if name not in (None, "python", "libcrypto"):
        raise ValueError("backend 只能是 None / 'python' / 'libcrypto'")
    _force = name


def _impl(key: bytes):
    if _force != "python":
        lib = _c_lib()
        if lib is not None:
            return _CDES(lib, key)
        if _force == "libcrypto":
            raise RuntimeError(f"要求 libcrypto 但不可用：{_c_state['reason']}")
    return DES(key)


def bit_pad(data: bytes, block: int = 8) -> bytes:
    """客户端用的补位方式：先补一个 0x80，再补 0x00 到块边界（ISO/IEC 9797-1 method 2）。

    实测：319 字节的登录 JSON 加密后是 320 字节，末尾补位正好是 0x80。
    """
    data = data + b"\x80"
    if len(data) % block:
        data += b"\x00" * (block - len(data) % block)
    return data


def bit_unpad(data: bytes) -> bytes:
    # 去掉末尾的 0x00，再去掉一个 0x80
    end = len(data)
    while end > 0 and data[end - 1] == 0:
        end -= 1
    if end > 0 and data[end - 1] == 0x80:
        end -= 1
    return data[:end]


def des_encode(key: bytes, msg: bytes) -> bytes:
    """对应客户端的 crypt.desEncode(key, msg)。"""
    return _impl(key).encrypt(bit_pad(msg))


def des_decode(key: bytes, msg: bytes) -> bytes:
    """对应客户端的 crypt.desDecode(key, msg)。"""
    if len(msg) % 8:
        return b""
    return bit_unpad(_impl(key).decrypt(msg))
