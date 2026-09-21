"""私密剧情（`diary.*`）—— 剧情回看 / 花金条解锁。

客户端 `assets/src/data/diary.jsc`（反汇编）：

    Diary.ctor(data)          // 登录块 `data.diary`
        this._storyDiarys   = data.storyDiarys      // {chapterId: 行}
        this._diarysBuyInfo = data.diarysBuyInfo    // {chapterId: 1}

行（`storyDiarys[chapterId]`）只有两个字段被读：

    isUnlock(cid, lid)       -> !!_storyDiarys[cid].unlockLevels[lid]
    isStoryCanShow(cid, lid) -> isUnlock(cid,lid)
                                || (!!_storyDiarys[cid].lockLevels[lid]
                                    && _diarysBuyInfo[cid] == 1)     // ← 松散相等！必须是 1

也就是说：**已解锁**的放 `unlockLevels`，**可以买**的放 `lockLevels`，
章节能不能买由 `_diarysBuyInfo[cid] === 1` 决定。

购买链路（`ui/illustrated/diarylevelitem.jsc`）：

    _clickLayer()  未解锁时 → BuyPopBox.pop(dictionary[3401].replace("%d",
                    table_story_review[chapterId].price), cb, ITEM_KEY.GEM)
    cb()           → dataManager.diary.unlockStory({chapterId, levelId}, cb)
    Diary.unlockStory/<  成功 → _storyDiarys[data.chapterId] = data
                         失败 code 201..204 → toast(table_dictionary[<那条>])

⚠️ **`price` 客户端表里没有**：`table_story_review`（38 行，本服已抽）每行只有
`image`（立绘文件名），`price` 是原版**服务端**塞进去的 —— 所以：
  * 服务端这边的价格是私服自己定的（`STORY_PRICE`，见 differences §D）；
  * 客户端那个确认弹窗的文案会是「是否花费undefined金条解锁该剧情？」，
    要修就得在 `patch.js` 里把价格填回 `table_story_review[*].price`
    （登录块里我们顺带发了 `prices`，供那个补丁读）。

解锁状态怎么算：**已经通关过的关卡算已解锁**（那是玩家自己看过的剧情），
没通关的放进 `lockLevels` 当"可购买"。通关记录来自 `gamesrv/instance.py`。
"""

from __future__ import annotations

import os

from . import items, logx, store

log = logx.get("diary")

# 购买失败码（客户端 `tableswitch low=201 high=204` → `table_dictionary[<id>]`）。
# ⚠️ 码→文案的对应是**按字典顺序推的**（3401 是购买提示，紧接着 3402..3405 正好四条：
# 物品不足 / 该章节已解锁 / 标签错误 / 章节错误），没有逐条反汇编跳转表核实过。
CODE_NO_ITEM = 201       # table_dictionary[3402] 物品不足
CODE_ALREADY = 202       # table_dictionary[3403] 该章节已解锁
CODE_BAD_TAB = 203       # table_dictionary[3404] 标签错误
CODE_BAD_CHAPTER = 204   # table_dictionary[3405] 章节错误

GEM = "100001"           # ITEM_KEY.GEM（解锁消耗金条）

# 每章解锁一个剧情的价格（金条）。⚠️ **私服自己定的**（见模块注释：原版在服务端）。
STORY_PRICE_DEFAULT = 30
STORY_PRICE = {
    # 章节越靠后越贵（按 table_story_review 的顺序）
}


def story_review() -> dict:
    """`table_story_review`（38 行：章节 -> 立绘）。键就是**章节 id**。"""
    return items.table("table_story_review")


def chapter_table() -> dict:
    return items.table("table_chapter")


def price_of(chapter_id) -> int:
    """`table_story_review[cid].price` 的替代：私服价格表。"""
    key = str(chapter_id)
    if key in STORY_PRICE:
        return int(STORY_PRICE[key])
    keys = sorted(story_review())
    try:
        idx = keys.index(key)
    except ValueError:
        return STORY_PRICE_DEFAULT
    # 每往后一章 +5 金条，30 起
    return STORY_PRICE_DEFAULT + idx * 5


def _levels_of(chapter_id) -> list:
    row = chapter_table().get(str(chapter_id)) or {}
    levels = row.get("lv")
    return [str(x) for x in levels] if isinstance(levels, list) else []


