import io

P = r"E:\code\python\game_server\client\hook.js"
s = io.open(P, encoding="utf-8").read()

OLD = '''        if (typeof ccui.VideoPlayer === "function" && ccui.VideoPlayer.__oppaiFake) {
            return;
        }'''

NEW = '''        // 原生绑定已经注册了就别覆盖（引擎侧 register_all_oppai_videoplayer）
        if (typeof ccui.VideoPlayer === "function") {
            emit("VIDEO ccui.VideoPlayer 已存在（原生绑定），跳过 polyfill");
            return;
        }'''

if NEW in s:
    print("已经改过")
elif OLD in s:
    s = s.replace(OLD, NEW, 1)
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("已改成优先用原生实现")
else:
    print("!! 没找到目标片段")
