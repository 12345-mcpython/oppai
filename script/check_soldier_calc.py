"""交叉验证服务端的军士升级计算和客户端 `calcSoldierUpgrade` 是否一致。

为什么要这个：
    「培养」面板是**客户端先本地算**出目标等级显示出来，点确认才把材料发给
    服务端，服务端回什么等级就显示什么等级。两边算得不一样的表现就是
    「预览 +3 级，点完跳 +8 级」。所以 gamesrv/soldier.py 必须和
    `src/data/charcenter.jsc` 的 calcSoldierUpgrade 逐位对齐。

用法（游戏要在跑，探针已加载）：
    python tools/check_soldier_calc.py
"""

from __future__ import annotations

import json
import os
import random
import sys

# --- 路径自举（项目已重排：脚本在 script/、服务端在 server/）---
# 从自己往上找带 _paths.py 的那一层，把它和 server/ 都塞进 sys.path。
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gamesrv import soldier  # noqa: E402
from repl import eval_remote  # noqa: E402



# 关掉名字/等级之外的一切随机性：给一组覆盖边界的情况。
#   - lv 刚好升一级 / 刚好卡在临界
#   - 材料星级 1..5（table_soldier_to_exp 只用 star 选列）
#   - 目标接近等级上限
KEYS = ["sasm010104", "sbd010104", "saf010104", "sglrs010104", "shs010104"]

JS = r"""
(function () {
    var cases = %s;
    var out = [];
    for (var i = 0; i < cases.length; i++) {
        var c = cases[i];
        var target = {
            lv: c.tlv, curExp: c.texp, quality: c.tq, star: c.ts,
            maxLv: c.tmax, mainSkill: {lv: 1}
        };
        var mats = [];
        for (var j = 0; j < c.mats.length; j++) {
            mats.push({
                lv: c.mats[j][0], quality: c.mats[j][1],
                star: c.mats[j][2], key: c.mats[j][3]
            });
        }
        var r = dataManager.character.calcSoldierUpgrade(mats, target);
        out.push({
            i: i, tarLv: r.tarLv, tarExp: r.tarExp, gainExp: r.gainExp,
            needMoney: r.needMoney, tarMaxExp: r.tarMaxExp
        });
    }
    return JSON.stringify(out);
})()
"""


def build_cases(seed: int = 20260918) -> list[dict]:
    rnd = random.Random(seed)
    cases: list[dict] = []
    # 先手工放几个固定用例（尤其是「材料喂满到等级上限」）
    cases.append({"tlv": 1, "texp": 0, "tq": 4, "ts": 1, "tmax": 30,
                  "mats": [[1, 4, 1, "sasm010104"]]})
    cases.append({"tlv": 1, "texp": 0, "tq": 4, "ts": 1, "tmax": 30,
                  "mats": [[1, 4, 1, "sasm010104"], [1, 4, 1, "sbd010104"]]})
    cases.append({"tlv": 29, "texp": 0, "tq": 4, "ts": 1, "tmax": 30,
                  "mats": [[1, 4, 1, "sasm010104"]]})
    cases.append({"tlv": 28, "texp": 100, "tq": 4, "ts": 1, "tmax": 30,
                  "mats": [[5, 4, 3, "saf010104"], [2, 3, 2, "sglrs010104"]]})
    for _ in range(40):
        cases.append({
            "tlv": rnd.randint(1, 29),
            "texp": rnd.choice([0, 1, 100, 1300, 1359]),
            "tq": rnd.choice([1, 2, 3, 4]),
            "ts": rnd.choice([1, 2, 3, 4, 5]),
            "tmax": rnd.choice([30, 40, 50, 60, 70]),
            "mats": [[rnd.randint(1, 20), rnd.choice([1, 2, 3, 4]),
                      rnd.choice([1, 2, 3, 4, 5]), rnd.choice(KEYS)]
                     for _ in range(rnd.randint(1, 4))],
        })
    return cases


def main() -> int:
    from gamesrv import config

    cases = build_cases()
    base = f"http://127.0.0.1:{config.CDN_PORT}"
    got = eval_remote(base, JS % json.dumps(cases), timeout=30.0)
    if not got.get("ok"):
        print("!! 取客户端结果失败:", got, file=sys.stderr)
        return 1
    value = got["value"]
    while isinstance(value, str):
        value = json.loads(value)

    bad = 0
    for case, want in zip(cases, value):
        target = {
            "lv": case["tlv"], "curExp": case["texp"], "quality": case["tq"],
            "star": case["ts"], "maxLv": case["tmax"], "skillLv": 1,
        }
        # calc_upgrade 要玩家字典去找材料，这里直接绕过：把材料当成 dict 传
        mats = [{"lv": m[0], "quality": m[1], "star": m[2], "key": m[3]}
                for m in case["mats"]]
        mine = soldier.calc_upgrade({}, target, mats)
        # 材料不是玩家名下的会被 _material_of 过滤掉，这里改用纯计算入口
        mine = soldier._calc(target, mats)
        diff = {k: (mine.get(k), want.get(k)) for k in
                ("tarLv", "tarExp", "gainExp", "needMoney", "tarMaxExp")
                if mine.get(k) != want.get(k)}
        if diff:
            bad += 1
            print(f"✗ case#{want['i']} target={target} mats={case['mats']}")
            print(f"    服务端={mine}")
            print(f"    客户端={want}")
            print(f"    不一致={diff}")
    print(f"[check] {len(cases)} 个用例，{bad} 个不一致")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
