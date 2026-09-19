"""项目路径自举 —— 每个实用脚本开头都会 import 一下这个。

项目重排成了这样（根目录是 `E:\\code\\zcsmw`）：

    zcsmw\\
      build.ps1        一键构建
      game\\            游戏包（apktool 解包目录 / 原版 APK）
      server\\          服务端（python 包 gamesrv + run.py）
      script\\          实用脚本（反汇编 / 反编译 / 抽表 / 打包 …）
      engine\\          引擎源码 + NDK（重编 libcocos2djs.so 用）
      out\\             构建产物（APK / .so / 截图）

脚本散在 `script/` 和 `script/sdk_strip/` 两层，直接
`os.path.dirname(os.path.dirname(__file__))` 算不出根目录，
所以统一在 `script/_paths.py` 放一个**哨兵**：脚本从自己往上找它，
找到就把 `script/` 和 `server/` 都塞进 `sys.path`。

于是脚本里可以照常写：

    from gamesrv import config      # server/ 在 sys.path 上
    import jsc_disasm as J          # script/ 在 sys.path 上
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))      # script/
ROOT = os.path.dirname(HERE)                           # E:\code\zcsmw

# 中文 Windows 的控制台默认是 GBK，脚本里一个 "✓" 就能把 print 打成
#   UnicodeEncodeError: 'gbk' codec can't encode character '\u2713'
# 而且是在最后一步汇总时才炸，看着像功能坏了。统一在这里把 stdout/stderr
# 掰成 UTF-8 —— 所有脚本都 import 这个模块，一处改全好。
# （build.ps1 里也设了 PYTHONIOENCODING，但手动跑单个脚本时不会经过它。）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

SERVER = os.path.join(ROOT, "server")
GAME = os.path.join(ROOT, "game")
ENGINE = os.path.join(ROOT, "engine")
OUT = os.path.join(ROOT, "out")
SCRIPT = HERE

for _p in (HERE, SERVER, os.path.join(ENGINE, "build")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)


def env_defaults() -> None:
    """把项目里的路径塞成环境变量默认值 —— 各个脚本都读这些。"""
    os.environ.setdefault("GS_ROOT", ROOT)
    os.environ.setdefault("GS_APK_DIR", GAME)
    os.environ.setdefault("GS_WORK_DIR", OUT)
    os.environ.setdefault("GS_ENGINE_DIR", ENGINE)
    os.environ.setdefault("GS_APKTOOL", os.path.join(HERE, "apktool.bat"))
