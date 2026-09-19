"""mail.* —— 邮箱。

客户端调用点只有一个文件：`src/data/mailbox.jsc`，四条 route：

    mail.getnewmails    Mailbox.requestNewMails     拉新邮件
    mail.readmail       Mailbox.requesetReadMail    {id}        标记已读
    mail.receivemails   Mailbox.requestCollectMail  {ids: []}   领取附件
    mail.deletemail     Mailbox.requestDeleteMail   {id}        删除

形状是拿 `script/jsc_funcs.py` 按函数切开原子表读出来的：

    Mailbox<.ctor            data k _lastRequestNewMailsTime _mails mails _updateTime updateTime _remindCount
        -> 登录块的三件套：`mails` / `updateTime` / `remindCount`

    Mailbox<._addNewMail     newMail mail rewards k cc clone deleteFlag state readFlag collectFlag
                             createTimeSec getTimeByDateStr createTime annexFlag isNew rewards
                             rewardManager getRewardsWithString items REWARD_TYPE ITEM type key
                             count chars SOLDIER _mails id
        -> 一封邮件的字段：id / rewards / deleteFlag / state / readFlag / collectFlag /
           createTimeSec / annexFlag / isNew（failureTimeSec 是 getMails 里判过期的）

    Mailbox<.requestCollectMailCb   err getData cb ids data k cc log ... error code data invalid
                                    _mails collectFlag analyticsManager ... rewards
        -> 领附件时回包里要带 `invalid`（哪些 id 没领成）和 `rewards`

    Mailbox<.requestNewMails/<   err getData requestNewMailsCb
    Mailbox<.requestDeleteMail   id dataManager isLogin server request mail.deletemail id _mails deleteFlag
    Mailbox<.requesetReadMail    id dataManager isLogin server request mail.readmail id _mails readFlag

后三条客户端都是**本地先改标记再发请求**（乐观更新），所以服务端只要
`code == 200` 并且回来的 `mail` 块和本地一致，就不会有状态回跳。

`mail` 在客户端的 responseConfig 里，所以响应里带上 `{"mail": {...}}`
客户端会自动 `Mailbox.updateByServer(...)`，本地状态以服务端为准。

## 现状：邮箱是空的（这是对的）

私服目前**不发任何邮件**，所以 `mails` 恒为空数组，四条 route 都只是
「把权威状态回给客户端」。之前它们全落进「未实现的 route」，拿到空 data，
客户端 `updateByServer(undefined)` 会把 `_mails` 弄成 undefined。

要真发邮件（比如送新手奖励），就得给 `mails` 里塞邮件，而附件是
`rewardManager.getRewardsWithString(rewards)` 解析的**奖励字符串** ——
服务端目前**没有这个格式的实现**（`quests.py` / `char.py` 都是直接回
`"rewards": []` 绕过去的）。所以「带附件的邮件」是另一个独立任务。
"""

from __future__ import annotations

import time

from .. import config, logx, store
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.mail")


def _account(session: dict) -> str:
    return (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT


def _mail_block(player: dict) -> dict:
    """客户端 `Mailbox.updateByServer` 认的就是这个形状（和登录块一致）。"""
    mails = [m for m in (player.get("mails") or []) if not m.get("deleteFlag")]
    remind = 0
    for m in mails:
        if m.get("isNew"):
            remind += 1
    return {
        "mails": mails,
        "updateTime": store.now_ms(),
        "remindCount": remind,
    }


def _ok(player: dict, **extra) -> dict:
    data = {"mail": _mail_block(player)}
    data.update(extra)
    return {"code": CODE_OK, "msg": "", "data": data}


def _find(player: dict, mail_id):
    for m in player.get("mails") or []:
        if str(m.get("id")) == str(mail_id):
            return m
    return None


@route("mail.getnewmails")
def get_new_mails(session: dict, msg: dict, req_id):
    """拉新邮件。私服不发信，所以就是把手里的邮箱回一遍。"""
    player = store.get_or_create_player(_account(session))
    block = _mail_block(player)
    log.info("mail.getnewmails account=%s 回 %d 封", _account(session), len(block["mails"]))
    return {"code": CODE_OK, "msg": "", "data": {"mail": block}}


@route("mail.readmail")
def read_mail(session: dict, msg: dict, req_id):
    """标记已读。请求体 {"id": <mailId>}。

    客户端发之前已经本地把 `readFlag` 置 1 了，服务端跟着改就行；
    顺便把 `isNew` 清掉，红点（remindCount）才会消。
    """
    player = store.get_or_create_player(_account(session))
    mail_id = (msg or {}).get("id")
    m = _find(player, mail_id)
    if m is not None:
        m["readFlag"] = 1
        m["isNew"] = 0
        store.save_player(player)
    else:
        log.info("mail.readmail 找不到 id=%s（忽略）", mail_id)
    return _ok(player)


@route("mail.receivemails")
def receive_mails(session: dict, msg: dict, req_id):
    """领取附件。请求体 {"ids": [...]}。

    回包里带 `invalid`（没领成的 id）和 `rewards`。

    ⚠️ 附件奖励**这里发不出去**：邮件的 `rewards` 是客户端
    `rewardManager.getRewardsWithString()` 解析的奖励字符串，而服务端还没有
    这套格式的实现。所以现在只把 `collectFlag` 标上，`rewards` 恒空 ——
    **不要**在这里假装发了奖励。等奖励格式做完再接。
    """
    player = store.get_or_create_player(_account(session))
    ids = (msg or {}).get("ids") or []
    invalid, done = [], 0
    for i in ids:
        m = _find(player, i)
        if m is None or m.get("deleteFlag"):
            invalid.append(i)
            continue
        if not m.get("annexFlag"):
            invalid.append(i)          # 没有附件，客户端也不该把它塞进 ids
            continue
        m["collectFlag"] = 1
        done += 1
    if done:
        store.save_player(player)
    log.info("mail.receivemails account=%s 领了 %d 封，%d 个无效",
             _account(session), done, len(invalid))
    return _ok(player, invalid=invalid, rewards=[])


@route("mail.deletemail")
def delete_mail(session: dict, msg: dict, req_id):
    """删除。请求体 {"id": <mailId>}。软删（置 deleteFlag），列表里过滤掉。"""
    player = store.get_or_create_player(_account(session))
    mail_id = (msg or {}).get("id")
    m = _find(player, mail_id)
    if m is not None:
        m["deleteFlag"] = 1
        store.save_player(player)
        log.info("mail.deletemail account=%s id=%s", _account(session), mail_id)
    else:
        log.info("mail.deletemail 找不到 id=%s（忽略）", mail_id)
    return _ok(player)
