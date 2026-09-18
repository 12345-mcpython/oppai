import io

P = r"E:\code\python\game_server\gamesrv\handlers\agent.py"
s = io.open(P, encoding="utf-8").read()

OLD = '            "soldiers": [],'
NEW = '            "soldiers": store.new_soldiers(),'

if NEW in s:
    print("agent.py 已经改过")
elif OLD in s:
    s = s.replace(OLD, NEW, 1)
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("agent.py 已改成 store.new_soldiers()")
else:
    print("!! 没找到 soldiers 字段")
    for i, l in enumerate(s.split("\n")):
        if "soldier" in l.lower():
            print("   %4d: %s" % (i + 1, l.rstrip()))
