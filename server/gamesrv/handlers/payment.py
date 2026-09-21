"""exchange.checkorder —— 充值订单检查（**客户端五个渠道模块登录后都会打**）。

客户端调用点是 `src/data/payment/` 下的 5 个渠道模块：
`iabpayment.jsc / iappayment.jsc / onepayment.jsc / quickpayment.jsc / yepayment.jsc`。
每个渠道模块构造时会 `_checkCacheOrder()` + `_checkServerOrder()`，后者每
`PAYMENT_SYNC_INTERVAL`（5 分钟）再拉一次。

反汇编 `OnePayment<._checkServerOrder/<`（**注意读的是 `res` 顶层**，不是 `res.player`）::

    var res = data.data;
    if (!res) return;
    if (res.payment)                  player.payment = res.payment;              // ← 数字！
    if (res.sendFirstChargeReward)    player.sendFirstChargeReward = res.sendFirstChargeReward;
    if (res.dueTimeSec !== undefined) player.monthCardDueTimeSec = res.dueTimeSec;
    if (res.exchangeData) for (key in res.exchangeData) this._updateExchangeData(res.exchangeData[key]);
    if (res.orderList) this._dealServerOrderList(res.orderList);

所以 `data` 的形状是**平铺的**：

    {payment: 累计充值金额（数字）, sendFirstChargeReward: 0/1, dueTimeSec: 月卡到期秒,
     exchangeData: {商品key: 行}, orderList: []}

⚠️ 以前这里发的是 `{player: {payment: {...}, dueTimeSec: 0, monthCardDueTimeSec: 0},
exchangeData: {}, orderList: []}` —— **多包了一层 `player`**，而且 `payment` 当成对象。
客户端因此一个字都读不到：`dataManager.player.payment` 一直是 undefined，
首充界面 `FirstPaymentLayer` 里 `player.payment`（当数字用）直接是 undefined。
"""
from __future__ import annotations

from .. import config, exchange as ex, logx, store
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.payment")


@route("exchange.checkorder")
def check_order(session: dict, msg: dict, req_id):
    """回**平铺**的支付状态 + 兑换数据 + 待发货订单（私服同步发货，`orderList` 恒空）。"""
    account = (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT
    player = store.get_or_create_player(account)
    data = ex.check_order_block(player)
    log.info("exchange.checkorder 累计充值=%s 月卡到期=%s 兑换行=%d",
             data["payment"], data["dueTimeSec"], len(data["exchangeData"]))
    return {"code": CODE_OK, "msg": "", "data": data}
