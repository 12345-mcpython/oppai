"""diary.* —— 私密剧情（回看 / 花金条解锁）。

两条 route（`assets/src/data/diary.jsc`）：

    diary.getdiarybuyinfo    Diary.updateDiaryBuyInfo  刷新可买信息
    diary.buyunlockstory     Diary.unlockStory         {chapterId, levelId} 解锁一条

业务逻辑在 `gamesrv/diary.py`（登录块 `data.diary` 的形状也写在那儿）。
"""

from __future__ import annotations

from .. import config, diary, items, logx, store
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.diary")


def _player(session: dict) -> dict:
    account = (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT
    return store.get_or_create_player(account)


def _reply(player: dict, result: dict) -> dict:
    data = dict(result.get("data") or {})
    if int(result.get("code") or 0) == CODE_OK:
        store.save_player(player)
    return {"code": result.get("code"), "msg": result.get("msg", ""), "data": data}


@route("diary.getdiarybuyinfo")
def get_diary_buy_info(session: dict, msg: dict, req_id):
    """回 `{diarysBuyInfo, updateDiarys}`（客户端按 key 合并进 `_storyDiarys`）。"""
    player = _player(session)
    result = diary.buy_info_route(player)
    log.info("diary.getdiarybuyinfo 可买章节 %d 个",
             len(result["data"].get("diarysBuyInfo") or {}))
    return _reply(player, result)


@route("diary.buyunlockstory")
def buy_unlock_story(session: dict, msg: dict, req_id):
    """花金条解锁一条剧情；失败用 201..204（客户端按码 toast 文案）。"""
    player = _player(session)
    known = {str(k) for k in items.items_of(player)}
    result = diary.buy_unlock(player, msg or {})
    out = _reply(player, result)
    if int(result.get("code") or 0) == CODE_OK:
        out["data"]["items"] = items.changed_block(player, known)
    return out
