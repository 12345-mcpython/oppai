r"""把引擎自带的那个 JS 调试器换成**明文源码**，顺便打几个补丁。

    python tools\patch_js_debugger.py

## 为什么

`ScriptingCore::compileScript`（`bindings/manual/ScriptingCore.cpp:661`）是这么找脚本的：

```cpp
std::string byteCodePath = RemoveFileExt(path) + ".jsc";
if (futil->isFileExist(byteCodePath)) { script = JS_DecodeScript(...); }   // a) 先找 .jsc
if (!script) { ... JS::Compile(... jsFileContent ...); }                  // b) 没有才读 .js
```

也就是说 **`.jsc` 只是缓存，删掉它引擎就回去读同名 `.js`**。
而 `require` 就是 `ScriptingCore::executeScript` → `runScript`，走的是同一条路。

于是只要把 `assets/script/jsb_debugger.js` 和 `assets/script/debugger/**` 换成明文、
删掉对应的 `.jsc`，**不用重编引擎**就能改调试器本身的 JS。

## 顺手打的补丁

`assets/script/debugger/actors/script.js` 的 `_discoverSources()`：

```js
for (let s of this.dbg.findScripts()) { scriptsByUrl[s.url] = s; }
```

实测在这套引擎上 `dbg.findScripts()`（**不带参数**）返回空数组，
所以 `sources` 请求永远是 `{sources: []}`，客户端根本不知道有哪些脚本，
也就无从下断点。这里改成：

1. 先试无参版，把结果打进 logcat（`log()` 是引擎给 debug global 装的函数，写 CCLOG）
2. 空的就再试 `findScripts({})`（空查询 = 全部脚本）
3. 两个都空就把 `dbg` 的状态也打出来，方便继续查

补丁是按「老字符串 → 新字符串」精确替换的，**幂等**，认 `[oppai]` 标记。
"""

from __future__ import annotations

import io
import os
import shutil
import sys

JSDIR = r"E:\code\apk\move\src\cocos2d-js\frameworks\js-bindings\bindings\script"
DST = r"E:\code\apk\zcsmw\assets\script"

# 要搬过去的明文源码（相对 bindings/script）
FILES = [
    "jsb_debugger.js",
    "debugger/DevToolsUtils.js",
    "debugger/main.js",
    "debugger/transport.js",
    "debugger/core/promise.js",
    "debugger/actors/root.js",
    "debugger/actors/script.js",
]

OLD_DISCOVER = """  _discoverSources: function TA__discoverSources() {
    // Only get one script per url.
    let scriptsByUrl = {};
    for (let s of this.dbg.findScripts()) {
      scriptsByUrl[s.url] = s;
    }

    return all([this.sources.sourcesForScript(scriptsByUrl[s])
                for (s of Object.keys(scriptsByUrl))]);
  },"""

NEW_DISCOVER = """  _discoverSources: function TA__discoverSources() {
    // [oppai] 原版是 `for (let s of this.dbg.findScripts())`。
    // 在这套引擎（cocos2d-js 3.6 + SpiderMonkey 33.1.1）上实测无参的
    // findScripts() 返回空数组 —— 于是 sources 永远是 []，
    // 客户端不知道有哪些脚本、也就没法下断点。
    // 这里依次退化成 findScripts({})，并把结果打进 logcat 方便继续查。
    let scriptsByUrl = {};
    let scripts = [];
    try {
      scripts = this.dbg.findScripts() || [];
    } catch (e) {
      log('[oppai] findScripts() threw: ' + e);
    }
    if (!scripts.length) {
      try {
        scripts = this.dbg.findScripts({}) || [];
      } catch (e) {
        log('[oppai] findScripts({}) threw: ' + e);
      }
    }
    for (let s of scripts) {
      if (s && s.url) {
        scriptsByUrl[s.url] = s;
      }
    }
    let urls = Object.keys(scriptsByUrl);
    log('[oppai] _discoverSources: findScripts()=' + scripts.length +
        ' distinctUrls=' + urls.length +
        ' debuggees=' + (this.dbg ? 'dbg-ok' : 'no-dbg') +
        ' state=' + this._state);
    for (let i = 0; i < urls.length && i < 15; i++) {
      log('[oppai]   src ' + urls[i]);
    }

    return all([this.sources.sourcesForScript(scriptsByUrl[s])
                for (s of urls)]);
  },"""

OLD_SETBP = """    // Find all scripts matching the given location
    let scripts = this.dbg.findScripts(aLocation);
    if (scripts.length == 0) {"""

NEW_SETBP = """    // Find all scripts matching the given location
    let scripts = this.dbg.findScripts(aLocation);
    log('[oppai] setBreakpoint ' + aLocation.url + ':' + aLocation.line +
        ' matchedScripts=' + scripts.length);
    if (scripts.length == 0) {"""

