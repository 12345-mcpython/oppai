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

⚠️ IAP（10/20/80）现在**通了**：私服没有支付渠道，改成「点购买直接成功」
（客户端 `patch.js` 的 PAY-SUCCESS 把 `op.pay` 换成立刻回调），服务端
`exchange.payment` 收到 `payInfo` 就按**同一套档位表**发货（见下面 `payment()`）。

支付链路（`src/data/exchangecenter.jsc` + `src/data/payment/*.jsc` 反汇编）::

    ExchangeCenter.payment(key)                      // 点购买
      -> exchange.judgeexchangestate {key}           // state 0 才继续（1 已售完 / 2 未到时间 / 3 等级不够）
      -> productKey = table_exchange_item[key].param_1
      -> (月卡先 exchange.checkmonthcard：remainDay > buy_month_card_days_limit 就中止)
      -> clientOrderId = op.getClientOrderId()       // uuid.v4()
      -> _payment.payment(productKey, clientOrderId, playerId, next)
           -> op.pay(...)  ★ 客户端补丁在这里直接回调成功
           -> Payment.exchangePay(payInfo)           // exchange.payment {payInfo}
      -> 回包 {result, player:{payment, sendFirstChargeReward}, dueTimeSec}

⚠️ 两个回包形状都很容易搞错（都是**反汇编里读出来的**）:

* `exchange.payment` 的 `data.result` 才是那段新行（客户端 `update(data.result)`）；
* `exchange.checkorder` 的 `data` 是**平铺**的
  `{payment, sendFirstChargeReward, dueTimeSec, exchangeData, orderList}`
  —— `payment` 是**数字**（累计充值金额，`Player.ctor` 里 `_payment = data.payment || 0`），
  不是对象！以前发成 `{player: {...}}` 那一层，客户端一个字都读不到。
"""

from __future__ import annotations

import time

from . import items, logx, quests, store

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
ITEM_FRAGMENT = "100016"      # 好人卡（抽卡碎片）

# ---- IAP（充值）----
#
# 116 个 IAP 商品全是「`param_1` = table_payment 的 key」+「`exchange_key_default`
# 指向 table_resource_exchange 里的产出行」（没有 spend_key —— 花的是真钱）。
# `param_1` 实测**唯一**（116 个商品 116 个不同值），所以 payInfo 里带 productKey
# 就足以定位商品，不用再记「玩家刚才点了哪个」。
IAP_TYPES = (TYPE_MONTH_CARD, TYPE_RECHARGE, TYPE_BUNDLE)
PAYMENT_TABLE = "table_payment"
PLAYER_PAYMENT_KEY = "payment"          # player["payment"] = **累计充值金额（数字）**
PLAYER_PAID_ORDERS = "paidOrders"       # {clientOrderId: {key, price, timeSec}} 幂等用
PAID_ORDERS_MAX = 200

# 月卡：`table_constant.month_card_days = 30`；每天 75 金条这条只写在
# `table_exchange_item[100001].desc` 里（「30天内每天可领取75金条」），客户端没有表。
MONTH_CARD_DAILY_GOLD = 75
MONTH_CARD_REWARD_DAY = "monthCardRewardDay"

# 首充：门槛取自 `table_constant.charge_reward_need_payment = 30`（元）。
# ⚠️ **奖励内容客户端全库没有**（`charge_reward` 只在 table_constant 里有门槛那一项，
# `ExchangeCenter.receiveReward` 是死代码）→ 这是私服自己定的（differences §D）。
FIRST_CHARGE_REWARD = {ITEM_GOLD: 200, ITEM_MONEY: 50000, ITEM_FRAGMENT: 10}
FIRST_CHARGE_SENT = "firstChargeSent"

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


def payment_table() -> dict:
    t = items.table(PAYMENT_TABLE)
    return t if isinstance(t, dict) else {}


def iap_items() -> dict:
    """`{param_1（= table_payment 的 key）: (商品key, 商品行)}`（只有 IAP 类型）。"""
    out = {}
    for key, item in item_table().items():
        if str(item.get("type")) not in IAP_TYPES:
            continue
        product = str(item.get("param_1") or "")
        if product:
            out[product] = (str(key), item)
    return out


def iap_of_product(product_key) -> tuple | None:
    """`payInfo.productKey` -> `(商品key, 商品行)`；不是 IAP 商品就 None。"""
    return iap_items().get(str(product_key or ""))


def is_iap(key) -> bool:
    item = item_table().get(str(key or "")) or {}
    return str(item.get("type")) in IAP_TYPES


def month_card_due(player: dict) -> int:
    try:
        return int(player.get("monthCardDueTimeSec") or 0)
    except (TypeError, ValueError):
        return 0


def plan(key: str, times: int, total_times: int | None = None) -> dict | None:
    """算出这一次兑换的档位、消耗、产出（已经按 percentage 折算）。

    返回 `{"ladder":…, "spend": {key: n}, "receive": {key: n}, "percentage": n}`
    或 None（表里查不到）。

    ⚠️ `first_exchange_percentage`（首充/首次双倍）判的是**累计**次数，不是今天的：
    客户端 `getInfoByKey` 里是 `totalTimes = data.totalExchangeTimes + 1`，只有
    `totalTimes === 1` 才用那个百分比。以前这里拿 `todayExchangeTimes + 1` 判 ——
    每日清零之后又变回 1，等于**天天都能再领一次双倍**。
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
    if int(total_times if total_times is not None else times) == 1 \
            and item.get("first_exchange_percentage"):
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


