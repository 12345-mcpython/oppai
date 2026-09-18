"""玩家相关业务路由。

字段和成功码都是从客户端反汇编里读出来的：
`Player.updateGuideMark` 的回调判断 `data.code == 200`，
不是 200 就永远不更新本地 `_guideMark`，于是无限重发同一个请求。
"""

from __future__ import annotations

from .. import config, logx, store
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.player")


def _account(session: dict) -> str:
    return (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT


@route("player.getdata")
def get_data(session: dict, msg: dict, req_id):
    return {"code": CODE_OK, "msg": "", "data": {"player": store.get_or_create_player(_account(session))}}


@route("player.naming")
def naming(session: dict, msg: dict, req_id):
    """新号起名。

    反汇编 Player.naming：
        server.request('player.naming', {name: name}, function (err, data) {
            if (err) return;
            if (data.code == 200) { _this._setName(name); if (cb) cb(); gameEvent.onRoleCreate(); }
            else                  { if (cb) cb(PLAYER_ERR_DICT[data.code] || data.data); }
        });
    """
    account = _account(session)
    name = (msg or {}).get("name", "") or account
    log.info("player.naming account=%s name=%s", account, name)
    store.update_player(account, name=name)
    return {"code": CODE_OK, "msg": "", "data": {"name": name}}


@route("player.updateguidemark")
def update_guide_mark(session: dict, msg: dict, req_id):
    """引导进度。

    反汇编 Player.updateGuideMark：
        server.request('player.updateguidemark', {mark: mark}, function (err, data) {
            if (err) return;
            if (data.code == 200) { _this._setGuideMark(mark); if (cb) cb(); }
        }, true);
    只有回 200 客户端才会更新本地 mark，否则会一直重发。
    """
    account = _account(session)
    mark = (msg or {}).get("mark", 0)
    log.info("player.updateguidemark account=%s mark=%s", account, mark)
    store.update_player(account, guideMark=mark)
    return {"code": CODE_OK, "msg": "", "data": {"mark": mark}}


@route("player.updateteams")
def update_teams(session: dict, msg: dict, req_id):
    """编成保存。

    反汇编 Player.updateTeams：

        paramTeams.push({id: team.id, team: team.getReqUpdateParam()});
        server.request('player.updateteams', {teams: paramTeams}, function (err, data) {
            if (data.code == 200) { 每个改过的队伍 saveLocalStatusStr(); }
            else cc.log('save team failed');
        });

    其中 `getReqUpdateParam()` 返回 `{heroKey, mechaKey, soldierKeys}`，
    `soldierKeys` 实际是 `getSoldierIds()`（军士的 id）。

    ⚠️ 这个路由不只是「存档」：主线的「队伍中只上阵 N 个军士获得胜利」要靠
    `player.teams[curTeamIdx].soldierKeys` 的长度判定上阵人数；
    不存的话服务端永远以为队伍是空的（`new_team()` 给的就是空队伍），
    那条任务就永远做不完。
    """
    account = _account(session)
    player = store.get_or_create_player(account)
    teams = player.get("teams") or []
    by_id = {str(t.get("id")): t for t in teams}

    changed = 0
    for item in (msg or {}).get("teams") or []:
        team_id = str((item or {}).get("id"))
        patch = (item or {}).get("team") or {}
        target = by_id.get(team_id)
        if target is None:
            log.warning("updateteams 未知队伍 id=%s", team_id)
            continue
        if "heroKey" in patch:
            target["heroKey"] = patch["heroKey"]
        if "mechaKey" in patch:
            target["mechaKey"] = patch["mechaKey"]
        if "soldierKeys" in patch:
            target["soldierKeys"] = list(patch["soldierKeys"] or [])
            target["soldierCount"] = len(target["soldierKeys"])
        changed += 1

    store.save_player(player)
    log.info("player.updateteams account=%s 更新 %d 支队伍 %s", account, changed,
             [(t.get("id"), len(t.get("soldierKeys") or [])) for t in teams])
    return {"code": CODE_OK, "msg": "", "data": {"teams": teams}}
