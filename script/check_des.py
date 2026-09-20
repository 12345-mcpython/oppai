r"""DES 自检：参考向量逐字节比对 + 往返 + 速率（两条路线都验）。

    python script\check_des.py

`out/des_ref.json` 是**改实现之前**跑出来的参考向量（key/pt/ct/back 全 hex），
所以这个脚本能证明新实现和原来的逐位版密文完全一致（客户端按标准 DES 解，
差一个 bit 就会整包解不出来）。

`des.py` 有两条路线（纯 Python 查表 / libcrypto C 实现），这里**两条都跑**，
另外再用随机 key + 随机明文做交叉比对 —— 只靠固定向量的话，
「某条路线只在别的长度上出错」这种问题会漏掉。
"""
import io
import json
import os
import random
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

SIZE = 275 * 1024          # 登录响应这个量级
CROSS_CASES = 200          # 随机交叉验证组数


def check_backend(name: str, vec: list) -> bool:
    """验一条路线：参考向量 + 大包往返 + 速率。返回是否全部通过。"""
    des.force_backend(name)
    try:
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
        print("参考向量 %d 条，不一致 %d 条" % (len(vec), bad))

        key = b"\x01" + b"\x00" * 7
        payload = b"x" * SIZE
        t0 = time.perf_counter()
        ct = des.des_encode(key, payload)
        dt = time.perf_counter() - t0
        speed = SIZE / 1024.0 / max(dt, 1e-9)
        print("大包往返：%.1f KB 加密用时 %.3f 秒（%.0f KB/s）"
              % (SIZE / 1024.0, dt, speed))
        if des.des_decode(key, ct) != payload:
            print("!! 大包往返失败")
            bad += 1
        return bad == 0
    finally:
        des.force_backend(None)


def cross_check(a: str, b: str) -> bool:
    """两条路线交叉比对：随机 key + 随机长度明文，密文必须一模一样。"""
    rnd = random.Random(20240607)
    bad = 0
    for _ in range(CROSS_CASES):
        key = bytes(rnd.randrange(256) for _ in range(8))
        msg = bytes(rnd.randrange(256) for _ in range(rnd.randrange(0, 300)))
        des.force_backend(a)
        enc_a = des.des_encode(key, msg)
        dec_a = des.des_decode(key, enc_a)
        des.force_backend(b)
        enc_b = des.des_encode(key, msg)
        dec_b = des.des_decode(key, enc_b)
        des.force_backend(None)
        if enc_a != enc_b or dec_a != msg or dec_b != msg:
            bad += 1
            if bad <= 3:
                print("!! 交叉不一致 key=%s msg=%s" % (key.hex(), msg.hex()[:64]))
    print("%s vs %s 随机交叉 %d 组，不一致 %d 组" % (a, b, CROSS_CASES, bad))
    return bad == 0


def main() -> int:
    if not os.path.isfile(REF):
        print("!! 没有参考向量 %s（先在旧实现上生成一次）" % REF)
        return 1
    vec = json.load(io.open(REF, encoding="utf-8"))

    ok = True
    print("== 纯 Python（参考实现 / 兜底）==")
    ok = check_backend("python", vec) and ok

    print("\n== libcrypto（C 实现）==")
    try:
        des.force_backend("libcrypto")
        has_c = des.backend() == "libcrypto"
        des.force_backend(None)
    except RuntimeError as exc:
        has_c = False
        print("不可用（%s）—— 跳过，纯 Python 仍然能跑" % exc)
    if has_c:
        ok = check_backend("libcrypto", vec) and ok
        print()
        ok = cross_check("python", "libcrypto") and ok

    print("\n当前默认后端：%s" % des.backend())
    print("全部通过 ✅" if ok else "有不一致 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
