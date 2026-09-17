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

# 模拟器能直接访问到的主机地址（客户端里所有 URL 都指向这里）
PUBLIC_HOST = os.environ.get("GS_PUBLIC_HOST", "10.110.29.230")
BIND_HOST = os.environ.get("GS_BIND_HOST", "0.0.0.0")

# 客户端资源/配置服务器（原 cdn.shuangmawei.net）
# 注意：打包时要把它原地替换进 urlconfig.jsc 的字符串里，
# 而 "cdn.shuangmawei.net" 是 19 字节，所以 <host>:<port> 也必须是 19 字节。
CDN_PORT = int(os.environ.get("GS_CDN_PORT", "18080"))
# 网关：服务器状态查询
GATE_PORT = int(os.environ.get("GS_GATE_PORT", "10001"))
# 登录：oauth + WebSocket 握手
# 注意：客户端 server.js 里 OAUTH_HOST 是硬编码的 "http://114.55.66.97:16840"
# （25 字节），打包时会原地替换成 <host>:<port>，所以这里必须是 4 位端口。
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
