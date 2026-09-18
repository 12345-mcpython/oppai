"""去掉 patch.js 里 ActionTimeline 最后一帧回调的「只触发一次」守卫。

这个守卫是早期为 UpdateScene 的「重复回调」猜的 —— 后来证明真正原因是
引擎不传动画名（已在 C++ 层根治），守卫本身没必要，而且**有害**：

游戏复用同一个 ActionTimeline 播整段序列：
    began1 -> loop1 -> end1 -> began2 -> loop2 -> end2 -> began3 -> loop3 -> end3
每一段的结束都要靠最后一帧回调推进。守卫让每个 timeline 只允许触发一次，
所以 began3 之后 loop3 永远播不出来 —— 战斗收尾卡死。

日志证据（pl.txt）：
    play       began3
    lastFrame  began3        <- 引擎传参完全正确
    （之后 loop3 再没出现，也没有任何 JS 报错）

修法：把 __oppaiLfFired 这套判断整个删掉，回调每次都直达。

注：UpdateScene._logo 那边不依赖守卫 —— 它自己用 eventName === "default"
做判断，天然只处理一次。
"""

import io

P = r"E:\code\python\game_server\client\patch.js"
s = io.open(P, encoding="utf-8").read()

OLD = '''            self.__oppaiLfFired = false;
            var wrapped = function (engineName) {
                if (self.__oppaiLfFired) { return; }
                self.__oppaiLfFired = true;
'''

NEW = '''            // 注意：这里**不能**加"只触发一次"的守卫。
            // 游戏复用同一个 ActionTimeline 播整段序列（began1→loop1→end1→…→end3），
            // 每一段的推进都依赖最后一帧回调，挡掉第二次就会卡死收尾。
            var wrapped = function (engineName) {
'''

if "不能**加" in s:
    print("已经改过")
elif OLD in s:
    s = s.replace(OLD, NEW, 1)
    # 兜底分支里的 fired 标记也清掉
    s = s.replace('''                for (var i = 0; i < found.length; i++) {''', '''                for (var i = 0; i < found.length; i++) {''')
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("已去掉只触发一次的守卫")
else:
    print("!! 没找到守卫片段")
