"""业务路由注册表。

客户端 src/util/server.js 里有一张 responseConfig：
响应 data 里出现某个 key（player / quest / mail / gacha ...）时，
客户端会自动把它喂给对应模块的 updateByServer()。
所以服务端只要在响应里带上变化的模块即可。
"""

from __future__ import annotations

from .. import logx
from ..gameproto import CODE_OK

log = logx.get("routes")

# route -> handler(session, msg, req_id) -> dict
_HANDLERS: dict = {}


def route(name: str):
    def deco(fn):
        _HANDLERS[name] = fn
        return fn

    return deco


def dispatch(name: str, session: dict, msg: dict, req_id):
    fn = _HANDLERS.get(name)
    if fn is None:
        log.warning("未实现的 route: %s msg=%s", name, msg)
        return {"code": CODE_OK, "msg": "", "data": {}}, False
    return fn(session, msg, req_id), True


def registered() -> list:
    return sorted(_HANDLERS)


def load_all():
    """导入所有 handler 模块，触发 @route 注册。"""
    from . import agent  # noqa: F401
    from . import boss  # noqa: F401
    from . import char  # noqa: F401
    from . import detect  # noqa: F401
    from . import equipment  # noqa: F401
    from . import exchange  # noqa: F401
    from . import favor  # noqa: F401
    from . import favorevent  # noqa: F401
    from . import friendsupport  # noqa: F401
    from . import gacha  # noqa: F401
    from . import instance  # noqa: F401
    from . import item  # noqa: F401
    from . import mail  # noqa: F401
    from . import payment  # noqa: F401
    from . import player  # noqa: F401
    from . import quest  # noqa: F401
    from . import rank  # noqa: F401
    from . import score  # noqa: F401
    from . import shop  # noqa: F401
    from . import sign  # noqa: F401
    from . import subareaachievement  # noqa: F401
