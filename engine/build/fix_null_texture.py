r"""修 `Sprite::draw` 的空贴图崩溃。

    python fix_null_texture.py

## 现象

游戏在新手引导那一段必崩（GLThread），tombstone 里：

    signal 11 (SIGSEGV), code 1 (SEGV_MAPERR), fault addr 0x20
    #00 Texture2D::getName() const+4
    #01 Sprite::draw(Renderer*, Mat4 const&, unsigned int)+80
    #02 Node::visit ...
    #11 Director::drawScene()

（这是 x86 原生包才有的符号化栈；同一处崩溃在 armeabi 包里被 houdini
吞掉，只剩 `#00 /system/lib/libhoudini.so` + `fault addr 0xdead0000`，
看着像翻译层自己的 bug，其实是我们自己的空指针。）

`fault addr 0x20` = `this + 0x20`，也就是 `Texture2D::getName()` 里的
`_name` 成员，而 `this`（`_texture`）是 **nullptr**。

## 原因

cocos2d-x 3.6 的 `Sprite::draw()` 直接就是：

    _quadCommand.init(_globalZOrder, _texture->getName(), ...);

**没有判空**。而私服的资源不可能 100% 齐 —— 只要有一个精灵是
`new cc.Sprite()` 之后贴图没设上（`initWithFile` / `setSpriteFrame`
失败，或者贴图被释放），它一进渲染队列就是空指针解引用，整个 GLThread
直接挂掉。

## 改法

`_texture == nullptr` 就直接 return（这块精灵不画），并且打一条日志
指出是哪个节点。这样缺资源最坏的结果是「这块图看不见」，而不是闪退。

日志用 `cocos2d::log` 而不是 `CCLOG`：`CCLOG` 在 release
（`COCOS2D_DEBUG == 0`）里是空宏，什么都打不出来。
"""

from __future__ import annotations

import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MOVE = os.path.dirname(HERE)
TARGET = os.path.join(MOVE, "src", "cocos2d-js", "frameworks", "js-bindings",
                      "cocos2d-x", "cocos", "2d", "CCSprite.cpp")

MARK = "[oppai] Sprite::draw 空贴图判空"

INC_OLD = '#include "deprecated/CCString.h"'
INC_NEW = ('#include "deprecated/CCString.h"\n'
           '#include "base/CCConsole.h"  // [oppai] Sprite::draw 判空时要打日志')

DRAW_OLD = """void Sprite::draw(Renderer *renderer, const Mat4 &transform, uint32_t flags)
{
#if CC_USE_CULLING"""

DRAW_NEW = """void Sprite::draw(Renderer *renderer, const Mat4 &transform, uint32_t flags)
{
    // [oppai] Sprite::draw 空贴图判空 ##########
    //
    // 3.6 原版这里没有判空，下面第一行就是 _texture->getName()：
    // 只要有一个精灵没贴上图（私服缺资源、initWithFile 失败、贴图被释放），
    // 就是一次空指针解引用，GLThread 直接 SIGSEGV（fault addr 0x20）。
    //
    // 注意别把这段当成「houdini 的锅」：armeabi 包跑在 x86 模拟器上时，
    // 崩溃栈只剩 #00 /system/lib/libhoudini.so、fault addr 0xdead0000，
    // 看起来像翻译层自己崩了；编个 x86 原生的包，栈立刻变成
    // Sprite::draw -> Texture2D::getName，一眼就能看出是自己的空指针。
    if (_texture == nullptr)
    {
        // 只报前 20 次，免得每帧刷屏把 logcat 冲掉
        static int s_oppaiNullTex = 0;
        if (s_oppaiNullTex < 20)
        {
            ++s_oppaiNullTex;
            cocos2d::log("[oppai] Sprite::draw 贴图为空，跳过该精灵"
                         "（name=%s tag=%d pos=(%.1f,%.1f)）",
                         getName().c_str(), _tag, _position.x, _position.y);
        }
        return;
    }

#if CC_USE_CULLING"""


def main() -> int:
    if not os.path.isfile(TARGET):
        print(f"!! 找不到 {TARGET}", file=sys.stderr)
        return 1

    with io.open(TARGET, encoding="utf-8") as fh:
        text = fh.read()

    if MARK in text:
        print(f"[null-tex] 已经打过补丁，跳过")
        return 0

    if DRAW_OLD not in text:
        print("!! 找不到 Sprite::draw 的开头（cocos2d-x 换版本了？）", file=sys.stderr)
        return 1

    if '#include "base/CCConsole.h"' not in text:
        if INC_OLD not in text:
            print("!! 找不到 CCString.h 那行 include，无法插 CCConsole.h", file=sys.stderr)
            return 1
        text = text.replace(INC_OLD, INC_NEW, 1)

    text = text.replace(DRAW_OLD, DRAW_NEW, 1)

    with io.open(TARGET, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)

    print(f"[null-tex] 已写入 {TARGET}")
    print("[null-tex] 下一步： .\\build.ps1 -Engine   （armeabi + x86 都要重编）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
