"""玩家相关业务路由。

字段和成功码都是从客户端反汇编里读出来的：
`Player.updateGuideMark` 的回调判断 `data.code == 200`，
不是 200 就永远不更新本地 `_guideMark`，于是无限重发同一个请求。
"""

from __future__ import annotations

from .. import config, items, logx, store
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.player")

# 失败码。客户端 `TalentCenter.requestXxxCb` 只在 `code == 200` 时才改本地状态，
# 其余情况只打一条 `[error] ...` 日志 —— 所以「材料不够」必须回非 200，
# 否则客户端自己把 lv +1 了、服务端没扣，两边就分叉。
FAIL = 1


def _account(session: dict) -> str:
    return (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT


@route("player.getdata")
def get_data(session: dict, msg: dict, req_id):
    return {"code": CODE_OK, "msg": "", "data": {"player": store.get_or_create_player(_account(session))}}


@route("player.updateasst")
def update_asst(session: dict, msg: dict, req_id):
    """设置**助战角色**（宿舍右下角「设置助战」那个按钮）。

    反汇编 `Player.updateAsst(favor, cb)`：

        if (!favor.isAsstEnabled)               { cb({msg: table_dictionary[109]}); return; }
        if (this._asstKey === favor.charKey)    { cb({msg: table_dictionary[110]}); return; }
        this._asst = favor;
        this._asstKey = favor.charKey;          // ← **本地先改**
        if (dataManager.isLogin)
            server.request("player.updateasst", {charKey: this._asstKey}, cb, true);
        // 回调里：失败弹 table_dictionary[111]，成功调 favorCenter.resetIsNeedAsstEff()

    请求体就是 `{charKey}`。`isAsstEnabled` 是客户端那边
    `Favor._isAsstEnabled`（受 `table_favor_common[好感度等级].enable_set_asst` 控制，
    好感度 1 级就开了），服务端不校验 —— 校验在客户端，且它已经本地改过了。

    回包**必须带 `player`** —— `asstKey` 是玩家数据的一部分，重登时
    `Player._initData` 从登录包的 `player.asstKey` 恢复；只回 code 的话
    这次会话看着好了，一重登就回到旧值。
    """
    account = _account(session)
    char_key = str((msg or {}).get("charKey") or "")
    if not char_key:
        return {"code": FAIL, "msg": "缺少 charKey", "data": {}}
    player = store.get_or_create_player(account)
    player["asstKey"] = char_key
    player["asst"] = {"charKey": char_key}
    store.save_player(player)
    log.info("player.updateasst account=%s 助战 -> %s", account, char_key)
    return {"code": CODE_OK, "msg": "", "data": {"player": player}}


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


@route("player.setmoduleopenmark")
def set_module_open_mark(session: dict, msg: dict, req_id):
    """功能模块开启标记。

    请求体是一个**数组**：`[1, 2, 3, 4, 5, 8, 9, 10, 12, ...]`（客户端每次登录打一次）。

    反汇编 `src/data/player.jsc`：`server.request('player.setmoduleopenmark', ...)`
    之后紧接着就是 `Player<.initTeams` —— 调用点**没读响应**，是发完不管的。

    所以这里两件事：
      1. 把客户端报上来的这批编号记到存档里（`moduleOpenMark`，**存成 `{markIndex: 1}` map**
         —— 登录块要把它回给客户端，而客户端读的是 `moduleOpenMark[module.markIndex]`，
         markIndex 从 1 起，回数组会整体错一位）；
      2. 回一个**带 `player` 块**的响应 —— `player` 在客户端的 responseConfig 里，
         带上它客户端会顺手 `Player.updateByServer(player)`，
         把 `moduleState` 这种权威状态再对齐一次。
         （`moduleState` 缺失会让主界面 `_initModuleButtons` 死在
          `TypeError: modules is undefined`，见 store.py 里的注释。）

    ⚠️ **不要**拿这批编号去改 `moduleState`：`moduleState` 的 key 是
    `store.MODULE_KEYS`（100001…100032），而这里收到的是 1…32，
    两套编号不是一回事，硬套会把 32 个模块的开启状态全抹掉。
    """
    account = _account(session)
    marks = msg if isinstance(msg, list) else (msg or {}).get("marks") or []
    player = store.get_or_create_player(account)
    # ⚠️ 存成 **map**（`{markIndex: 1}`），不是客户端报上来的那个数组 ——
    # 登录块要把这份数据回给客户端，而客户端读的是
    # `moduleOpenMark[module.markIndex]`（markIndex 从 1 起）；回数组会整体错一位。
    stored = player.get("moduleOpenMark")
    if not isinstance(stored, dict):
        stored = {}
    for mi in marks:
        try:
            stored[str(int(mi))] = 1
        except (TypeError, ValueError):
            continue
    player["moduleOpenMark"] = stored
    store.save_player(player)
    log.info("player.setmoduleopenmark account=%s 收到 %d 个编号（存档里现在 %d 个）",
             account, len(marks), len(stored))
    return {"code": CODE_OK, "msg": "", "data": {"player": player}}


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

    ⚠️⚠️ **队伍 id 是 0 起的下标，不是我们 `new_team()` 里那个 1 起的 `id`。**
    反汇编 `Player.initTeams`：

        for (var i in this._teams) { ... new Team(this._teams[i], this._character, i); }

    `Team.ctor(team, chars, id)` 的第三个参数**就是** `for..in` 出来的 key（"0".."4"），
    所以客户端 `team.id` = 服务端 `teams` 数组的下标。实测（运行中的客户端）：

        player._teams = [{id:"0",index:0}, {id:"1",index:1}, ..., {id:"4",index:4}]

    而 `store.new_team(index)` 早期给的是 `id = index + 1`（1 起）—— 两边对不上，
    于是**编成保存整单被跳过**（日志里 `updateteams 未知队伍 id=0`），
    表现就是「编好的队伍一直消失」（重登后又是空的）。
    （更早还"成功"过一次：客户端发 id="1" 时恰好撞上我们 id=1 那行 —— 但那是 index 0 的队伍
    写进了 id=1 的行，等于**写错队伍**。）

    所以这里**按 index 找**，并且 `store.normalize_team_ids()` 会把老存档里的 id 拉回 index。

    ⚠️ 这个路由不只是「存档」：主线的「队伍中只上阵 N 个军士获得胜利」要靠
    `player.teams[curTeamIdx].soldierKeys` 的长度判定上阵人数；
    不存的话服务端永远以为队伍是空的（`new_team()` 给的就是空队伍），
    那条任务就永远做不完。
    """
    account = _account(session)
    player = store.get_or_create_player(account)
    store.normalize_team_ids(player)
    teams = player.get("teams") or []

    changed = 0
    for item in (msg or {}).get("teams") or []:
        team_id = str((item or {}).get("id"))
        patch = (item or {}).get("team") or {}
        target = None
        for t in teams:
            # 先按 index（客户端约定），再按 id（兜底，比如手工构造的请求）
            if str(t.get("index")) == team_id or str(t.get("id")) == team_id:
                target = t
                break
        if target is None:
            log.warning("updateteams 未知队伍 id=%s（现有 index/id：%s）", team_id,
                        [(t.get("index"), t.get("id")) for t in teams])
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
             [(t.get("index"), len(t.get("soldierKeys") or [])) for t in teams])
    return {"code": CODE_OK, "msg": "", "data": {"teams": teams}}


# ---------------------------------------------------------------------------
# 天赋（培养）
#
# 两条路由的**请求体形状都是反汇编出来的**（`src/data/talentcenter.jsc`）：
#
#   requestSelectTalent(data):
#       var sendData = {}; sendData[data.type] = data.talentKey;
#       server.request('player.selecttalent', sendData, cb)
#       -> 收到的是 {"101": "1002"}（**key 是 type，不是固定字段名**）
#
#   requestUpgradeTalent(data):
#       server.request('player.upgradetalent', {type: data.type}, cb)
#       -> 收到的是 {"type": "101"}
#
# 两个回调都只看 `code == 200`，成功后就**纯本地改状态**：
#       selectTalent:  this._talentTypes[type].curTalentKey = talentKey; this._initTalents();
#       upgradeTalent: this._talentTypes[type].lv += 1;                   this._initTalents();
# 响应体不带数据（`talents` 也不在客户端的 responseConfig 里），回 `{}` 就够。
# 所以服务端的职责是**校验 + 落盘 + 扣材料**。
# ---------------------------------------------------------------------------
def _talent_max_lv(talent_type: str) -> int:
    """`table_talent_type[type].max_lv`（客户端那边是个全局常量 TALENT_MAX_LV）。"""
    row = items.table("table_talent_type").get(str(talent_type)) or {}
    try:
        return int(row.get("max_lv") or 0) or 10
    except (TypeError, ValueError):
        return 10


@route("player.selecttalent")
def select_talent(session: dict, msg: dict, req_id):
    """选天赋。请求体是**动态 key** 的对象：`{"101": "1002"}`。

    校验规则照客户端的 `_checkSelectTalentData`：
    `table_talent_master[talentKey].type === type`（这个天赋必须属于这个类型）。

    服务端必须落盘。客户端只改了内存里的 `_talentTypes`，不存的话重登就回到
    默认天赋 —— 和「军士升完重登又变回去」是同一类坑。
    """
    account = _account(session)
    player = store.get_or_create_player(account)
    talents = store.player_talents(player)

    picked = []
    for key, value in (msg or {}).items():
        talent_type, talent_key = str(key), str(value)
        if talent_type not in store.TALENT_TYPES:
            log.warning("player.selecttalent 未知 type=%r（请求体 %r）", key, msg)
            continue
        row = items.table("table_talent_master").get(talent_key) or {}
        # 和客户端 _checkSelectTalentData 同一条规则：不满足它自己就不会发请求
        if str(row.get("type") or "") != talent_type:
            log.warning("player.selecttalent 天赋 %s 不属于 type %s", talent_key, talent_type)
            continue
        talents[talent_type]["curTalentKey"] = talent_key
        picked.append((talent_type, talent_key))

    if not picked:
        return {"code": FAIL, "msg": "bad talent", "data": {}}

    store.save_player(player)
    log.info("player.selecttalent account=%s %s", account, picked)
    return {"code": CODE_OK, "msg": "", "data": {}}


@route("player.upgradetalent")
def upgrade_talent(session: dict, msg: dict, req_id):
    """升级天赋。请求体 `{"type": "101"}`。

    消耗取自 `table_talent_upgrade["<type>#<lv>"]` 的 `{money, item, count}`
    （服务端抽成 `data/table_talent_upgrade.json`）。实测 `money` 全是 0，
    材料就是 200040~200048 那九种。

    ⚠️ 材料不够时**必须回非 200**：客户端 `upgradeTalent()` 是「code==200 才
    `lv += 1`」。要是这里回 200 却没扣材料，客户端会自己 +1，两边就分叉了
    （下次登录 lv 又掉回去）。
    """
    account = _account(session)
    player = store.get_or_create_player(account)
    talents = store.player_talents(player)
    talent_type = str((msg or {}).get("type") or "")

    row = talents.get(talent_type)
    if not isinstance(row, dict):
        log.warning("player.upgradetalent 未知 type=%r", talent_type)
        return {"code": FAIL, "msg": "bad type", "data": {}}

    lv = int(row.get("lv") or 0)
    max_lv = _talent_max_lv(talent_type)
    if lv >= max_lv:
        log.info("player.upgradetalent type=%s 已经满级（%s）", talent_type, max_lv)
        return {"code": FAIL, "msg": "max lv", "data": {}}

    cost = items.table("table_talent_upgrade").get(f"{talent_type}#{lv}")
    if not isinstance(cost, dict):
        log.warning("player.upgradetalent 表里没有 %s#%s 这一行（表没抽？）", talent_type, lv)
        return {"code": FAIL, "msg": "no cost row", "data": {}}

    money = int(cost.get("money") or 0)
    material_key = str(cost.get("item") or "")
    material_count = int(cost.get("count") or 0)
    need = []
    if money:
        need.append((store.ITEM_MONEY, money))
    if material_key and material_count:
        need.append((material_key, material_count))

    # 先整体判断够不够再扣 —— 否则扣了一半才发现不够，前面的材料就白没了
    if need and not items.can_afford(player, need):
        log.info("player.upgradetalent 材料不够 type=%s lv=%s 需要=%s 现有=%s",
                 talent_type, lv, need,
                 {k: items.count_of(player, k) for k, _ in need})
        return {"code": FAIL, "msg": "not enough", "data": {}}

    for key, count in need:
        items.sub_item(player, key, count)

    row["lv"] = lv + 1
    store.save_player(player)
    log.info("player.upgradetalent account=%s type=%s lv %s -> %s（扣 %s）",
             account, talent_type, lv, row["lv"], need or "无")
    return {"code": CODE_OK, "msg": "", "data": {}}
