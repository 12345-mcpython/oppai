import ast
import glob
import os

for path in sorted(glob.glob('tools/*.py')):
    try:
        src = open(path, encoding='utf-8').read()
        doc = ast.get_docstring(ast.parse(src)) or ''
    except Exception as exc:  # noqa: BLE001
        doc = '!! ' + str(exc)
    first = ''
    for line in doc.splitlines():
        line = line.strip()
        if line and not line.startswith(('用法', '前提', '===')):
            first = line
            break
    print('%-24s %s' % (os.path.basename(path), first[:120]))
