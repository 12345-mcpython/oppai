"""把 hook.js 拆成 patch.js（必需适配）+ probe.js（探针/诊断）。

hook.js 原本是单个 IIFE（第 11-1783 行，前面 10 行是注释）。
直接切片段会缺外层包装和 emit，所以要分别重新包一遍。

patch.js 段（必需）：
    1149-1296  ccui.helper.seekNodeByName/ByTag + ActionTimeline 回调补参数
    1337-1538  ccui.WebView polyfill + VideoPlayer 让路原生
    1594-1711  LaunchGuideLayer 守卫 / 看门狗 / 视频层清理
probe.js 段（诊断）：
    12-1148    日志/心跳/序列化/挂钩/登录绕过/REPL
    1297-1336  UpdateScene 调用跟踪
    1539-1593  LaunchGuideLayer 调用跟踪
    1712-1782  启动引导
"""

from __future__ import annotations

import io
import os

SRC = r"E:\code\python\game_server\client\hook.js"
OUT = r"E:\code\python\game_server\client"
TOTAL_END = 1782          # IIFE 体结束行（1783 是 })();）

lines = io.open(SRC, encoding="utf-8").read().split("\n")


def seg(a: int, b: int) -> str:
    return "\n".join(lines[a - 1:b])


PATCH = f'''// ===========================================================================
// patch.js —— 《战场双马尾》私服客户端适配层（必需）
//
// 这里放的是「少了游戏就跑不对」的补丁，根因都是引擎与游戏的约定不一致：
//
//   1. ccui.helper.seekNodeByName / seekNodeByTag
//      原版 libcocos2djs.so 有这两个绑定，vanilla cocos2d-js v3.6 没有。
//      缺了 UpdateScene._init() 抛 "seekNodeByName is not a function"，
//      热更新界面建不出来 -> 一直黑屏。
//
//   2. ActionTimeline 回调补动画名
//      v3.6 的自动绑定是 func->invoke(0, ...)，而游戏回调写的是
//      function (eventName) {{ if (eventName === "default") this._init(); }}
//      少了这个参数，UpdateScene 永远走不到 _init()（表现为黑屏卡死）。
//
//   3. ccui.WebView
//      原版有 experimental_webView_WebView 绑定，v3.6 仓库没有。
//      公告层靠它显示私服公告（内容由服务端 var/notice.html 提供）。
//
//   4. ccui.VideoPlayer 让路
//      引擎侧已提供原生绑定（register_all_oppai_videoplayer），
//      这里只在原生不存在时才装占位实现。
//
//   5. LaunchGuideLayer 战斗开场引导
//      视频播完后回调会因为 _videoPlayer 已为 null 而抛异常；而且 Android 侧
//      残留的 VideoView 会一直盖在画面上（表现为画面停在视频最后一帧）。
//
// 探针/诊断部分在 probe.js —— release 可以不打包那个文件。
// 两个文件互相独立，这个文件不依赖 probe.js 的任何东西。
// ===========================================================================

(function () {{
    "use strict";

    var TAG = "OPPAIPATCH";

    // 自带极简日志（不依赖 probe.js 的 emit）
    function emit(line) {{
        try {{ console.log(TAG + "|" + line); }} catch (e) {{ }}
    }}
    function vlog(line) {{
        if (typeof __OPPAI_VERBOSE__ !== "undefined" && __OPPAI_VERBOSE__) {{
            emit(line);
        }}
    }}

{seg(1149, 1296)}

{seg(1337, 1538)}

{seg(1594, 1689)}

{seg(1690, 1741)}
}})();
'''

PROBE = f'''// ===========================================================================
// probe.js —— 研究用探针（release 可以不打包）
//
// 内容：日志上报 / 心跳 / 字段探测 / 加密与协议挂钩 / 登录绕过 /
//       REPL 控制通道 / 若干调用跟踪。
//
// 与 patch.js 相互独立：patch.js 负责让游戏跑对，本文件负责看清它在跑什么。
// ===========================================================================

(function () {{

{seg(12, 1148)}

{seg(1297, 1336)}

{seg(1539, 1593)}

{seg(1743, TOTAL_END)}
}})();
'''

io.open(os.path.join(OUT, "patch.js"), "w", encoding="utf-8", newline="\n").write(PATCH)
io.open(os.path.join(OUT, "probe.js"), "w", encoding="utf-8", newline="\n").write(PROBE)

print("patch.js  %d 行" % PATCH.count("\n"))
print("probe.js  %d 行" % PROBE.count("\n"))
