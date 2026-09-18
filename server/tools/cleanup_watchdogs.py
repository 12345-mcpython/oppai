"""清理 patch.js：拆掉因为误判而加的两个看门狗。

根因已在引擎层根治（setLastFrameCallFunc 传动画名），所以：

  ✗ MOVIE-WD  引导层视频看门狗（视频停播就替引擎触发 COMPLETED）
               当初以为 COMPLETED 事件不送达，其实单独测是正常的；
               真正的问题是 _videoPlayer 已被置 null、JS 够不到，
               那个由 LGL-GUARD 的清理逻辑解决，看门狗是多余的。

  ✗ BATTLE-WD 战斗收尾看门狗（_nextCb 挂 20 秒就替它调一次）
               真正原因是 ActionTimeline 最后一帧回调拿不到动画名，
               导致 began3 之后进不了 loop3。引擎修好后序列自己会走完。

保留：
  ✓ LGL-GUARD 的视频层清理（stop + setVisible(false)）
    —— Android 侧残留的 VideoView 会盖住画面，这个跟回调参数无关，仍然需要
  ✓ ccui.helper.seekNodeByName / ByTag polyfill
  ✓ ccui.WebView / ccui.VideoPlayer 让路
"""

from __future__ import annotations

import io
import re

P = r"E:\code\python\game_server\client\patch.js"
s = io.open(P, encoding="utf-8").read()
n0 = len(s.split("\n"))

# ---------------------------------------------------------- 去掉 MOVIE-WD 整块
start = s.find("    // ------------------------------------------------------------------\n"
               "    // 引导层视频看门狗")
if start < 0:
    start = s.find("    // 引导层视频看门狗")
    start = s.rfind("    // ----", 0, start) if start > 0 else -1
end = s.find("    })();\n", start)
if start >= 0 and end > start:
    end += len("    })();\n")
    # 连带把后面的空行一起吃掉
    while end < len(s) and s[end] == "\n":
        end += 1
    s = s[:start] + s[end:]
    print("已删除 MOVIE-WD 块")
else:
    print("!! 没定位到 MOVIE-WD 块")

# ------------------------------------------------------- 去掉 BATTLE-WD 整块
start = s.find("    // ------------------------------------------------------------------\n"
               "    // 战斗结束推进看门狗")
if start >= 0:
    end = s.find("    })();\n", start)
    if end > start:
        end += len("    })();\n")
        while end < len(s) and s[end] == "\n":
            end += 1
        s = s[:start] + s[end:]
        print("已删除 BATTLE-WD 块")
    else:
        print("!! BATTLE-WD 块结尾没找到")
else:
    print("!! 没定位到 BATTLE-WD 块")

# --------------------------------- 去掉只为 MOVIE-WD 服务的 __oppaiGuideRef 跟踪
s = re.sub(
    r"\n\s*// 记录当前引导层实例（ctor 时挂上）\n"
    r"\s*var origCtor = LaunchGuideLayer\.prototype\.ctor;\n"
    r"\s*if \(typeof origCtor === \"function\"\) \{\n"
    r"\s*LaunchGuideLayer\.prototype\.ctor = function \(\) \{\n"
    r"\s*var r = origCtor\.apply\(this, arguments\);\n"
    r"\s*window\.__oppaiGuideRef = this;\n"
    r"\s*return r;\n"
    r"\s*\};\n"
    r"\s*\}\n", "\n", s)

io.open(P, "w", encoding="utf-8", newline="\n").write(s)
print("patch.js: %d 行 -> %d 行" % (n0, len(s.split("\n"))))

# 残留检查
left = [k for k in ("MOVIE-WD", "BATTLE-WD", "oppaiGuideRef", "oppaiBEwd", "oppaiMovieWd")
        if k in s]
print("残留标记: %s" % (left or "无"))
