"""gacha.* —— 抽卡。

客户端调用点：`src/data/gacha.jsc`，三条 route：

    gacha.getgacha         Gacha.updateGachaInfo    刷新卡池信息
    gacha.gacha            Gacha.gacha -> gachaJudge -> getGachaFullInfo   抽
    gacha.getlibraryshow   Gacha.getLibraryShow     看卡池里有哪些卡

形状（`script/jsc_funcs.py gacha.jsc updateGachaInfo`）：

    Gacha<.updateGachaInfo      cb now _this util time _lastUpdateInfoTime
                                server request gacha.getgacha
    Gacha<.updateGachaInfo/<    err data T code update data
        -> `if (data.code == 200) _this.update(data.data)`
        -> 所以响应 `data` 要**扁平**给这五个字段（**不要**再套一层 `gacha`），
           就是登录块里 `gacha` 那一份：gachaData / gachaLibCards /
           lastUpdateInfoTime / freeGachaTip / activityTimes

    Gacha<.getLibraryShow/<     err data cards upRate gachaInfo T code
                                updateLibCards data _gachaLibCardsObj cards upRate
        -> `data.data` 里要有 `cards` / `upRate` / `gachaInfo`

    Gacha<.gacha/<              err data result res itemKey T code data
                                updateGachaData gachaData _getRandomCard cards
                                extraReward gemGachaTimes player analyticsGacha
                                itemKey ITEM_KEY GEM ccuiManager goldTip 808
                                GACHA_ERROR_DICT
        -> 抽卡结果读 `data.gachaData`（新的卡池状态）和 `cards`

## ⚠️ 现状：抽卡是**内容缺口**，不是接线缺口

`assets/src/table/` 里 **176 张表没有一张是 gacha 的** —— 卡池配置
（有哪些池子、消耗什么、概率、能出哪些卡）不在客户端表里，而是**服务端下发**的，
就是登录块里那个 `"gachaData": {}`。

所以：

* `gacha.getgacha` / `gacha.getlibraryshow` 现在回**空但形状正确**的包 ——
  客户端的卡池界面会显示「没有卡池」，而不是像以前那样拿 `{}` 去
  `update(undefined)` 然后抛异常。
* `gacha.gacha` 同理，回空结果。**这里没有假装抽到东西** ——
  真抽卡要先把 `gachaData` 的 master 数据做出来（内容制作：
  池子 id / 消耗 / 概率 / 卡池列表），那是独立任务。

另外 `Gacha.gacha` 前面还有一道客户端自己的 `gachaJudge()` 校验，
没有 master 数据时它根本不会发出请求 —— 实测日志里从来没出现过
`gacha.gacha`，只出现过下面两条。
"""

from __future__ import annotations

from .. import logx, store
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.gacha")


def _gacha_block() -> dict:
    """和登录包里 `gacha` 块完全同形状（客户端 `Gacha.update(data)` 直接吃）。"""
    return {
        "gachaData": {},
        "gachaLibCards": {},
        "lastUpdateInfoTime": store.now_ms(),
        "freeGachaTip": {},
        "activityTimes": {},
    }


@route("gacha.getgacha")
def get_gacha(session: dict, msg: dict, req_id):
    """刷新卡池信息。注意回的是**扁平**的五件套，不是 `{"gacha": {...}}`
    —— 客户端是 `_this.update(data.data)`，不是走 responseConfig 派发。"""
    log.info("gacha.getgacha（尚无卡池 master 数据，回空）")
    return {"code": CODE_OK, "msg": "", "data": _gacha_block()}


@route("gacha.getlibraryshow")
def get_library_show(session: dict, msg: dict, req_id):
    """卡池里有哪些卡。

    客户端的读法：`var d = data.data; if (data.code == 200) updateLibCards(d.cards, d.upRate)`。
    没有 master 数据，所以 cards/upRate 都是空的。
    """
    log.info("gacha.getlibraryshow key=%s（回空卡池）", (msg or {}).get("key"))
    return {"code": CODE_OK, "msg": "", "data": {"cards": [], "upRate": {}, "gachaInfo": {}}}


@route("gacha.gacha")
def do_gacha(session: dict, msg: dict, req_id):
    """抽卡。

    **不假装发出东西**：没有卡池 master 就没法决定出什么卡，所以回空结果 +
    保持卡池状态不变。客户端会走到 `updateGachaData({})`，什么都不会变。

    真要实现，得先有 `gachaData` 的 master（池子 id / 消耗道具 / 概率 / 卡池），
    再按概率抽、把卡发进玩家存档、然后把结果塞进 `cards`。
    """
    key = (msg or {}).get("key")
    times = (msg or {}).get("times")
    log.warning("gacha.gacha key=%s times=%s —— 卡池 master 数据还没做，回空结果",
                key, times)
    return {
        "code": CODE_OK,
        "msg": "",
        "data": {
            "gachaData": {},
            "cards": [],
            "itemKey": "",
            "extraReward": [],
            "gemGachaTimes": 0,
        },
    }
