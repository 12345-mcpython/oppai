"""shop.* —— 商店。

客户端调用点：`src/data/shop.jsc`。

    shop.getshop       Shop.updateShop(shopKey)        拉某个商店的状态
                       Shop.updateActivityShopList()   拉活动商店列表
    shop.buygood       Shop.buyGood(shelfKey, times)   {shelfKey, times}
    shop.refreshshop   Shop.refreshShop(shopKey)       {shopKey}

## 商品表客户端自带，不用我们编内容

`assets/src/table/tableshelf.jsc` 有 **2049 条**，服务端抽成
`gamesrv/data/table_shelf.json`（`script/extract_client_tables.py`）：

    100101 = {shop_key:"1001", good_type:"i", good_key:"100038", good_count:1,
              consume_key_1:"100019", consume_count_1:200, cycle_times_limit:1}

    shop_key          属于哪个商店（对应 table_shop 的 key，如 1001 = 练习场商店）
    good_type/good_key/good_count   卖什么、几个（i=道具, s=军士）
    consume_key_1/consume_count_1   花什么、花多少（一次）
    cycle_times_limit               一个刷新周期内限购几次

客户端 `Shop.judgeBuyGood` 会拿这张表做**客户端侧**校验（等级/公会/限购/消耗/
背包满没满），但真正成交必须服务端说了算 —— 不然改个内存就能白拿。

## 响应形状（原子表读出来的）

    Shop<.updateShop/<     err data shopList T code data _shopObj ccuiManager toast SHOP_ERROR_DICT
        -> `_shopObj[key] = data.data`（**不是** `data.data.shop`）

    Shop<.buyGood/<        err data ret shop record T code data shop _shopObj key record
        -> 读 `data.data.shop`（新的商店状态）和 `data.data.record`（新的购买记录）

    Shop<.refreshShop/<    err data shop T code data _shopObj key
        -> 读 `data.data`

`shop` **不在**客户端的 responseConfig 里，所以不能靠自动派发，得让回调自己读。

## ⚠️ 没做完：`shop.getshop` 的响应形状还没对上

实测（2026-09-19）：

* **`shop.buygood` 已经完全打通** —— 走客户端传输层调
  `server.request('shop.buygood', {shelfKey:'600101', times:1})`，
  存档里 钻石 100000→99950、金币 10000000→10044000、`shopBuyRecord['600101'].times=1`，
  扣钱发货记账全部正确。
* **`shop.getshop` 客户端收下了但不存** —— `Shop._shopObj` 一直是空，
  `getShopList('1001')` 返回 0。服务端这边算得没错（日志里
  「货架 6 个」），是**回包的字段名不对**：客户端 `updateShop` 的回调
  只留了 `shopList` 这个名字，说明它要的大概率是
  `data.data.shopList`（货架对象数组）而不是我们给的
  `{shopKey, todayUpdateTimes, shelfList}`。

另外客户端对 key 有白名单：调 `updateShop('6001')` 会报
`cc.error: checkActivityShop error: unknown shop key = 6001` ——
6001 这种不在它的「活动商店」列表里，得用它在 `table_shop` 里认得的 key（如 1001）。

**下一步**：把 `Shop.updateShop` 的回调反汇编出来（`script/jsc_funcs.py shop.jsc updateShop`），
确定 `_shopObj[key]` 到底存的是哪个字段，再改 `_shop_payload()`。
"""

from __future__ import annotations

from .. import config, items, logx, store
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.shop")


