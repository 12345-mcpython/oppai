"""解析 cocostudio 的 .csb（FlatBuffers）—— 列出动画区间和所有帧事件。

目的：确认 battlebeganui.csb 里 loop1/2/3 到底是「动画名」还是「帧事件」，
以及它们的帧号有没有落在 began3 的 [startIndex, endIndex] 区间内。

EventFrame::onEnter 里有范围检查：
    if (_frameIndex < _action->getStartFrame() || _frameIndex > _action->getEndFrame()) return;
事件帧号落在区间外就不会触发 —— 这是战斗收尾卡住的可疑原因。

Schema（CSParseBinary_generated.h 的真实偏移）：
    CSParseBinary : version(4) textures(6) texturePngs(8) nodeTree(10)
                    action(12) animationList(14)
    AnimationInfo : name(4) startIndex(6) endIndex(8)
    NodeAction    : duration(4) speed(6) timeLines(8) currentAnimationName(10)
    TimeLine      : property(4) actionTag(6) frames(8)
    Frame         : pointFrame(4) scaleFrame(6) colorFrame(8) textureFrame(10)
                    eventFrame(12) intFrame(14) boolFrame(16) innerActionFrame(18)
    EventFrame    : frameIndex(4) tween(6) value(8) easingData(10)

FlatBuffers 二进制约定：
    文件起始 4 字节 = root table 偏移
    table 起始 4 字节 = soffset（table_pos - soffset = vtable_pos）
    vtable[0] = vtable 字节数，之后每 2 字节一个字段偏移（相对 table）
    字段偏移 0 = 使用默认值
"""

from __future__ import annotations

import struct
import sys


class FB:
    def __init__(self, data: bytes):
        self.d = data

    def u16(self, p: int) -> int:
        return struct.unpack_from("<H", self.d, p)[0]

    def i32(self, p: int) -> int:
        return struct.unpack_from("<i", self.d, p)[0]

    def u32(self, p: int) -> int:
        return struct.unpack_from("<I", self.d, p)[0]

    def root(self) -> int:
        return self.u32(0)

    def field(self, table: int, voff: int):
        if table <= 0 or table + 4 > len(self.d):
            return None
        vt = table - self.i32(table)
        if vt < 0 or vt + 4 > len(self.d):
            return None
        vsize = self.u16(vt)
        if voff >= vsize or vt + voff + 2 > len(self.d):
            return None
        rel = self.u16(vt + voff)
        return table + rel if rel else None

    def indirect(self, p: int) -> int:
        return p + self.u32(p)

    def string(self, p) -> str:
        if p is None:
            return ""
        t = self.indirect(p)
        if t + 4 > len(self.d):
            return ""
        n = self.u32(t)
        return self.d[t + 4: t + 4 + n].decode("utf-8", "replace")

    def vector(self, p):
        if p is None:
            return []
        v = self.indirect(p)
        if v + 4 > len(self.d):
            return []
        n = self.u32(v)
        if n > 100000:
            return []
        return [v + 4 + i * 4 for i in range(n)]


def dump(path: str) -> int:
    fb = FB(open(path, "rb").read())
    root = fb.root()
    print("== 文件: %s" % path)

    node_action = fb.field(root, 12)
    action = fb.indirect(node_action) if node_action else None

    duration = fb.field(action, 4) if action else None
    print("   action.duration = %d" % (fb.i32(duration) if duration else 0))

    # ---- 动画区间 ----
    infos = []
    for item in fb.vector(fb.field(root, 14)):
        t = fb.indirect(item)
        nm, si, ei = fb.field(t, 4), fb.field(t, 6), fb.field(t, 8)
        name = fb.string(nm)
        if name:
            infos.append((name, fb.i32(si) if si else 0, fb.i32(ei) if ei else 0))
    print("\n== 动画（AnimationInfo）%d 个 ==" % len(infos))
    for name, si, ei in infos:
        print("   %-12s [%5d, %5d]  长度 %d" % (name, si, ei, ei - si))

    # ---- 帧事件 ----
    events = []
    if action:
        for tv in fb.vector(fb.field(action, 8)):
            tl = fb.indirect(tv)
            prop = fb.string(fb.field(tl, 4))
            for fv in fb.vector(fb.field(tl, 8)):
                fr = fb.indirect(fv)
                ev = fb.field(fr, 12)
                if ev is None:
                    continue
                e = fb.indirect(ev)
                fi = fb.field(e, 4)
                events.append((prop, fb.i32(fi) if fi else 0, fb.string(fb.field(e, 8))))
    events.sort(key=lambda x: x[1])
    print("\n== 帧事件（EventFrame）%d 个 ==" % len(events))
    for prop, fi, val in events:
        inside = [n for n, si, ei in infos if si <= fi <= ei]
        mark = ("  落在: " + ",".join(inside)) if inside else "  (不在任何动画区间内)"
        print("   frame %5d  %-26s [%s]%s" % (fi, val, prop, mark))

    # ---- 结论 ----
    print("\n== 结论 ==")
    names = {n for n, _, _ in infos}
    evnames = {v for _, _, v in events}
    loops_anim = sorted(n for n in names if n.startswith("loop"))
    loops_ev = sorted(v for v in evnames if v.startswith("loop"))
    print("   loop* 动画名 : %s" % (loops_anim or "无"))
    print("   loop* 事件名 : %s" % (loops_ev or "无"))
    for target in ("began1", "began2", "began3", "loop1", "loop2", "loop3"):
        hit = next((x for x in infos if x[0] == target), None)
        if not hit:
            continue
        _, si, ei = hit
        inside = [(e[1], e[2]) for e in events if si <= e[1] <= ei]
        print("   %-7s [%5d,%5d] 区间内事件: %s" % (target, si, ei, inside or "无"))
    return 0


if __name__ == "__main__":
    raise SystemExit(dump(sys.argv[1] if len(sys.argv) > 1 else
                          r"E:\code\apk\zcsmw\assets\res\ui\battlebeganui\src\battlebeganui.csb"))
