r"""让 `Widget::propagateTouchEvent` 能**穿过裸 Node** 往上找到父 Widget。

    python fix_scrollview_propagate.py

## 现象

宿舍「RoomList」的列表条目（`FavorListLayer` 的 item）是**裸 `cc.Node`**
（`favorlistitemlayer.csb` 里没有 Widget 包装），而列表本身是 `ccui.ScrollView`。
结果：**在条目上按住拖动，列表不滚**；只有从条目之间的空隙起手才滚得动。

## 根因

cocos2d-x 3.6 的 `Widget::propagateTouchEvent`（`cocos/ui/UIWidget.cpp`）：

    Widget* widgetParent = getWidgetParent();     // == dynamic_cast<Widget*>(getParent())
    if (widgetParent) widgetParent->interceptTouchEvent(event, sender, touch);

`getWidgetParent()` **只看直接父节点**，而且必须 `dynamic_cast<Widget*>` 成功。于是：

    条目里的按钮(Widget)
      -> propagateTouchEvent
      -> getWidgetParent() = dynamic_cast<Widget*>(裸 Node) = **nullptr**
      -> 传播到此为止

`ScrollView::interceptTouchEvent` 永远收不到 BEGAN/MOVED，
`_isInterceptTouch` / `handleMoveLogic` 都不跑 —— 拖动就被条目自己的
`_touchListener`（`Widget::addTouchEventListener` 里写死的 `setSwallowTouches(true)`）
吃掉了。

原版引擎显然能穿过去。这是本项目反复遇到的那一类问题：
**引擎与游戏的约定不一致，单看 C++ 或单看 JS 都发现不了**（见
`server/docs/overview.md` §6 和 `ENGINE_PATCHES.md`）。

## 改法

把「只看直接父节点」改成「**沿裸父链往上找第一个 Widget**」：

    Node* parent = getParent();
    while (parent)
    {
        Widget* w = dynamic_cast<Widget*>(parent);
        if (w) { w->interceptTouchEvent(event, sender, touch); return; }
        parent = parent->getParent();
    }

⚠️ 只影响「传播」这一条路：
  * 条目自己的点击**照旧**（`onTouchEnded` 仍然发给它）
  * 父 ScrollView 现在也能拿到事件，于是原版那套
    「拖动超过 `_childFocusCancelOffset`(5px) 就滚动、不到 5px 当成点击」生效
"""

from __future__ import annotations

import io

P = (r"E:\code\zcsmw\engine\src\cocos2d-js\frameworks\js-bindings\cocos2d-x"
     r"\cocos\ui\UIWidget.cpp")

OLD = """void Widget::propagateTouchEvent(cocos2d::ui::Widget::TouchEventType event, cocos2d::ui::Widget *sender, cocos2d::Touch *touch)
{
    Widget* widgetParent = getWidgetParent();
    if (widgetParent)
    {
        widgetParent->interceptTouchEvent(event, sender, touch);
    }
}"""

NEW = """void Widget::propagateTouchEvent(cocos2d::ui::Widget::TouchEventType event, cocos2d::ui::Widget *sender, cocos2d::Touch *touch)
{
    // [oppai] 原版只走 `getWidgetParent()`（= dynamic_cast<Widget*>(getParent())），
    // 只看**直接**父节点。游戏里 ScrollView 的条目是裸 cc.Node，
    // 于是传播在那一层就断了 —— 表现是「在条目上拖动，列表不滚」。
    // 这里改成沿裸父链往上找第一个 Widget。
    Node* parent = getParent();
    while (parent)
    {
        Widget* widgetParent = dynamic_cast<Widget*>(parent);
        if (widgetParent)
        {
            widgetParent->interceptTouchEvent(event, sender, touch);
            return;
        }
        parent = parent->getParent();
    }
}"""

s = io.open(P, encoding="utf-8").read()

if "[oppai] 原版只走" in s:
    print("[sv-propagate] 已经打过补丁，跳过")
elif OLD in s:
    io.open(P, "w", encoding="utf-8", newline="\n").write(s.replace(OLD, NEW, 1))
    print("[sv-propagate] 已改成沿裸父链往上找父 Widget")
else:
    raise SystemExit("!! 没匹配到 Widget::propagateTouchEvent —— cocos2d-js 源码版本对不上？")