def deliver(player: dict, key, paid: bool = False) -> dict:
    """发货：算档位 →（非 IAP）扣钱 → 发奖 → 记次数。返回 `(row, granted, plan)`。

    `paid=True` 表示这是 IAP（钱已经付了）—— 跳过扣道具那一步。
    ⚠️ IAP 商品的 `table_resource_exchange` 行**本来就没有 `spend_key`**，
    所以就算不跳过也扣不到东西，但显式跳过更不容易被后来的表改动坑到。
    """
    key = str(key or "")
    item = item_table().get(key)
    if not item:
        return {}

    row = reset_daily(player, key)
    times = int(row.get("todayExchangeTimes") or 0) + 1
    total_times = int(row.get("totalExchangeTimes") or 0) + 1
    p = plan(key, times, total_times)
    if p is None:
        return {}

    if not paid:
        for ik, need in p["spend"].items():
            if items.count_of(player, ik) < int(need):
                log.info("exchange %s 第 %s 次：%s 不够（要 %s 有 %s）",
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
    row["totalExchangeTimes"] = total_times
    row["lastExchangeTimeSec"] = int(store.now_ms() // 1000)
    # ⚠️ `exchangeKey` 必须是**商品自己的 key**，不是档位 key（`exchange_key_default`
    # 那种 `210009` / `400012`）：客户端 `ExchangeCenter.update(data)` 会拿它当
    # `_exchangeData` 的下标（`getExchangeData(key)` 是按商品 key 查的），并且立刻
    # `table_exchange_item[data.exchangeKey].type` —— 档位 key 在那张表里**不存在**，
    # 会直接 TypeError。
    row["exchangeKey"] = key

    log.info("exchange %s（%s）第 %s 次档位=%s 花 %s 得 %s（%s%%）%s",
             key, item.get("name"), times, p["ladder"], p["spend"], granted, p["percentage"],
             "【IAP 已支付】" if paid else "")
    return {"row": row, "granted": granted, "plan": p, "item": item, "times": times}


def exchange(player: dict, key) -> dict:
    """`exchange.exchange {key}` 的业务体（返回完整响应 dict）。

    客户端只发 key，**档位由服务端算**（`todayExchangeTimes + 1`），
    回包 `data` 是 `{result: 新行}` —— `ExchangeCenter.exchange/<` 读的是
    **`data.result`**（不是 `data` 本身），给错了客户端那边 `update()` 不会被调用，
    档位/次数要重登才刷新。
    """
    key = str(key or "")
    item = item_table().get(key)
    if not item:
        log.warning("exchange.exchange 表里没有 key=%s", key)
        return {"code": CODE_UNKNOWN_TYPE, "msg": "商品不存在", "data": {}}

    if str(item.get("type")) in IAP_TYPES:
        # 月卡/充值包/礼包走充值（`exchange.payment`），客户端也不会用这条
        log.info("exchange.exchange 拒绝 IAP 商品 %s（type=%s）—— 走充值流程",
                 key, item.get("type"))
        return {"code": CODE_UNKNOWN_TYPE, "msg": "该商品需要充值", "data": {}}

    out = deliver(player, key)
    if not out:
        return {"code": CODE_DB_ERROR, "msg": "档位配置缺失", "data": {}}
    if out.get("code"):
        return out
    return {"code": 200, "msg": "", "data": {"result": out["row"]}}


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
    """`exchange.judgeexchangestate {key}` —— 充值下单前的状态（客户端只认 state 0）。

    `ExchangeCenter.payment/<` 里 `state !== 0` 就 `tableswitch(1..3)` 弹文案：

        1 `table_dictionary[3123]` 已售完
        2 `table_dictionary[3124]` 时间未到
        3 `op.getDictTipsString(3122, item.open_lv)` ->「#@1@#级开放」

    ⚠️ **时间窗不校验**（原版那些 `begin_time/ended_time` 是 2016 年的活动档期，
    90 个礼包全都过期了，照表卡的话商店里几乎什么都买不了）；购买次数上限也不卡
    （`exchange_num`/`interval_day`）—— 私服买多少都行，见 differences §B。
    """
    key = str(key or "")
    item = item_table().get(key) or {}
    if not item:
        return {"code": 200, "msg": "", "data": {"state": 1, "item": key}}
    open_lv = item.get("open_lv")
    try:
        need_lv = int(open_lv) if open_lv not in (None, "") else 0
    except (TypeError, ValueError):
        need_lv = 0
    if need_lv and int(player.get("lv") or 1) < need_lv:
        return {"code": 200, "msg": "", "data": {"state": 3, "item": key}}
    return {"code": 200, "msg": "", "data": {"state": 0, "item": key}}


def crystal_exchange(player: dict, count) -> dict:
    """`exchange.exchangecrystal {count}` —— 钻石直购扭蛋次数。

    调用方只有 `GachaGem2BuyLayer.onClickBuyButton`，而私服的扭蛋是空卡池
    （缺运营配置，见 differences.md §C），所以这里回「未实现」而不是假装成功。
    """
    log.info("exchange.exchangecrystal count=%s —— 扭蛋未实现，拒绝", count)
    return {"code": CODE_UNKNOWN_TYPE, "msg": "扭蛋暂未开放", "data": {}}


# ---------------------------------------------------------------------------
# 充值（IAP）
# ---------------------------------------------------------------------------

def paid_orders(player: dict) -> dict:
    got = player.get(PLAYER_PAID_ORDERS)
    if not isinstance(got, dict):
        got = {}
        player[PLAYER_PAID_ORDERS] = got
    return got


def play_payment(player: dict) -> int:
    """`player.payment` —— 客户端 `Player.ctor` 是 `_payment = data.payment || 0`，
    `FirstPaymentLayer` 拿它当**数字**用（`nowPayLabel.string = payment`、
    `nowPayPanel.visible = payment < charge_reward_need_payment`）。"""
    try:
        return int(player.get(PLAYER_PAYMENT_KEY) or 0)
    except (TypeError, ValueError):
        return 0


def first_charge_need() -> int:
    """首充门槛（元）：`table_constant.charge_reward_need_payment`。"""
    row = items.table("table_constant")
    try:
        return int((row or {}).get("charge_reward_need_payment") or 0)
    except (TypeError, ValueError):
        return 0


def month_card_days_add() -> int:
    row = items.table("table_constant")
    try:
        return int((row or {}).get("month_card_days") or 30)
    except (TypeError, ValueError):
        return 30


def ensure(player: dict, now: int | None = None) -> bool:
    """月卡每天的金条（`table_exchange_item[100001].desc`：30 天内每天 75 金条）。

    客户端**没有**领取月卡每日奖励的路由（`jsc_find monthCardDueTimeSec` 只有
    「显示剩余天数」那几个），所以只能服务端在登录时结算：跨过换日点（05:00）
    且月卡还在有效期内就发一份。中间隔了好几天只补一份（不追溯）——
    想追溯的话把 `monthCardRewardDay` 改成记时间戳、按天数补齐即可。
    """
    ts = int(now if now is not None else time.time())
    if month_card_due(player) <= ts:
        return False
    today = quests.day_str(ts)
    if player.get(MONTH_CARD_REWARD_DAY) == today:
        return False
    player[MONTH_CARD_REWARD_DAY] = today
    items.add_item(player, ITEM_GOLD, MONTH_CARD_DAILY_GOLD)
    log.info("月卡每日奖励：%s 金条（到期 %s）", MONTH_CARD_DAILY_GOLD,
             store.time_str(month_card_due(player)))
    return True


def payment(player: dict, msg: dict) -> dict:
    """`exchange.payment {payInfo}` —— 充值回执（私服里就是「点购买直接成功」）。

    `payInfo` 是客户端补丁（`patch.js` 的 PAY-SUCCESS）现造的：
    `{clientOrderId, productKey, productId, productName, price, receipt, txid}`；
    原版这里收到的是渠道 SDK 的支付凭证（`receipt`/`txid`），私服没有渠道，
    所以**只按 `productKey` 认商品**（实测 116 个商品的 `param_1` 唯一）。

    发货走 `deliver(player, key, paid=True)`：和游戏内兑换**同一套档位表**
    （`exchange_key_default` 指向产出行；没有 `spend_key` = 花的是真钱）。

    回包形状（客户端 `Payment.exchangePay/<`）::

        data.result      -> ExchangeCenter.update(row)
        data.player      -> {payment: 累计充值金额（数字）, sendFirstChargeReward: 0/1}
        data.dueTimeSec  -> 月卡到期时间（秒，绝对时间戳）

    ⚠️ **同一个 `clientOrderId` 只发一次**（`player["paidOrders"]`）：客户端
    重试/切后台回来会重发同一个订单，不记的话就是无限刷。
    """
    from .gameproto import CODE_OK

    info = (msg or {}).get("payInfo") or {}
    if not isinstance(info, dict):
        return {"code": CODE_UNKNOWN_TYPE, "msg": "购买内容不存在", "data": {}}
    product_key = str(info.get("productKey") or "")
    found = iap_of_product(product_key)
    if not found:
        log.warning("exchange.payment 收到不认识的 productKey=%s（payInfo=%s）",
                    product_key, str(info)[:200])
        return {"code": CODE_UNKNOWN_TYPE, "msg": "购买内容不存在", "data": {}}
    key, item = found
    order_id = str(info.get("clientOrderId") or "") or product_key

    table_row = payment_table().get(product_key) or {}
    try:
        price = int(info.get("price") or table_row.get("price") or 0)
    except (TypeError, ValueError):
        price = 0

    orders = paid_orders(player)
    if order_id in orders:
        # 重复回执：不重复发货，把当前状态原样回给客户端
        log.info("exchange.payment 订单 %s 已经发过货（%s），忽略", order_id, key)
        row = rows(player).get(key) or new_row(key)
        return _payment_ok(player, row, item, due=month_card_due(player), repeat=True)

    out = deliver(player, key, paid=True)
    if not out or out.get("code"):
        log.warning("exchange.payment 发货失败 key=%s out=%s", key, out)
        return {"code": CODE_DB_ERROR, "msg": "发货失败", "data": {}}

    # 累计充值金额（首充进度就靠它）
    player[PLAYER_PAYMENT_KEY] = play_payment(player) + price
    orders[order_id] = {"key": key, "price": price, "timeSec": int(time.time())}
    for old in list(orders)[:-PAID_ORDERS_MAX]:
        del orders[old]

    # 月卡：到期时间往后加 30 天（客户端 `checkMonthCard` 会拦 remainDay > 3 的）
    due = 0
    if str(item.get("type")) == TYPE_MONTH_CARD:
        base = max(int(time.time()), month_card_due(player))
        player["monthCardDueTimeSec"] = base + month_card_days_add() * 86400
        due = player["monthCardDueTimeSec"]
        player[MONTH_CARD_REWARD_DAY] = ""      # 当天就能领第一份每日金条

    # 首充：累计充值够门槛就发一次（内容是我们自己定的，客户端没有这张表）
    if not player.get(FIRST_CHARGE_SENT):
        need = first_charge_need()
        if need and play_payment(player) >= need:
            player[FIRST_CHARGE_SENT] = 1
            # `sendFirstChargeReward` 是客户端 `Player._sendFirstChargeReward`
            # 那个字段（登录块 / exchange.checkorder / exchange.payment 都会带下去），
            # 原版拿它表示「首充奖励已发放」。客户端目前只是存着，没有界面读它。
            player["sendFirstChargeReward"] = 1
            for ik, cnt in FIRST_CHARGE_REWARD.items():
                items.add_item(player, ik, cnt)
            log.info("首充奖励已发放（累计 %s >= %s 元）：%s",
                     play_payment(player), need, FIRST_CHARGE_REWARD)

    log.info("exchange.payment %s（%s）%s 元 订单=%s 累计=%s 得 %s",
             key, item.get("name"), price, order_id, play_payment(player), out["granted"])
    return _payment_ok(player, out["row"], item, due=due)


def _payment_ok(player: dict, row: dict, item: dict, due: int = 0,
                repeat: bool = False) -> dict:
    """充值成功的回包（形状见 `payment()` 的注释）。"""
    return {"code": 200, "msg": "", "data": {
        "result": row,
        "player": {
            "payment": play_payment(player),
            "sendFirstChargeReward": int(player.get("sendFirstChargeReward") or 0),
        },
        "dueTimeSec": int(due or 0),
        "repeat": bool(repeat),
    }}


def check_order_block(player: dict) -> dict:
    """`exchange.checkorder` 的 `data`（客户端 `OnePayment._checkServerOrder/<`）。

    ⚠️ **平铺的五个键**，而且 `payment` 是**数字**（累计充值金额）：
    它是 `dataManager.player.payment = res.payment`（不是 `res.player.payment`）。
    以前我们发的是 `{player: {payment: {...}, ...}, exchangeData, orderList}`，
    客户端一个字都读不到（`player.payment` 永远是 undefined → 首充界面
    `FirstPaymentLayer` 读 `payment.nowPay` 那一层直接崩）。
    """
    return {
        "payment": play_payment(player),
        "sendFirstChargeReward": int(player.get("sendFirstChargeReward") or 0),
        "dueTimeSec": month_card_due(player),
        # {商品key: 行} —— 客户端对每一行调 `_updateExchangeData(row)`
        "exchangeData": {k: dict(v) for k, v in rows(player).items()},
        # 已付款还没发货的订单：私服是同步发货，永远为空
        "orderList": [],
    }
