"""启动入口： python run.py"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from gamesrv import apps, config, logx
from gamesrv.httpd import HttpService


def main(argv=None):
    parser = argparse.ArgumentParser(description="战场双马尾(oppai) 模拟服务器")
    parser.add_argument("--verbose", "-v", action="store_true", help="打印调试日志")
    args = parser.parse_args(argv)

    logx.setup(logging.DEBUG if args.verbose else logging.INFO)
    log = logx.get("main")

    from gamesrv import handlers

    handlers.load_all()

    log.info("=" * 72)
    log.info(" 战场双马尾 / Oppai  v%s  模拟服务器", config.APP_VERSION)
    log.info(" 对外地址: %s", config.PUBLIC_HOST)
    log.info(" cdn=%-6d gate=%-6d login=%-6d game=%-6d", config.CDN_PORT, config.GATE_PORT, config.LOGIN_PORT, config.GAME_PORT)
    log.info("=" * 72)

    services = [
        apps.build_cdn(HttpService("cdn", config.CDN_PORT)),
        apps.build_gate(HttpService("gate", config.GATE_PORT)),
        apps.build_login(HttpService("login", config.LOGIN_PORT)),
        apps.build_game(HttpService("game", config.GAME_PORT)),
    ]
    for svc in services:
        svc.start()

    stop = False

    def _sig(*_a):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _sig)
    try:
        signal.signal(signal.SIGTERM, _sig)
    except (AttributeError, ValueError):
        pass

    log.info("服务已就绪，等待客户端连接 ...")
    while not stop:
        time.sleep(0.5)
    log.info("正在停止 ...")
    for svc in services:
        svc.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
