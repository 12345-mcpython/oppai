"""黑市交易所 / 充值页（`exchange.*`）。

这个模块在客户端里叫 `ExchangeCenter`，实际是**一整个商店中枢**：
月卡、充值礼包（IAP）、金条↔萌钞、行动力/BP 兑换，全走它。

数据模型（反汇编 `src/data/exchangecenter.jsc`）
================================================

* 玩家行：`_exchangeData[<key>]`，登录块 `data.exchange` 就是这一整张 map。
  字段：`{exchangeKey, todayExchangeTimes, totalExchangeTimes, lastExchangeTimeSec}`
  —— `todayExchangeTimes` 的每日清零是**客户端自己**按 `table_constant.common_reset_time`
  和 `lastExchangeTimeSec` 算的（`_resetData`），服务端也要按同一天界清零（不然隔天
  兑换档位会停在高档）。

* `table_exchange_item[<key>]` 是**展示行 + 价格阶梯**：

      exchange_key_1 / exchange_key_2 / … / exchange_key_12   第 N 次兑换用哪一档
      exchange_key_default                                    超过阶梯后一直用这档
      first_exchange_percentage                               首次兑换的百分比（折扣/加成）
      type / name / desc / icon / open_lv / show_status / begin_time / ended_time …

  `ExchangeCenter.getExchangeInfoKey()` 就是
  `times = 行 ? 行.todayExchangeTimes + 1 : 1` → 取 `exchange_key_<times>`，
  取不到退回 `exchange_key_default`（前缀常量 `EXCHANGE_KEY_PRE = "exchange_key_"`）。

* `table_resource_exchange[<档位key>]` 是**消耗/产出**：

      spend_key / spend_count                 花什么、花多少
      receive_key_<i> / receive_count_<i>     给什么、给多少（i = 1..MAX_RECEIVE_COUNT=6）
      presented_key / presented_count         额外赠送
      type

  `receive_count = -1` 是「**补满**」哨兵：400001（金条买行动力）的说明就是
  「每次可回满行动力。」，700001 同理补 BP。补满的上限取玩家自己的
  `maxActionPoint`（行动力）或 `table_item[<key>].limit_count`（BP = 6）。

* 客户端把**消耗和产出都乘** `percentage/100`（`getInfoByKey` 里
  `total[k] = floor(total[k] * percentage / 100)`，`percentage` 默认 100，
  只有「第一次兑换」且表里有 `first_exchange_percentage` 时才变）——
  服务端必须照同一条规则算，否则界面显示的和实际给的对不上。

类型（`type` 字段，实测分布）
============================

    10  月卡（`param_1 = pay025`）              ← IAP
    20  充值包（6 元 / 30 元）                  ← IAP
    30  金条 → 萌钞（6 个固定档）
    40  买行动力（金条阶梯 / 糖果 200101~200104）
    50  金条 → 100101 / 100102（各 100 金条）
    60  金条 → 600001（表里没写产出，先按「无产出」跳过）
    70  买 BP（金条阶梯 / 200202 → BP×2）
    80  礼包（`param_1 = bundleXXXXX`，109 个）  ← IAP

一条路由一张表
==============

    exchange.getexchangebycategory  PackageLayer.msgInit("80", updateView1)  ← 礼包页（IAP，回空）
    exchange.exchange               兑换/购买（**服务端算档位**，请求只带 key）
    exchange.exchangecrystal        钻石直购扭蛋次数（依赖扭蛋，未实现 → 回错误码）
    exchange.checkmonthcard         月卡剩余天数（读 player.monthCardDueTimeSec）
    exchange.judgeexchangestate     IAP 下单前判状态（私服不可购买 → 回状态码）
    exchange.checkorder             已有实现（`handlers/payment.py`）
    exchange.payment                已有实现（同上）

⚠️ IAP 那半边（10/20/80 + `judgeexchangestate`/`payment`）在私服里**故意不通**：
没有支付渠道，客户端点下去只会按状态码弹 `table_dictionary` 的提示。
"""

from __future__ import annotations