def _account(session: dict) -> str:
    return (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT


def _shops(player: dict) -> dict:
    shops = player.get("shops")
    if not isinstance(shops, dict):
        shops = {}
        player["shops"] = shops
    return shops


def _shop_state(player: dict, shop_key) -> dict:
    """某个商店的状态。没有就建一份。"""
    key = str(shop_key)
    shops = _shops(player)
    state = shops.get(key)
    if not isinstance(state, dict):
        state = {"todayUpdateTimes": 0, "updateTimeSec": int(store.time_obj()["timeSec"])}
        shops[key] = state
    return state


def _shelves_of(shop_key) -> list:
    """这个商店有哪些货架（shelfKey）。从客户端表算，服务端不另存一份。"""
    key = str(shop_key)
    return [k for k, row in items.table("table_shelf").items()
            if str((row or {}).get("shop_key")) == key]


def _shop_payload(player: dict, shop_key) -> dict:
    """回给客户端的一个商店块。`_shopObj[key]` 直接就是它。"""
    state = _shop_state(player, shop_key)
    return {
        "shopKey": str(shop_key),
        "todayUpdateTimes": int(state.get("todayUpdateTimes") or 0),
        "shelfList": _shelves_of(shop_key),
    }


def _buy_record(player: dict) -> dict:
    """购买记录：`{shelfKey: {times, cycleStartSec}}`。

    客户端买完会把 `record` 存进 `_shopObj[key].buyRecordObj` 之类的地方，
    具体字段名客户端自己拍平，服务端只要保证「同一次购买周期内次数一致」。
    """
    rec = player.get("shopBuyRecord")
    if not isinstance(rec, dict):
        rec = {}
        player["shopBuyRecord"] = rec
    return rec


@route("shop.getshop")
def get_shop(session: dict, msg: dict, req_id):
    """拉商店状态。

    请求体会带 `{key: shopKey}`（`updateShop`）或 `{type: SHOP_TYPE.TIME_LIMIT}`
    （`updateActivityShopList`）。带 type 的那种是活动商店列表，这里先只回
    当前 key 的（没有 key 就回第一个商店），别把 type 当 key 用。
    """
    player = store.get_or_create_player(_account(session))
    shop_key = (msg or {}).get("key")
    if shop_key is None:
        shop_key = (msg or {}).get("type")
    if shop_key is None or not str(shop_key).isdigit():
        shop_key = next(iter(items.table("table_shop")), "1001")
    payload = _shop_payload(player, shop_key)
    log.info("shop.getshop key=%s 货架 %d 个，今日刷新 %s 次",
             shop_key, len(payload["shelfList"]), payload["todayUpdateTimes"])
    return {"code": CODE_OK, "msg": "", "data": payload}


@route("shop.refreshshop")
def refresh_shop(session: dict, msg: dict, req_id):
    """手动刷新商店。

    扣多少、能刷几次是客户端按 `table_shop[key].manual_update_times_limit`
    和 `getRefreshShopConsume()` 算的；服务端就负责记次数。
    """
    player = store.get_or_create_player(_account(session))
    shop_key = (msg or {}).get("shopKey") or (msg or {}).get("key")
    state = _shop_state(player, shop_key)
    state["todayUpdateTimes"] = int(state.get("todayUpdateTimes") or 0) + 1
    state["updateTimeSec"] = int(store.time_obj()["timeSec"])
    store.save_player(player)
    log.info("shop.refreshshop key=%s -> 今日第 %s 次", shop_key, state["todayUpdateTimes"])
    return {"code": CODE_OK, "msg": "", "data": _shop_payload(player, shop_key)}


@route("shop.buygood")
def buy_good(session: dict, msg: dict, req_id):
    """买货架上的东西。请求体 `{shelfKey, times}`。

    流程：查 table_shelf -> 校验限购 -> 校验钱够 -> 扣钱 -> 发货 -> 回新状态。

    ⚠️ **一切以服务端为准**：客户端的 judgeBuyGood 只是为了让按钮变灰，
    不能信它算出来的消耗或限购。所以这里全部重算一遍。

    花钱 / 发货走 `gamesrv/items.py`：物品进背包（`player["items"]`），
    军士进名单（`good_type == "s"`）。
    """
    account = _account(session)
    player = store.get_or_create_player(account)
    shelf_key = str((msg or {}).get("shelfKey") or "")
    try:
        times = int((msg or {}).get("times") or 1)
    except (TypeError, ValueError):
        times = 1
    if times < 1:
        times = 1

    shelf = items.table("table_shelf").get(shelf_key)
    if not shelf:
        log.warning("shop.buygood 未知货架 %s", shelf_key)
        return {"code": CODE_OK, "msg": "unknown shelf", "data": {}}

    shop_key = str(shelf.get("shop_key") or "")
    cost_key = str(shelf.get("consume_key_1") or "")
    cost_each = int(shelf.get("consume_count_1") or 0)
    total_cost = cost_each * times
    limit = int(shelf.get("cycle_times_limit") or 0)

    # 限购
    record = _buy_record(player)
    bought = int((record.get(shelf_key) or {}).get("times") or 0)
    if limit > 0 and bought + times > limit:
        log.info("shop.buygood %s 超限购（已买 %d + %d > %d）", shelf_key, bought, times, limit)
        return {"code": CODE_OK, "msg": "over limit",
                "data": {"shop": _shop_payload(player, shop_key),
                         "record": record.get(shelf_key) or {}}}

    # 扣钱
    if total_cost > 0 and not items.sub_item(player, cost_key, total_cost):
        have = items.count_of(player, cost_key)
        log.info("shop.buygood %s 钱不够（要 %d 有 %d）", shelf_key, total_cost, have)
        return {"code": CODE_OK, "msg": "not enough",
                "data": {"shop": _shop_payload(player, shop_key),
                         "record": record.get(shelf_key) or {}}}

    # 发货
    good_type = str(shelf.get("good_type") or "i")
    good_key = str(shelf.get("good_key") or "")
    good_count = int(shelf.get("good_count") or 0) * times
    granted = {}
    if good_type == "i":
        granted = items.settle(player, [(items.REWARD_TYPE["ITEM"], good_key, good_count)])
    elif good_type == "s":
        granted = items.settle(player, [(items.REWARD_TYPE["SOLDIER"], good_key, good_count)])
    else:
        # h=主角 / m=机甲 / e=装备 … 这些还没有对应的数据结构，别假装发了
        log.warning("shop.buygood %s 的 good_type=%s 还没实现发放，只扣钱", shelf_key, good_type)

    record[shelf_key] = {"times": bought + times, "shopKey": shop_key}
    store.save_player(player)
    log.info("shop.buygood %s x%d：花 %s×%d，得 %s(%s)×%d %s",
             shelf_key, times, cost_key, total_cost, good_key, good_type, good_count,
             "已发" if granted else "未发")

    return {
        "code": CODE_OK,
        "msg": "",
        "data": {
            "shop": _shop_payload(player, shop_key),
            "record": record[shelf_key],
        },
    }