OLD_SETBP2 = """    if (scriptsAndOffsetMappings.size > 0) {
      for (let [script, mappings] of scriptsAndOffsetMappings) {
        for (let offsetMapping of mappings) {
          script.setBreakpoint(offsetMapping.offset, actor);
        }
        actor.addScript(script, this);
      }"""

NEW_SETBP2 = """    log('[oppai] setBreakpoint offsetMappings=' + scriptsAndOffsetMappings.size);
    if (scriptsAndOffsetMappings.size > 0) {
      for (let [script, mappings] of scriptsAndOffsetMappings) {
        for (let offsetMapping of mappings) {
          log('[oppai]   arm offset=' + offsetMapping.offset +
              ' line=' + offsetMapping.lineNumber + ' script=' + script.url);
          script.setBreakpoint(offsetMapping.offset, actor);
        }
        actor.addScript(script, this);
      }"""

OLD_HIT = """  hit: function BA_hit(aFrame) {
    // Don't pause if we are currently stepping (in or over) or the frame is
    // black-boxed."""

NEW_HIT = """  hit: function BA_hit(aFrame) {
    try {
      log('[oppai] BP HIT ' + this.location.url + ':' + this.location.line +
          ' actor=' + this.actorID);
    } catch (e) {}
    // Don't pause if we are currently stepping (in or over) or the frame is
    // black-boxed."""

OLD_GRIP = """    let g = {
      "type": "object",
      "class": this.obj.class,
      "actor": this.actorID,
      "extensible": this.obj.isExtensible(),
      "frozen": this.obj.isFrozen(),
      "sealed": this.obj.isSealed()
    };"""

NEW_GRIP = """    let g = {
      "type": "object",
      "class": this.obj.class,
      "actor": this.actorID,
      // [oppai] 这套 script.js 是从比 SpiderMonkey 33 更新的 Firefox 里搬过来的，
      // `Debugger.Object.prototype.isExtensible / isFrozen / isSealed` 在 SM 33 上
      // **还不存在**，直接调会抛：
      //     TypeError: this.obj.isExtensible is not a function
      // 而 grip() 是 frame.form() 里给 `this` 做 grip 时必经的一步 ——
      // 结果就是 `frames` 请求整个失败，调用栈根本拿不到。
      // 兜一下：函数不存在就给个保守值（这三个字段只有客户端的属性编辑器在用）。
      "extensible": (typeof this.obj.isExtensible === "function") ? this.obj.isExtensible() : true,
      "frozen": (typeof this.obj.isFrozen === "function") ? this.obj.isFrozen() : false,
      "sealed": (typeof this.obj.isSealed === "function") ? this.obj.isSealed() : false
    };"""

PATCHES = [
    ("debugger/actors/script.js", OLD_DISCOVER, NEW_DISCOVER),
    ("debugger/actors/script.js", OLD_SETBP, NEW_SETBP),
    ("debugger/actors/script.js", OLD_SETBP2, NEW_SETBP2),
    ("debugger/actors/script.js", OLD_HIT, NEW_HIT),
    ("debugger/actors/script.js", OLD_GRIP, NEW_GRIP),
]


def main() -> int:
    if not os.path.isdir(JSDIR):
        print(f"!! 找不到引擎源码里的 script 目录: {JSDIR}", file=sys.stderr)
        return 1
    if not os.path.isdir(os.path.join(DST, "debugger")):
        print(f"!! 找不到 APK assets 里的 debugger 目录: {DST}", file=sys.stderr)
        return 1

    copied = 0
    removed = 0
    for rel in FILES:
        src = os.path.join(JSDIR, rel.replace("/", os.sep))
        dst = os.path.join(DST, rel.replace("/", os.sep))
        if not os.path.isfile(src):
            print(f"!! 源文件不存在: {src}", file=sys.stderr)
            return 1
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        copied += 1
        jsc = os.path.splitext(dst)[0] + ".jsc"
        if os.path.isfile(jsc):
            os.remove(jsc)
            removed += 1

    # 打补丁
    for rel, old, new in PATCHES:
        path = os.path.join(DST, rel.replace("/", os.sep))
        with io.open(path, encoding="utf-8") as fh:
            text = fh.read()
        if new in text:
            print(f"[js-debugger] {rel} 已经打过补丁")
            continue
        if old not in text:
            print(f"!! {rel} 里找不到要替换的代码（cocos2d-js 换版本了？）", file=sys.stderr)
            return 1
        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text.replace(old, new, 1))
        print(f"[js-debugger] {rel} 已打补丁")

    print(f"[js-debugger] 复制 {copied} 个 .js，删掉 {removed} 个 .jsc")
    print("[js-debugger] 下一步： python tools\\build_apk.py  然后 adb install")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