from . import items, logx, store

log = logx.get("exchange")

# 客户端模块常量：EXCHANGE_KEY_PRE / MAX_RECEIVE_COUNT
EXCHANGE_KEY_PRE = "exchange_key_"
MAX_RECEIVE_COUNT = 15      # 客户端模块体里是 `int8 15`（表里最多只用到 receive_key_6）

# 失败码用**客户端 `EXCHANGE_ERR_CODE_DICT` 里真有的**（实测取的值），
# 客户端拿到非 200 会 `ccuiManager.toast(EXCHANGE_ERR_CODE_DICT[code])`：
#     201 充值成功 / 202 未知兑换类型 / 203 卡槽次数上限 / 204 今天购买次数已用完
#     205 资源不够啦……OAQ / 405 更新数据错误
CODE_UNKNOWN_TYPE = 202
CODE_LIMIT = 204
CODE_NOT_ENOUGH = 205
CODE_DB_ERROR = 405

# ExchangeCenter.EXCHANGE_TYPE（表里 type 的数字）
TYPE_MONTH_CARD = "10"          # RECHARGE_CARD 月卡（IAP）
TYPE_RECHARGE = "20"            # RECHARGE 充值包（IAP）
TYPE_MONEY = "30"               # MONEY 金条 -> 萌钞
TYPE_ACTION_POINT = "40"        # 买行动力（金条阶梯 / 糖果）
TYPE_CARD_SLOT = "50"           # 买卡槽道具（100101）
TYPE_CLEAR_CD = "60"            # 清冷却
TYPE_FRIEND_BOSS_POINT = "70"   # 买 BP（100201）
TYPE_BUNDLE = "80"              # 礼包（IAP）
IN_GAME_TYPES = (TYPE_MONEY, TYPE_ACTION_POINT, TYPE_CARD_SLOT,
                 TYPE_CLEAR_CD, TYPE_FRIEND_BOSS_POINT)

ITEM_GOLD = "100001"          # 金条（客户端 ITEM_KEY.GEM）
ITEM_MONEY = "100002"         # 萌钞
ITEM_ACTION_POINT = "100003"  # 行动力（上限看 player.maxActionPoint）
ITEM_CARD_SLOT = "100101"     # 卡槽
ITEM_BP = "100201"            # BP / 好友 BOSS 点（上限看 table_item.limit_count = 6）

PLAYER_KEY = "exchangeData"

# 每日清零的小时（和分区关卡用同一个：`instance.SUBAREA_RESET_HOUR`）
RESET_HOUR = 5


def item_table() -> dict:
    t = items.table("table_exchange_item")
    return t if isinstance(t, dict) else {}


def resource_table() -> dict:
    t = items.table("table_resource_exchange")
    return t if isinstance(t, dict) else {}


def rows(player: dict) -> dict:
    """玩家已兑换过的行（map，key = item key）。"""
    got = player.get(PLAYER_KEY)
    if not isinstance(got, dict):
        got = {}
        player[PLAYER_KEY] = got
    return got


