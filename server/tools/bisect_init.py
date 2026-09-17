r"""逐个构造 initUserData 里的数据模块，找出会把 JS 主线程卡死的那个。

    python tools\bisect_init.py

原理：每次只 new 一个模块，然后立刻发一个 `1+1` 探测；
如果探测超时，说明上一个模块的构造函数把主线程跑死了。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ADB = r"D:\Android\android-sdk\platform-tools\adb.exe"

# initUserData 里的顺序（从反汇编读出来的）：(类名, data 里的 key, dataManager 上的属性名)
MODULES = [
    ("Player", "player", "player"),
    ("Instance", "instance", "instance"),
    ("Bag", "item", "bag"),
    ("CharCenter", "char", "character"),
    ("Gacha", "gacha", "gacha"),
    ("Mailbox", "mail", "mailbox"),
    ("ActQuestCenter", "actquest", "actQuestCenter"),
    ("QuestCenter", "quest", "questCenter"),
    ("FavorCenter", "favor", "favorCenter"),
    ("FavorEventCenter", "favorevent", "favorEventCenter"),
    ("Friend", "friend", "friend"),
    ("ExchangeCenter", "exchange", "exchangeCenter"),
    ("TalentCenter", "talents", "talentCenter"),
    ("SignCenter", "sign", "signCenter"),
    ("Shop", "shop", "shop"),
    ("ArenaCenter", "arena", "arenaCenter"),
    ("Rank", "rank", "rank"),
    ("Score", "score", "score"),
    ("Society", "society", "society"),
    ("SocietyClg", "societyclg", "societyClg"),
    ("BossCenter", "boss", "bossCenter"),
    ("Chat", "chat", "chat"),
    ("Detect", "detect", "detect"),
    ("Medal", "medal", "medal"),
    ("EquipmentCenter", "equipment", "equipmentCenter"),
    ("Share", "share", "share"),
    ("SubareaAchievement", "subareaachievement", "subareaachievement"),
    ("ConsumeActivity", "consumeactivity", "consumeActivity"),
    ("Diary", "diary", "diary"),
    ("FriendSupport", "friendsupport", "friendSupport"),
    ("NoviceQuestCenter", "novicequest", "noviceQuestCenter"),
]


def adb(*args):
    return subprocess.run([ADB, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")


def ev(base, code, timeout=6.0):
    body = json.dumps({"code": code, "timeout": timeout}).encode()
    req = urllib.request.Request(
        base + "/control/eval", data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout + 6) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "value": f"<TIMEOUT/ERR {exc}>"}


def alive(base):
    return ev(base, "1+1", timeout=5).get("ok", False)


def main():
    from gamesrv import config

    base = f"http://127.0.0.1:{config.CDN_PORT}"

    print(">> 先取一份 createplayer 数据 ...")
    print(ev(base, "(function(){server.request('agent.createplayer',{},function(e,r){window.__d=r.data;"
                  "__oppaiHook__.log('D ready');});return 'ok';})()"))
    time.sleep(2)
    if not alive(base):
        print("!! 客户端没响应，先重启一下再跑")
        return

    for cls, key, prop in MODULES:
        code = (f"(function(){{try{{var o=new {cls}(window.__d.{key});"
                f"__oppaiHook__.log('CTOR {cls} ok');return 'ok';}}"
                f"catch(e){{return 'ERR '+e;}}}})()")
        r = ev(base, code, timeout=8)
        time.sleep(0.3)
        ok = alive(base)
        print(f"  {cls:20s} <- data.{key:20s} ret={str(r.get('value'))[:60]:60s} alive={ok}")
        if not ok:
            print(f"\n>>> 卡死在这个模块：{cls} (data.{key})")
            return

    print("\n=== 第二阶段：initUserData 里的 init*() 调用 ===")
    POST = [
        ("syncTime(data.agent)", "syncTime(window.__d.agent, 0, 0)"),
        ("player.setCharacter(character)", "dataManager.player.setCharacter(dataManager.character)"),
        ("player.initTeams()", "dataManager.player.initTeams()"),
        ("player.initAsst()", "dataManager.player.initAsst()"),
        ("guideManager.init()", "guideManager.init()"),
        ("uiLayoutManager.init()", "uiLayoutManager.init()"),
        ("player.initModuleState()", "dataManager.player.initModuleState()"),
        ("player.initXgNotifications()", "dataManager.player.initXgNotifications()"),
        ("chat.init(...)", "dataManager.chat.init(window.__d.agent.msgCenterAddr, window.__d.agent.worldChatLimit)"),
        ("share.getWechatAppId()", "dataManager.share.getWechatAppId()"),
    ]
    # 先把模块对象按 initUserData 的顺序建好，第二阶段才有东西可调
    setup = ("(function(){var d=window.__d;"
             + "".join(f"try{{dataManager.{prop} = new {cls}(d.{key});}}catch(e){{}}"
                       for cls, key, prop in MODULES)
             + "return 'setup';})()")
    print("  setup:", str(ev(base, setup).get("value"))[:60])

    for label, code in POST:
        r = ev(base, f"(function(){{try{{{code};return 'ok';}}catch(e){{return 'ERR '+e;}}}})()", timeout=8)
        time.sleep(0.3)
        ok = alive(base)
        print(f"  {label:34s} ret={str(r.get('value'))[:44]:44s} alive={ok}")
        if not ok:
            print(f"\n>>> 卡死在这一步：{label}")
            return

    print("\n全部通过 —— 说明是组合起来才卡，需要再单独看 syncTime / 循环引用")


if __name__ == "__main__":
    main()
