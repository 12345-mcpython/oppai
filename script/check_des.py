r"""DES 自检：参考向量逐字节比对 + 往返 + 速率。

    python script\check_des.py

`out/des_ref.json` 是**改实现之前**跑出来的参考向量（key/pt/ct/back 全 hex），
所以这个脚本能证明「查表版」和「逐位版」密文完全一致（客户端按标准 DES 解，
差一个 bit 就会整包解不出来）。
"""
import io
import json
import os
import sys
import time

_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402

from gamesrv.crypto import des  # noqa: E402

ROOT = os.path.dirname(_d)
REF = os.path.join(ROOT, "out", "des_ref.json")


def main() -> int:
    ok = True
    if not os.path.isfile(REF):
        print("!! 没有参考向量 %s（先在旧实现上生成一次）" % REF)
        return 1
    vec = json.load(io.open(REF, encoding="utf-8"))
    bad = 0
    for one in vec:
        key = bytes.fromhex(one["key"])
        pt = bytes.fromhex(one["pt"])
        ct = des.des_encode(key, pt)
        if ct.hex() != one["ct"]:
            print("!! 密文不一致 key=%s pt=%s\n   期望 %s\n   实得 %s"
                  % (one["key"], one["pt"][:32], one["ct"][:48], ct.hex()[:48]))
            bad += 1
        back = des.des_decode(key, ct)
        if back.hex() != pt.hex():
            print("!! 往返失败 key=%s pt=%s" % (one["key"], one["pt"][:32]))
            bad += 1
    ok = ok and bad == 0
    print("参考向量 %d 条，不一致 %d 条" % (len(vec), bad))

    # 速率（登录响应 ~275 KB 这种量级）
    key = b"\x01" + b"\x00" * 7
    payload = b"x" * (275 * 1024)
    t0 = time.time()
    ct = des.des_encode(key, payload)
    dt = time.time() - t0
    print("加解密速率：%.1f KB 用时 %.3f 秒（%.0f KB/s）"
          % (len(payload) / 1024.0, dt, len(payload) / 1024.0 / max(dt, 1e-9)))
    assert des.des_decode(key, ct) == payload, "大包往返失败"
    print("全部通过 ✅" if ok else "有参考向量不一致 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