def new_row(key: str, exchange_key: str = "") -> dict:
    return {
        "exchangeKey": exchange_key or str(key),
        "todayExchangeTimes": 0,
        "totalExchangeTimes": 0,
        "lastExchangeTimeSec": int(store.now_ms() // 1000),
    }


def _reset_hour_epoch() -> int:
    """今天 05:00 的秒级时间戳（跨过它就当作新的一天）。"""
    import time

    now = int(time.time())
    local = time.localtime(now)
    today_reset = int(time.mktime((local.tm_year, local.tm_mon, local.tm_mday,
                                   RESET_HOUR, 0, 0, 0, 0, -1)))
    if now < today_reset:
        today_reset -= 86400
    return today_reset


def reset_daily(player: dict, key: str) -> dict:
    """跨天把 `todayExchangeTimes` 清零 —— 和客户端 `_resetData` 同一个语义。

    ⚠️ 客户端也会自己清一次（它拿 `lastExchangeTimeSec` 跟 `table_constant.common_reset_time`
    比）。两边都清是故意的：客户端清的是它内存里的副本，服务端这份才是存档。
    服务端清完要把 `lastExchangeTimeSec` 推到"今天"，否则客户端一直以为过了天。
    """
    rows_ = rows(player)
    row = rows_.get(str(key))
    if row is None:
        row = rows_[str(key)] = new_row(str(key))
    last = int(row.get("lastExchangeTimeSec") or 0)
    if last < _reset_hour_epoch():
        row["todayExchangeTimes"] = 0
        row["lastExchangeTimeSec"] = int(store.now_ms() // 1000)
    return row


def ladder_key(item: dict, times: int) -> str:
    """客户端 `getExchangeInfoKey` 的规则：`exchange_key_<times>`，取不到用 default。"""
    got = item.get(EXCHANGE_KEY_PRE + str(times))
    if not got:
        got = item.get(EXCHANGE_KEY_PRE + "default")
    return str(got or "")


def plan(key: str, times: int) -> dict | None:
    """算出这一次兑换的档位、消耗、产出（已经按 percentage 折算）。

    返回 `{"ladder":…, "spend": {key: n}, "receive": {key: n}, "percentage": n}`
    或 None（表里查不到）。
    """
    item = item_table().get(str(key))
    if not isinstance(item, dict):
        return None
    ladder = ladder_key(item, times)
    res = resource_table().get(ladder)
    if not isinstance(res, dict):
        log.warning("exchange %s 第 %s 次的档位 %s 在 table_resource_exchange 里没有",
                    key, times, ladder)
        return None

    percentage = 100
    if times == 1 and item.get("first_exchange_percentage"):
        try:
            percentage = int(item["first_exchange_percentage"])
        except (TypeError, ValueError):
            percentage = 100

    def scale(n):
        return int(n) * percentage // 100

    spend: dict = {}
    if res.get("spend_key"):
        spend[str(res["spend_key"])] = scale(res.get("spend_count") or 0)

    receive: dict = {}
    for i in range(1, MAX_RECEIVE_COUNT + 1):
        rk = res.get("receive_key_%d" % i)
        if not rk:
            continue
        rc = res.get("receive_count_%d" % i) or 0
        receive[str(rk)] = receive.get(str(rk), 0) + scale(rc)
    if res.get("presented_key"):
        pk = str(res["presented_key"])
        receive[pk] = receive.get(pk, 0) + scale(res.get("presented_count") or 0)

    return {"ladder": ladder, "spend": spend, "receive": receive, "percentage": percentage}


def _fill_up(player: dict, key: str) -> int:
    """`receive_count = -1` = 补满：返回还差多少。

    行动力用玩家自己的 `maxActionPoint`（客户端 `exchangeActionPoint` 也是这么判的），
    其余道具用 `table_item.limit_count`（BP = 6）。
    """
    cur = items.count_of(player, key)
    if str(key) == ITEM_ACTION_POINT:
        target = int(player.get("maxActionPoint") or 0)
    else:
        row = (items.table("table_item") or {}).get(str(key)) or {}
        target = int(row.get("lc") or row.get("limit_count") or 0)
    return max(0, target - cur)


def exchange(player: dict, key) -> dict:
    """`exchange.exchange {key}` 的业务体（返回完整响应 dict）。

    客户端只发 key，**档位由服务端算**（`todayExchangeTimes + 1`），
    回包 `data` 是**新的那一段行**（带 `exchangeKey`），客户端 `update(data)` 合并。
    """
    key = str(key or "")
    item = item_table().get(key)
    if not item:
        log.warning("exchange.exchange 表里没有 key=%s", key)
        return {"code": CODE_UNKNOWN_TYPE, "msg": "商品不存在", "data": {}}

    if str(item.get("type")) not in IN_GAME_TYPES:
        # 月卡/充值/礼包走 IAP —— 私服没有支付渠道
        log.info("exchange.exchange 拒绝 IAP 商品 %s（type=%s）", key, item.get("type"))
        return {"code": CODE_UNKNOWN_TYPE, "msg": "该商品需要充值", "data": {}}

    row = reset_daily(player, key)
    times = int(row.get("todayExchangeTimes") or 0) + 1
    p = plan(key, times)
    if p is None:
        return {"code": CODE_DB_ERROR, "msg": "档位配置缺失", "data": {}}

    # 扣钱
    for ik, need in p["spend"].items():
        if items.count_of(player, ik) < int(need):
            log.info("exchange.exchange %s 第 %s 次：%s 不够（要 %s 有 %s）",
                     key, times, ik, need, items.count_of(player, ik))
            return {"code": CODE_NOT_ENOUGH, "msg": "资源不够", "data": {}}
    for ik, need in p["spend"].items():
        items.sub_item(player, ik, int(need))

    # 发货（-1 = 补满）
    granted = {}
    for ik, cnt in p["receive"].items():
        cnt = int(cnt)
        if cnt < 0:
            cnt = _fill_up(player, ik)
        if cnt <= 0:
            continue
        items.add_item(player, ik, cnt)
        granted[ik] = granted.get(ik, 0) + cnt

    row["todayExchangeTimes"] = times
    row["totalExchangeTimes"] = int(row.get("totalExchangeTimes") or 0) + 1
    row["lastExchangeTimeSec"] = int(store.now_ms() // 1000)
    row["exchangeKey"] = p["ladder"]

    log.info("exchange.exchange %s（%s）第 %s 次档位=%s 花 %s 得 %s（%s%%）",
             key, item.get("name"), times, p["ladder"], p["spend"], granted, p["percentage"])
    return {"code": 200, "msg": "", "data": row}


def block(player: dict) -> dict:
    """登录块 `data.exchange`：`{<key>: 行}`（客户端 `_exchangeData` 直接用）。"""
    return rows(player)


def by_category(player: dict, category) -> dict:
    """`exchange.getexchangebycategory` 的 data。

    调用方只有 `PackageLayer.msgInit`（固定发 "80" = 礼包页），
    而 80 全是 IAP 礼包 → 私服回空列表，页面就是空的（不是 bug，是没有商品）。
    """
    category = str(category or "")
    out = []
    for key, item in sorted(item_table().items()):
        if str(item.get("type")) != category:
            continue
        if str(item.get("show_status") or "1") == "0":
            continue
        out.append(key)
    log.info("exchange.getexchangebycategory category=%s -> %d 个", category, len(out))
    return out


def month_card_days(player: dict) -> int:
    """月卡剩余天数（客户端 `checkMonthCard` → 回包里的天数）。"""
    import time

    due = player.get("monthCardDueTimeSec") or 0
    try:
        left = int(due) - int(time.time())
    except (TypeError, ValueError):
        left = 0
    return max(0, (left + 86399) // 86400)


def judge_state(player: dict, key) -> dict:
    """`exchange.judgeexchangestate {key}` —— IAP 下单前的状态。

    客户端 `payment/<`：`state !== 0` 就按 state 1/2/3 去 `table_dictionary` 取提示，
    所以私服统一回「不可购买」。月卡已经在生效时回 state=1（已拥有）。
    """
    key = str(key or "")
    item = item_table().get(key) or {}
    if str(item.get("type")) == TYPE_MONTH_CARD and month_card_days(player) > 0:
        return {"code": 200, "msg": "", "data": {"state": 1, "item": key}}
    return {"code": 200, "msg": "", "data": {"state": 2, "item": key}}


def crystal_exchange(player: dict, count) -> dict:
    """`exchange.exchangecrystal {count}` —— 钻石直购扭蛋次数。

    调用方只有 `GachaGem2BuyLayer.onClickBuyButton`，而私服的扭蛋是空卡池
    （缺运营配置，见 differences.md §C），所以这里回「未实现」而不是假装成功。
    """
    log.info("exchange.exchangecrystal count=%s —— 扭蛋未实现，拒绝", count)
    return {"code": CODE_UNKNOWN_TYPE, "msg": "扭蛋暂未开放", "data": {}}
