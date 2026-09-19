"""item.* —— 背包。

客户端调用点：`src/data/bag.jsc`。

    item.changeitems    Bag.changeItems(items, cb)   {items}

反汇编：

    Bag<.changeItems       items cb that dataManager isLogin server request item.changeitems items
    Bag<.changeItems/<     err data result T data updateItems
        -> `var result = data.data; if (data.code == 200) this.updateItems(result)`

`Bag.updateItems(items)` 是**按 key 覆盖数量**的，所以服务端要回**权威的整包**
（`{itemKey: count}`），客户端拿它对齐，而不是回增量。

## ⚠️ 这条路由在客户端是死代码

把 `assets/src/**` 全搜了一遍：`changeItems` 这个标识符**只出现在 bag.jsc 自己的
定义处**，没有任何调用者。`Bag` 里真正在用服务端数据的是 `updateItems`
（走 responseConfig 的 `item` 块，即登录包那条路）。

所以这条 handler 的实际作用是「万一以后客户端开始调它，别落到未实现的 route」，
以及给调试台一个手动对齐背包的入口。别指望它现在能触发。
"""

from __future__ import annotations

from .. import config, items, logx, store
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.item")


def _account(session: dict) -> str:
    return (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT


@route("item.changeitems")
def change_items(session: dict, msg: dict, req_id):
    """回权威背包。请求体里的 `items` 只记日志，不当成真话来用。"""
    account = _account(session)
    player = store.get_or_create_player(account)
    asked = (msg or {}).get("items") if isinstance(msg, dict) else msg
    log.info("item.changeitems account=%s 客户端报了 %s（回权威背包）",
             account, asked)
    return {"code": CODE_OK, "msg": "", "data": items.items_of(player)}
