import io

P = r"E:\code\zcsmw\script\build_apk.py"
s = io.open(P, encoding="utf-8").read()

REPL = [
    (
        'def prepare_assets(host: str, port: int, login_port: int, hook_path: str) -> None:',
        'def prepare_assets(host: str, port: int, login_port: int, patch_path: str,\n'
        '                   probe_path: str, with_probe: bool) -> None:',
    ),
    (
        'DEFAULT_HOOK = os.path.join(BASE_DIR, "client", "hook.js")',
        'DEFAULT_PATCH = os.path.join(BASE_DIR, "client", "patch.js")\n'
        'DEFAULT_PROBE = os.path.join(BASE_DIR, "client", "probe.js")',
    ),
    (
        'ap.add_argument("--hook", default=DEFAULT_HOOK)',
        'ap.add_argument("--patch", default=DEFAULT_PATCH)\n'
        '    ap.add_argument("--probe", default=DEFAULT_PROBE)\n'
        '    ap.add_argument("--no-probe", action="store_true",\n'
        '                    help="不打包 probe.js（release 构建）")',
    ),
    (
        'prepare_assets(args.host, args.port, args.login_port, args.hook)',
        'prepare_assets(args.host, args.port, args.login_port, args.patch,\n'
        '                   args.probe, not args.no_probe)',
    ),
]

n = 0
for old, new in REPL:
    if old in s and new.split("\n")[0] not in s.replace(old, ""):
        s = s.replace(old, new, 1)
        n += 1
    elif old in s:
        s = s.replace(old, new, 1)
        n += 1

io.open(P, "w", encoding="utf-8", newline="\n").write(s)
print("替换了 %d 处" % n)
