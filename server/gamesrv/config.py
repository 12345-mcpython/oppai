"""全局配置。

所有地址/端口都可以用环境变量覆盖，方便在别的机器上跑。
"""

from __future__ import annotations

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VAR_DIR = os.path.join(BASE_DIR, "var")
LOG_DIR = os.path.join(VAR_DIR, "logs")
DATA_DIR = os.path.join(VAR_DIR, "data")
CAPTURE_DIR = os.path.join(VAR_DIR, "capture")

for _d in (VAR_DIR, LOG_DIR, DATA_DIR, CAPTURE_DIR):
    os.makedirs(_d, exist_ok=True)

# 客户端要连的主机地址，**同时**决定两件事：
#   1. 服务器列表 / `GAME_SERV_URL` 里对外宣告的地址（客户端正则校验必须是 IP:PORT）
#   2. 打包时写进 `patch.js` 的 URL-REWRITE 目标（`build_apk.py` 默认就读这里）
# 所以只有这一处要设，不用再两边对齐。
#
# 默认 127.0.0.1 = **adb reverse 工作流**：
#     adb reverse tcp:{18080,8080,10001,10003} tcp:{...}
#   手机/模拟器上的回环被转发到 PC，于是不需要局域网、不需要防火墙、不需要 root，
#   而且**和 PC 的 IP 无关** —— DHCP 换 IP 也不会让包作废。
#   插 USB 的真机走的就是这条路（见 REPRODUCE.md Step 4b）。
# 要局域网直连就覆盖它：$env:GS_PUBLIC_HOST='192.168.1.100'（打包也要同一个值，
# 但 `build_apk.py` 默认读这里，所以只设环境变量就够）。
PUBLIC_HOST = os.environ.get("GS_PUBLIC_HOST", "127.0.0.1")
BIND_HOST = os.environ.get("GS_BIND_HOST", "0.0.0.0")

# 客户端资源/配置服务器（原 cdn.shuangmawei.net）。
# ⚠️ 老路子（`--patch-jsc-urls`）下：打包时要把 `<host>:<port>` **原地等长**替换进
# urlconfig.jsc，而 "cdn.shuangmawei.net" 是 19 字节 → host 必须正好 13 个字符。
# 默认的运行时改写没有这个约束。
CDN_PORT = int(os.environ.get("GS_CDN_PORT", "18080"))
# 网关：服务器状态查询
GATE_PORT = int(os.environ.get("GS_GATE_PORT", "10001"))
# 登录：oauth + WebSocket 握手
# 老路子下 `http://114.55.66.97:16840` 是 25 字节 → 端口必须 4 位。
LOGIN_PORT = int(os.environ.get("GS_LOGIN_PORT", "8080"))
# 游戏主逻辑：HTTP POST 路由
GAME_PORT = int(os.environ.get("GS_GAME_PORT", "10003"))

# 客户端版本（决定 urlconfig.js 里的 URL_PATH）
CLIENT_URL_PATH = "android/v2.2.0"
APP_VERSION = "2.2.0"
PATCH_VERSION = "2.2.0"

# 游戏服务器对外地址，客户端会用正则校验必须是 IP:PORT
GAME_SERV_URL = f"{PUBLIC_HOST}:{GAME_PORT}"

# 服务器列表里默认那台服
DEFAULT_SERVER = {
    "id": 1,
    "name": "S1 测试服",
    "host": PUBLIC_HOST,
}

# 单机模拟器的默认账号
DEFAULT_ACCOUNT = "test"
DEFAULT_PASSWORD = "test"
# 登录响应里回给客户端的 userId
DEFAULT_USER_ID = 1
