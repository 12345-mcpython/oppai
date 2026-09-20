"""gacha.* —— 抽卡（见 `gamesrv/gacha.py`：卡池 master 是服务端自己造的）。

三条 route（`src/data/gacha.jsc`）：

    gacha.getgacha         Gacha.updateGachaInfo    刷新卡池信息
    gacha.gacha            Gacha.gacha -> gachaJudge -> getGachaFullInfo   抽
    gacha.getlibraryshow   Gacha.getLibraryShow     看卡池里有哪些卡

形状（反汇编 + 实机核对）：

    gacha.getgacha          回包**扁平**给 `Gacha.update(data)` 读的那三件套：
                            `gachaData` / `gachaInfoList` / `gachaMasterList`（**都是 map**）
                            + `gachaLibCards` / `lastUpdateInfoTime` / `freeGachaTip` /
                            `activityTimes`。⚠️ **不是** `{"gacha": {...}}`（它不走响应派发）。
    gacha.getlibraryshow    回 `data.{cards, upRate, gachaInfo}`
    gacha.gacha             请求 `{key, times}`；回 `data.{gachaData, cards, itemKey,
                            extraReward, gemGachaTimes}`；失败用 `GACHA_ERROR_DICT` 里的码
                            （201 参数 / 202 无此池 / 204 次数用完 / 206 资源不够 …）。
"""

from __future__ import annotations

from .. import config, gacha, logx, store
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.gacha")


def _player(session: dict) -> dict:
    account = (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT
    return store.get_or_create_player(account)


@route("gacha.getgacha")
def get_gacha(session: dict, msg: dict, req_id):
    """刷新卡池信息（回扁平三件套，客户端 `_this.update(data.data)`）。

    顺手带一个顶层 `gacha` 块：主界面「黑市」按钮的红点读的是
    `Gacha.isNeedShowReminded()` = `_freeGachaTip`，而它**只有 RESP-DISPATCH
    的 `updateByServer` 能写**（登录块里发是死键 —— 那时 `dataManager.gacha` 还没建）。
    免费抽卡次数还在就点亮红点（也顺便让玩家找得到入口）。
    """
    player = _player(session)
    block = gacha.login_block(player)
    free_left = False
    for key, conf in gacha.POOLS.items():
        if conf.get("dailyLimit"):
            row = gacha.reset_daily(player, key, gacha.ONE)
            free_left = row["todayTimes"] < int(conf["dailyLimit"])
    block["gacha"] = {"freeGachaTip": bool(free_left)}
    store.save_player(player)
    log.info("gacha.getgacha：%d 个池子 / %d 行消耗 / %d 行次数，免费红点=%s",
             len(block["gachaMasterList"]), len(block["gachaInfoList"]),
             len(block["gachaData"]), free_left)
    return {"code": CODE_OK, "msg": "", "data": block}


@route("gacha.getlibraryshow")
def get_library_show(session: dict, msg: dict, req_id):
    player = _player(session)
    data = gacha.library_show(player, (msg or {}).get("key"))
    log.info("gacha.getlibraryshow key=%s -> %d 张卡",
             (msg or {}).get("key"), len(data.get("cards") or []))
    return {"code": CODE_OK, "msg": "", "data": data}


@route("gacha.gacha")
def do_gacha(session: dict, msg: dict, req_id):
    player = _player(session)
    result = gacha.draw(player, (msg or {}).get("key"), (msg or {}).get("times"))
    if result.get("code") == CODE_OK:
        store.save_player(player)
    return result
