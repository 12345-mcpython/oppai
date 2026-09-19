"""把 AT 包装里的回调包上 try/catch + 前后场景对比，定位回调到底做了什么。"""

import io

P = r"E:\code\zcsmw\server\client\hook.js"
s = io.open(P, encoding="utf-8").read()

OLD = '''            self.__oppaiLfFired = false;
            var wrapped = function () {
                if (self.__oppaiLfFired) { return; }
                self.__oppaiLfFired = true;
                emit("AT.lastFrame 触发（引擎）frame=" + (self.getCurrentFrame ? self.getCurrentFrame() : "?"));
                return cb.apply(self, arguments);
            };'''

NEW = '''            self.__oppaiLfFired = false;
            var wrapped = function () {
                if (self.__oppaiLfFired) { return; }
                self.__oppaiLfFired = true;
                var scB = null;
                try { scB = cc.director.getRunningScene(); } catch (e) { }
                emit("AT.lastFrame 触发 frame=" + (self.getCurrentFrame ? self.getCurrentFrame() : "?") +
                     " sceneBefore=" + (scB ? scB.getChildrenCount() : "-"));
                try {
                    var r = cb.apply(self, arguments);
                    var scA = null;
                    try { scA = cc.director.getRunningScene(); } catch (e) { }
                    emit("AT.lastFrame cb 正常返回 sceneAfter=" + (scA ? scA.getChildrenCount() : "-"));
                    return r;
                } catch (e) {
                    emit("AT.lastFrame cb 抛异常!! " + e);
                    throw e;
                }
            };'''

if "cb 抛异常" in s:
    print("已经改过了")
elif OLD in s:
    s = s.replace(OLD, NEW, 1)
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("已加上 try/catch 诊断")
else:
    print("!! 没找到目标片段")