def _cleared_levels(player: dict) -> set:
    """玩家通关过的关卡 id 集合（`instance` 的存档：`levels[levelKey].starMark` 之类）。"""
    out = set()
    try:
        from . import instance

        levels = (instance._record(player) or {}).get("levels") or {}
    except Exception:                                   # noqa: BLE001
        return out
    for key, row in levels.items():
        if not isinstance(row, dict):
            continue
        try:
            if int(row.get("starMark") or 0) > 0 or int(row.get("playCount") or 0) > 0:
                out.add(str(key))
        except (TypeError, ValueError):
            continue
    return out


def state(player: dict) -> dict:
    """`player["diary"] = {"bought": {chapterId: [levelId, ...]}}`（只存买过的）。"""
    st = player.get("diary")
    if not isinstance(st, dict):
        st = {}
        player["diary"] = st
    if not isinstance(st.get("bought"), dict):
        st["bought"] = {}
    return st


def bought_of(player: dict, chapter_id) -> list:
    got = state(player)["bought"].get(str(chapter_id))
    return [str(x) for x in got] if isinstance(got, list) else []


def story_row(player: dict, chapter_id) -> dict:
    """`storyDiarys[chapterId]`：已通关的 + 买过的 = 已解锁，其余进 lockLevels。"""
    cleared = _cleared_levels(player)
    bought = set(bought_of(player, chapter_id))
    unlock, lock = {}, {}
    for lid in _levels_of(chapter_id):
        if lid in cleared or lid in bought:
            unlock[lid] = 1
        else:
            lock[lid] = 1
    return {"chapterId": str(chapter_id), "unlockLevels": unlock, "lockLevels": lock}


def buy_info(player: dict) -> dict:
    """`diarysBuyInfo`：哪些章节可购买（客户端判 `== 1`）。

    私服：`table_story_review` 里的 38 章**全部可买**（原版是按活动时间逐步开的，
    客户端那个 `activity_chapter_unlock_diary_days = 7` 就是干这个的）。
    """
    return {str(cid): 1 for cid in story_review()}


def block(player: dict, now=None) -> dict:
    """登录块 `data.diary`（+ 一份 `prices` 给 patch.js 填客户端表用）。"""
    story = {str(cid): story_row(player, cid) for cid in story_review()}
    return {
        "storyDiarys": story,
        "diarysBuyInfo": buy_info(player),
        # ⚠️ 客户端不读这个键（`Diary._initData` 只认上面两个）；给 patch.js 用：
        # 它可以把价格填回 `table_story_review[*].price`，修掉确认弹窗里的 "undefined"。
        "prices": {str(cid): price_of(cid) for cid in story_review()},
    }


def buy_info_route(player: dict) -> dict:
    """`diary.getdiarybuyinfo` -> `{diarysBuyInfo, updateDiarys}`。"""
    from .gameproto import CODE_OK

    update = {str(cid): story_row(player, cid) for cid in story_review()}
    return {"code": CODE_OK, "msg": "", "data": {
        "diarysBuyInfo": buy_info(player),
        "updateDiarys": update,
    }}


def buy_unlock(player: dict, msg: dict) -> dict:
    """`diary.buyunlockstory {chapterId, levelId}` -> `data` = 该章的新行。"""
    from .gameproto import CODE_OK

    chapter_id = str((msg or {}).get("chapterId") or "")
    level_id = str((msg or {}).get("levelId") or "")
    if not chapter_id or chapter_id not in story_review():
        return {"code": CODE_BAD_CHAPTER, "msg": "bad chapter", "data": {}}
    row = story_row(player, chapter_id)
    levels = _levels_of(chapter_id)
    if level_id not in levels:
        return {"code": CODE_BAD_TAB, "msg": "bad level", "data": {}}
    if row["unlockLevels"].get(level_id):
        return {"code": CODE_ALREADY, "msg": "already unlocked", "data": {}}
    price = price_of(chapter_id)
    if items.count_of(player, GEM) < price:
        log.info("玩家 %s 解锁剧情金条不够：要 %s 有 %s",
                 player.get("account"), price, items.count_of(player, GEM))
        return {"code": CODE_NO_ITEM, "msg": "not enough gem", "data": {}}
    items.sub_item(player, GEM, price)
    st = state(player)
    got = st["bought"].setdefault(chapter_id, [])
    if level_id not in got:
        got.append(level_id)
    row = story_row(player, chapter_id)
    log.info("玩家 %s 花 %s 金条解锁剧情 %s/%s（该章已解锁 %d 个）",
             player.get("account"), price, chapter_id, level_id, len(row["unlockLevels"]))
    return {"code": CODE_OK, "msg": "", "data": row}
