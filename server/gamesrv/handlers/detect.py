"""detect.* —— 任务派遣（主界面「派遣」按钮，见 `gamesrv/detect.py`）。

六条路由，形状和错误码都照客户端反汇编抄：

    detect.getdetectlist     {id}                     → {code, detectList:[{index,data}]}
    detect.checkdetectmain   {idlist}                 → {code, detectMainList:[{id,isOpen}]}
    detect.selectsolders     {detectkey, solders}     → {code}
    detect.godetect          {detectkey}              → {code, detect:{…}}
    detect.godetectcomplete  {detectkey}              → {code, complateDetectCount, detect, rewards, items}
    detect.subtime           {detectkey, timesed}     → {code, speedInfo, detect:{…}}

⚠️ 失败也回 **HTTP 200**：客户端看的是 `data.code`（`DETECT_ERROR_CODE`），
   回非 200 反而会让它走网络失败分支。
"""

from __future__ import annotations

from .. import config, detect, logx, quests, store
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.detect")


def _player(session: dict) -> dict:
    account = (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT
    return store.get_or_create_player(account)


@route("detect.getdetectlist")
def get_detect_list(session: dict, msg: dict, req_id):
    player = _player(session)
    return detect.list_for(player, (msg or {}).get("id"))


@route("detect.checkdetectmain")
def check_detect_main(session: dict, msg: dict, req_id):
    player = _player(session)
    return detect.check_main(player, (msg or {}).get("idlist"))


@route("detect.selectsolders")
def select_solders(session: dict, msg: dict, req_id):
    player = _player(session)
    result = detect.select_solders(player, (msg or {}).get("detectkey"),
                                   (msg or {}).get("solders"))
    store.save_player(player)
    return result


@route("detect.godetect")
def go_detect(session: dict, msg: dict, req_id):
    player = _player(session)
    result = detect.go(player, (msg or {}).get("detectkey"))
    store.save_player(player)
    return result


@route("detect.godetectcomplete")
def go_detect_complete(session: dict, msg: dict, req_id):
    player = _player(session)
    result = detect.complete(player, (msg or {}).get("detectkey"))
    if result.get("code") == 200:
        quests.on_detect_complete(player)      # 日常 16202「完成任务派遣 N 次」
    store.save_player(player)
    return result


@route("detect.subtime")
def sub_time(session: dict, msg: dict, req_id):
    player = _player(session)
    result = detect.sub_time(player, (msg or {}).get("detectkey"), (msg or {}).get("timesed"))
    store.save_player(player)
    return result
