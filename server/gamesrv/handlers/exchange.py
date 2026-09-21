"""exchange.* —— 黑市交易所 / 充值（见 `gamesrv/exchange.py` 的模块注释）。

客户端那 7 条路由：

    exchange.getexchangebycategory  {category}        → data = 该分类的商品 key 列表
    exchange.exchange               {key}             → data = **`{result: 新行}`**
    exchange.exchangecrystal        {count}           → 钻石直购扭蛋（扭蛋未实现 → 非 200）
    exchange.checkmonthcard         {}                → data = {remainDay}
    exchange.judgeexchangestate     {key}             → data = {state, item}
    exchange.checkorder             {}                → data = 平铺的支付状态（见 handlers/payment.py）
    exchange.payment                {payInfo}         → 充值成功 + 发货

⚠️ 回包形状都按反汇编钉过，最容易搞错的两处：

* `exchange.exchange` 的**新行在 `data.result`** 里（`ExchangeCenter.exchange/<`
  读的是 `data.result`，不是 `data` 本身）；
* `exchange.payment` 的 `data` 是 `{result, player:{payment, sendFirstChargeReward},
  dueTimeSec}`（`Payment.exchangePay/<`），其中 `player.payment` 是**数字**。

⚠️ 每条**改背包**的回包都带 `items` 变更块（`patch.js` 的 RESP-DISPATCH
`data.items -> bag.updateItems`），不然买完东西顶部货币条要重登才动。
"""

from __future__ import annotations

from .. import config, exchange as ex, items, logx, store
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.exchange")


def _player(session: dict) -> dict:
    account = (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT
    return store.get_or_create_player(account)


def _bag_snapshot(player: dict) -> dict:
    return {str(k): int(v or 0) for k, v in items.items_of(player).items()}


def _items_block(player: dict, before: dict) -> dict:
    """只回这次真变了的、而且改动前就有的 key（`Bag.updateItems` 的约束）。"""
    out = {}
    for key, count in items.items_of(player).items():
        k = str(key)
        if k in before and int(count or 0) != before[k]:
            out[k] = int(count or 0)
    return out


def _reply(player: dict, result: dict, before: dict) -> dict:
    if int(result.get("code") or 0) == CODE_OK:
        data = result.get("data")
        if isinstance(data, dict):
            data["items"] = _items_block(player, before)
        store.save_player(player)
    return result


@route("exchange.getexchangebycategory")
def get_exchange_by_category(session: dict, msg: dict, req_id):
    player = _player(session)
    return {"code": CODE_OK, "msg": "",
            "data": ex.by_category(player, (msg or {}).get("category"))}


@route("exchange.exchange")
def do_exchange(session: dict, msg: dict, req_id):
    player = _player(session)
    before = _bag_snapshot(player)
    return _reply(player, ex.exchange(player, (msg or {}).get("key")), before)


@route("exchange.exchangecrystal")
def exchange_crystal(session: dict, msg: dict, req_id):
    player = _player(session)
    return ex.crystal_exchange(player, (msg or {}).get("count"))


@route("exchange.checkmonthcard")
def check_month_card(session: dict, msg: dict, req_id):
    """客户端 `checkMonthCard/<` 读的是 `data.remainDay`（不是 days）。"""
    player = _player(session)
    return {"code": CODE_OK, "msg": "",
            "data": {"remainDay": ex.month_card_days(player)}}


@route("exchange.judgeexchangestate")
def judge_exchange_state(session: dict, msg: dict, req_id):
    """充值下单前的状态：`state == 0` 客户端才会往下走。"""
    player = _player(session)
    return ex.judge_state(player, (msg or {}).get("key"))


@route("exchange.payment")
def exchange_payment(session: dict, msg: dict, req_id):
    """`Payment.exchangePay` —— 充值回执，**私服里就是「点购买直接成功」**。

    客户端补丁（`patch.js` 的 PAY-SUCCESS）把 `op.pay` 换成立刻回调，
    于是 `payInfo` 由客户端现造（`{clientOrderId, productKey, productId, price, …}`），
    服务端按 `productKey` 找到那个 IAP 商品、照 `table_resource_exchange` 发货。
    业务体在 `gamesrv/exchange.py` 的 `payment()`（含首充、月卡、订单幂等）。
    """
    player = _player(session)
    before = _bag_snapshot(player)
    return _reply(player, ex.payment(player, msg or {}), before)
