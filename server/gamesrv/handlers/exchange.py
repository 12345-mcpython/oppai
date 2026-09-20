"""exchange.* —— 黑市交易所 / 充值页（见 `gamesrv/exchange.py` 的模块注释）。

客户端那 5 条路由：

    exchange.getexchangebycategory  {category}        → data = 该分类的商品 key 列表
    exchange.exchange               {key}             → data = **新的那一段行**（带 exchangeKey）
    exchange.exchangecrystal        {count}           → 钻石直购扭蛋（扭蛋未实现 → 非 200）
    exchange.checkmonthcard         {}                → data = {remainDay}
    exchange.judgeexchangestate     {key}             → data = {state, item}（state≠0 客户端弹提示）

`exchange.checkorder` / `exchange.payment` 在 `handlers/payment.py` 里（早就实现了）。
"""

from __future__ import annotations

from .. import config, exchange as ex, logx, store
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.exchange")


def _player(session: dict) -> dict:
    account = (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT
    return store.get_or_create_player(account)


@route("exchange.getexchangebycategory")
def get_exchange_by_category(session: dict, msg: dict, req_id):
    player = _player(session)
    return {"code": CODE_OK, "msg": "",
            "data": ex.by_category(player, (msg or {}).get("category"))}


@route("exchange.exchange")
def do_exchange(session: dict, msg: dict, req_id):
    player = _player(session)
    result = ex.exchange(player, (msg or {}).get("key"))
    if result.get("code") == CODE_OK:
        # `_player()` 是 load 出来的副本，扣的道具/涨的次数必须写回
        store.save_player(player)
    return result


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
    player = _player(session)
    return ex.judge_state(player, (msg or {}).get("key"))


@route("exchange.payment")
def exchange_payment(session: dict, msg: dict, req_id):
    """`Payment.exchangePay` —— IAP 下单回执。

    私服没有支付渠道（`judgeexchangestate` 那一步就回 state≠0 把流程挡在客户端了），
    所以这里只可能是伪造请求。**故意回非 200**，别让它看着像"充值成功"
    （`EXCHANGE_ERR_CODE_DICT["201"]` 就是「充值成功」，回 200 客户端会当成功）。
    """
    log.info("exchange.payment msg=%s —— 私服没有支付渠道，拒绝", msg)
    return {"code": ex.CODE_UNKNOWN_TYPE, "msg": "充值未开放", "data": {}}
