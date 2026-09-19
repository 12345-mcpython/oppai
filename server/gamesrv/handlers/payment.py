"""exchange.checkorder —— 支付订单检查。

**名字骗人**：它不在兑换商店（ExchangeCenter）里，而是**支付**用的。
客户端调用点是 `src/data/payment/` 下的 5 个渠道模块：

    iabpayment.jsc / iappayment.jsc / onepayment.jsc / quickpayment.jsc / yepayment.jsc

每个渠道模块登录后都会打一次 `exchange.checkorder`，检查服务端上有没有
「已付款但还没发货」的订单，顺便拿首充奖励 / 月卡到期时间。

反汇编 `OnePayment<._checkServerOrder`：

    self.server.request('exchange.checkorder', ..., function (err, data) {
        var res      = data.data;          // ← 直接吃 data.data
        var player   = res.player;         //   player.payment.sendFirstChargeReward
                                           //   player.dueTimeSec / monthCardDueTimeSec
        var excData  = res.exchangeData;
        self._updateExchangeData(excData);
        self._dealServerOrderList(res.orderList);
    });

所以 `data.data` 的形状是：

    {"player": {..., "payment": {...}}, "exchangeData": {...}, "orderList": [...]}

⚠️ `player.payment` **必须存在**。给 `{}` 会变成 `player.payment.sendFirstChargeReward`
读 undefined 的属性 → TypeError，然后 `_updateExchangeData` / `_dealServerOrderList`
都不会执行。这就是之前一直回空 data 时 logcat 里那批 JS 异常的来源。

私服没有支付渠道，也就永远没有订单要发货，所以：
`orderList = []`、`dueTimeSec = 0`、`monthCardDueTimeSec = 0`、首充奖励不补发。
"""

from __future__ import annotations

from .. import logx
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.payment")


def _payment_block() -> dict:
    """`data.data`：玩家支付状态 + 兑换数据 + 待发货订单。

    这里的 `player` 是**支付视角的玩家块**，不是完整的 player 存档 ——
    客户端只从这里读 `payment.sendFirstChargeReward` / `dueTimeSec` /
    `monthCardDueTimeSec` 三个东西，所以不需要（也不该）把整份存档塞进来。
    """
    return {
        "player": {
            "payment": {
                "sendFirstChargeReward": 0,   # 0 = 没有首充奖励要补发
            },
            "dueTimeSec": 0,
            "monthCardDueTimeSec": 0,         # 0 = 没有月卡
        },
        "exchangeData": {},
        "orderList": [],                      # 已付款待发货的订单，私服永远为空
    }


@route("exchange.checkorder")
def check_order(session: dict, msg: dict, req_id):
    """检查服务端订单。回空但**形状完整**的包，让客户端五个渠道模块都能解析。"""
    log.info("exchange.checkorder（私服无支付渠道，回空订单）")
    return {"code": CODE_OK, "msg": "", "data": _payment_block()}
