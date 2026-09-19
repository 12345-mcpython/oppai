import io

# 1) store.py 加常量
P = r"E:\code\zcsmw\server\gamesrv\store.py"
s = io.open(P, encoding="utf-8").read()
if "GUIDE_MARK_DONE" in s and "GUIDE_MARK_DONE =" not in s:
    print("!! 用了常量但没定义")
elif "GUIDE_MARK_DONE =" in s:
    print("store.py 常量已存在")
else:
    s = s.replace(
        'CHAR_TYPE_SOLDIER = "s"',
        'CHAR_TYPE_SOLDIER = "s"\n\n'
        '# 新手引导位掩码全 1 = 所有引导已完成。见 new_player() 里的说明。\n'
        'GUIDE_MARK_DONE = 0x7FFFFFFF',
        1,
    )
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("store.py 已加 GUIDE_MARK_DONE 常量")

# 2) agent.py 的 newPlayerGuide 改成 0
P2 = r"E:\code\zcsmw\server\gamesrv\handlers\agent.py"
t = io.open(P2, encoding="utf-8").read()
OLD = '"newPlayerGuide": 1,'
NEW = '"newPlayerGuide": 0,   # 0 = 不是新号，跳过新手引导'
if NEW.split(",")[0] in t:
    print("agent.py 已经改过")
elif OLD in t:
    t = t.replace(OLD, NEW, 1)
    io.open(P2, "w", encoding="utf-8", newline="\n").write(t)
    print("agent.py 已把 newPlayerGuide 改成 0")
else:
    print("!! agent.py 没找到 newPlayerGuide")
