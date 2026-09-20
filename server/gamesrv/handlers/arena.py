"""arena.* —— 演习场（主界面「演习场」按钮，见 `gamesrv/arena.py`）。

四条路由，形状照客户端反汇编抄：

    arena.getrivallist  {}                          → data.arena（RESP-DISPATCH 喂给 ArenaCenter）
    arena.resetrivals   {useGold}                   → data.arena（冷却内要花金条）
    arena.enterfight    {index}                     → data.arena
    arena.exitfight     {index, success, battleInfo} → data 里平铺 success/rewards/winPoints/…
                                                      **外加** data.arena

⚠️ 四条都必须在 `data` 里带 `arena` 块：客户端那条链的 cb 不带参数，
   数据全靠 `patch.js` 的 RESP-DISPATCH（`arena -> arenaCenter.updateByServer`）。
"""

from __future__ import annotations

from .. import arena, config, logx, store
from . import route

log = logx.get("handler.arena")


def _player(session: dict) -> dict:
    account = (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT
    return store.get_or_create_player(account)


@route("arena.getrivallist")
def get_rival_list(session: dict, msg: dict, req_id):
    player = _player(session)
    result = arena.get_rival_list(player)
    store.save_player(player)
    return result


@route("arena.resetrivals")
def reset_rivals(session: dict, msg: dict, req_id):
    player = _player(session)
    result = arena.reset_rivals(player, (msg or {}).get("useGold"))
    store.save_player(player)
    return result


@route("arena.enterfight")
def enter_fight(session: dict, msg: dict, req_id):
    player = _player(session)
    result = arena.enter_fight(player, (msg or {}).get("index"))
    store.save_player(player)
    return result


@route("arena.exitfight")
def exit_fight(session: dict, msg: dict, req_id):
    player = _player(session)
    result = arena.exit_fight(player, (msg or {}).get("index"),
                              (msg or {}).get("success"), (msg or {}).get("battleInfo"))
    store.save_player(player)
    return result
