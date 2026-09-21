# 引擎复现（移植）工作区

目标：用源代码重建 `libcocos2djs.so`。**x86** 是第一步（摆脱 MuMu 的 libhoudini ARM 翻译层），
后来 armeabi / armeabi-v7a / **arm64-v8a** 也都编出来了 —— 四份 ABI 的打包与依赖见
[`../docs/build.md`](../docs/build.md) 的「ABI」一节。

---

## 当前进度（里程碑）

| # | 阶段 | 状态 |
|---|---|---|
| 1 | 确认版本 | ✅ cocos2d-x 3.6 + SpiderMonkey 33.1.1 |
| 2 | 拿到全部源码 | ✅ cocos2d-js v3.6 + cocos2d-x 3.6 + 3rd-party-libs v3-deps-47 + SM prebuilt |
| 3 | 挖出私有绑定的 API | ✅ 41 个函数（`ref/oppai_binding_api.txt`） |
| 4 | 搭建构建工程 | ✅ `build/oppai-engine/` |
| 5 | 重写 4 个私有绑定 | ✅ `Classes/{utilsex,gameshare,xg,talkingdata}/` |
| 6 | 补 `ccs.ActionTimelineCache` / `ccs.CSLoader` | ✅ 原版有、vanilla 仓库没有 |
| 7 | **编译通过** | ✅ `libcocos2djs.so` 18.92 MB |
| 8 | 替换进 APK，启动不崩 | ✅ 无 `UnsatisfiedLinkError` |
| 9 | **JS 引擎跑起来** | ✅ `cc` / `jsb` / `ccui` / `ccs` 全部就绪 |
| 10 | **自研绑定生效** | ✅ `cc.utilsex` 是 object |
| 11 | 游戏脚本加载 | ✅ `main.js` → `cc.game.run()` → `UpdateScene` 加载并渲染 |
| 12 | 补 `ccui.helper.seekNodeByName/ByTag` | ✅ **JS polyfill**（原生注册会被 jsb_boot.js 覆盖） |
| 13 | 补 `ActionTimeline` 回调参数 | ✅ **关键修复**，见下 |
| 14 | 补 Bugly 全局函数 | ✅ `buglySetUserId/SetTag/AddUserValue/Log` |
| 15 | 修 SDK 桩的接口/类错误 | ✅ `IWXAPI` 等改成 interface |
| 16 | **登录 + 进游戏** | ✅ **`isLogin=true`，播开场动画** |

## 🎯 最终状态：游戏完整可玩

```
✅ 自编引擎跑起来
✅ 热更新流程走完（UpdateScene → _init → _loadJs）
✅ 公告弹窗 → 关闭 → 登录界面
✅ 点击 Game Start → 登录成功（isLogin=true）
✅ loadinglayer → 主场景 → 新号开场动画
✅ 零 JS 报错、零崩溃
```

## ⭐ 最关键的一个修复：ActionTimeline 回调缺参数

游戏 `UpdateScene._logo` 里写的是：

```js
tl.setLastFrameCallFunc(function (eventName) {
    if (eventName === "default") { this._init(); }
});
```

但 cocos2d-js v3.6 的自动绑定生成的是：

```cpp
bool ok = func->invoke(0, nullptr, &rval);   // ← 0 个参数！
```

所以 `eventName` 永远是 `undefined`，`_init()` 永远不调用 →
`_loadJs()` 不执行 → 游戏的 `src/` 模块（`dataManager` / `table_dictionary`）从不加载
→ 游戏卡在 logo 屏（表现为纯黑，因为 `blackpanel` 盖在上面）。

原版引擎显然是**带动画名调用**的。修法是在 JS 层包一层：

```js
AT.prototype.play = function (name, loop) {
    this.__oppaiAnimName = name;          // 记下动画名
    return origPlay.apply(this, arguments);
};
AT.prototype.setLastFrameCallFunc = function (cb) {
    var self = this;
    return origSet.call(self, function () {
        return cb.call(self, self.__oppaiAnimName || "default");   // 补上参数
    });
};
```

**排查方法**：给 `setLastFrameCallFunc` 包一层日志，看到"引擎触发了、回调正常返回、
但场景不变"，再去反汇编 `update.jsc` 找到那个 31 字节的小 lambda：

```
UpdateScene<._logo/<  atoms: ['default', '_init']
   0  getarg 0                 ← 取第一个参数
   3  string "default"
   8  stricteq
   9  ifeq -> 30
  14  getaliasedvar slot=2
  19  dup
  20  callprop "_init"         ← 参数不等于 "default" 就永远不调
```

这类"引擎与游戏约定不一致"的问题，**单看 C++ 或单看 JS 都发现不了，必须两边对着看**。


| 现象 | 原因 | 解法 |
|---|---|---|
| `UnsatisfiedLinkError: nativeIsLandScape` | `main.cpp` 要用 **runtime 模板**（含 `ConfigParser` + AppActivity 的 JNI 实现） | 换 `js-template-runtime` 的 Classes 和 Android.mk |
| 只看到 `(evaluatedOK == JS_FALSE)`，没有错误信息 | cocos2d-js 默认 error reporter 写 **stderr**，Android 丢弃 | ① 在 `ScriptingCore::runScript` 里取出 pending exception；② 装自定义 `JS_SetErrorReporter` |
| `ccs.ActionTimelineCache is undefined` | 原版 `.so` 有这个绑定，**vanilla 3.6 仓库里没有** | 手写最小绑定（JS 自己覆盖了 `createAction`，只要 `getInstance()`） |
| `ccs.CSLoader is undefined` | 同上 | 手写绑定，转 `cocos2d::CSLoader::createNode/createTimeline` |
| 新建 `jsb_oppai_ccs.cpp` 编译不过，报 `CCString.h:207 __String::create` | `deprecated/CCString.h` 里有 **`#define ccs StringMake`**，文件名/标识符撞宏 | 把注册代码并进 `jsb_oppai_utilsex.cpp`，并 `#undef ccs` |
| `CSLoader has not been declared` | 它在 `cocos2d::` 下（`NS_CC_BEGIN` 里），不是 `cocostudio::` | 用裸 `CSLoader` |
| `GLES2/gl2platform.h: No such file` | NDK r10e 默认 `APP_PLATFORM=android-3` | 显式 `APP_PLATFORM := android-9` |
| `Cannot find module with tag 'freetype2/prebuilt/android'` | 缺第三方库 | 下 `v3-deps-47`（70 MB）解到 `cocos2d-x/external/` |
| `cc.utilsex is undefined`（`update.js:71`） | 绑定要挂在 **`cc`** 上，不是全局 | `oppai_attach()` 同时挂全局和 `cc` |

**关键工具**：`JS_SetErrorReporter` + 在 `ScriptingCore::runScript` 里打印 pending exception —— 
没有这两样就只能看到 `(evaluatedOK == JS_FALSE)`，完全瞎猜。


## 版本确认（从原版 `.so` 直接读的）

| 项 | 值 | 证据 |
|---|---|---|
| 引擎 | **cocos2d-x 3.6** | `.so` 字符串 `cocos2d-x 3.6`；源码 `cocos/cocos2d.h` 的 `COCOS2D_VERSION 0x00030600` |
| 脚本引擎 | **SpiderMonkey 33.1.1** | `JavaScript-C33.1.1`；jsc magic `0xb973c02c` = SM33.1.1 的字节码版本 |
| 发行版 | cocos2d-js v3.6 | 标准目录布局 |
| 原版 ABI | `armeabi`（ARMv5TE） | APK 里只有 `lib/armeabi/` |

原始工程路径（从 `.so` 调试字符串还原）：

```
F:\oppai\v2.2.0\client\oppai\
├── frameworks/runtime-src/proj.android_cn_quick/     ← Android 工程
├── frameworks/js-bindings/cocos2d-x/cocos/...        ← cocos2d-x 3.6
└── frameworks/js-bindings/bindings/{auto,manual}/    ← jsb 绑定
```

## 已解决的关键问题

### 1. `main.cpp` 用的是 **runtime 模板**，不是 default 模板

一开始用了 `js-template-default` 的 `main.cpp`，启动即报：

```
java.lang.UnsatisfiedLinkError: No implementation found for boolean
  org.cocos2dx.javascript.AppActivity.nativeIsLandScape()
```

`AppActivity` 有两个 native 方法，实现在 `main.cpp` 里：

```cpp
extern "C" {
    bool Java_org_cocos2dx_javascript_AppActivity_nativeIsLandScape(JNIEnv *env, jobject thisz)
    { return ConfigParser::getInstance()->isLanscape(); }   // 读 assets/config.json

    bool Java_org_cocos2dx_javascript_AppActivity_nativeIsDebug(JNIEnv *env, jobject thisz)
    { return COCOS2D_DEBUG > 0; }
}
```

这只存在于 **`js-template-runtime`**（它还带 `ConfigParser` / `VisibleRect` /
`runtime/` / `protobuf-lite/`）。游戏就是用这个模板建的 `proj.android_cn_quick`。

**所以 `Classes/` 和 `jni/Android.mk` 都要按 runtime 模板来。**

### 2. 构建环境的三个坑

| 坑 | 现象 | 解法 |
|---|---|---|
| 缺第三方库 | `Cannot find module with tag 'freetype2/prebuilt/android'` | 下 `cocos2d-x-3rd-party-libs-bin` 的 `v3-deps-47`（70 MB），解到 `cocos2d-x/external/` |
| `APP_PLATFORM` 没设 | `GLES2/gl2platform.h: No such file` | NDK r10e 默认 `android-3`（无 GLES2），必须显式 `APP_PLATFORM := android-9` |
| 路径层级 | `No rule to make target 'jni/../../Classes/...'` | 我的工程少了 `proj.android` 一层，改成 `../Classes/` |

### 3. `jsval_to_boolean` 不存在

cocos2d-x 3.6 的 `js_manual_conversions.h` 里没有，改用 `JS::ToBoolean()`。

## 四个自研绑定（41 个函数）

| 绑定 | 函数 | Java 对应 | JS 使用情况 |
|---|---|---|---|
| `utilsex` | 16 | `org/cocos2dx/javascript/Utilsex`（8 个转发，8 个原生） | **14 个 jsc 在用** |
| `GameShare` | 2 | `GameShare.shareToWeChat/shareToSina` | 3 个 jsc |
| `XGAdapter` | 6 | `XGAdapter.*` | 2 个 jsc |
| `TalkingDataAdapter` | 17 | — | **0 处调用，纯死代码** |

平台常量（从 `src/config/appconfig.jsc` 反汇编）：

```js
PLATFORM_CONFIG.YE          = 234592   // ← 本包
EXTRA_PLATFORM_CONFIG.QUICK = 234501   // ← 本包
```

`utilsex.getPlatform()` / `getExtraPlatform()` 必须返回这两个值，
否则 JS 会走错 SDK 分支。

## 编译方式

```powershell
cd E:\code\zcsmw\engine\build
.\build.ps1                 # 按 Application.mk 里的 APP_ABI
.\build.ps1 -Abi x86        # 换 ABI
.\build.ps1 -Clean          # 清理
```

内部就是：

```
NDK_MODULE_PATH = <js-bindings>;<cocos2d-x>;<cocos2d-x>/external;<cocos2d-x>/cocos
ndk-build -jN -C oppai-engine NDK_TOOLCHAIN_VERSION=4.8 NDK_DEBUG=0
```

`.\build.ps1 -Engine` 之前会先跑三个**幂等**的源码补丁
（`enable_js_debugger.py` / `fix_js_log.py` / `fix_null_texture.py`），
保证照着一份干净源码也能一键编出同样的 .so。
其余 `add_*.py` / `fix_*.py` 是一次性的，结果已经落在 `src/` 里了。

### 为什么要同时编 x86：让崩溃栈能说话

MuMu 是 x86 模拟器。包里只有 `armeabi` 时，引擎被 **houdini**（ARM→x86
二进制翻译层）接管；这时只要我们自己的代码里有空指针，tombstone 里往往
只剩一帧：

    #00 pc 0026865a  /system/lib/libhoudini.so
    signal 11 (SIGSEGV), code 2 (SEGV_ACCERR), fault addr 0xdead0000

看上去完全是翻译层自己的 bug（`0xdead0000` 是被污染/无权限的地址）。
换一份 **x86 原生** 的 `libcocos2djs.so` 复现，同一个崩溃立刻变成可读的
符号化栈：

    #00 Texture2D::getName() const+4          ← _texture 是 nullptr
    #01 Sprite::draw(Renderer*, Mat4 const&, unsigned int)+80
    #02 Node::visit ...
    #11 Director::drawScene()

结论：**看到「只有 libhoudini 一帧」的 tombstone，先编 x86 包复现一遍**，
不要急着怀疑 houdini。真正的根因见 `fix_null_texture.py`：
cocos2d-x 3.6 的 `Sprite::draw()` 对 `_texture` 没有判空，私服缺一张图
就是一个空指针解引用。

## 目录

```
E:\code\zcsmw\engine\
├── src/cocos2d-js/              474 MB   cocos2d-js v3.6 + cocos2d-x 3.6 + 3rd-party
├── ndk/android-ndk-r10e/        1.0 GB   NDK r10e
├── ref/                                  从原版 .so 挖出来的参考数据
│   ├── libcocos2djs-original.so          原始引擎（从备份 APK 取出）
│   ├── oppai_binding_api.txt             41 个导出函数
│   ├── register_symbols.txt
│   ├── jni_classes.txt                   36 个 JNI 硬依赖类
│   ├── compiled_sources.txt
│   └── version.txt
├── build/
│   ├── build.ps1                         构建脚本
│   ├── patch_appdelegate.py              注入 4 个绑定注册
│   └── oppai-engine/
│       ├── Classes/                      runtime 模板 + 4 个自研绑定
│       └── jni/{Android.mk,Application.mk,hellojavascript/}
└── out/libcocos2djs-new.so      18.92 MB 自编引擎
```

## 下一步（当前卡点）

**现象**：替换自编引擎后：
* `AppActivity` 能起来（`mResumedActivity` 就是它）
* **进程不崩**，无 `UnsatisfiedLinkError`
* 但：屏幕白屏、`logcat` 读不到 cocos 日志、探针不响应（JS 没跑）

**怀疑方向**：

1. **入口脚本** —— runtime 的 `AppDelegate` 用
   `ConfigParser::getInstance()->getEntryFile()` 决定入口，
   它读 `assets/config.json`。确认这个文件在不在、字段对不对。
2. **`CCFileUtils` 搜索路径** —— runtime 版可能有额外的
   `addSearchPath` / `setSearchPaths` 逻辑。
3. **JS 启动失败被吞掉** —— 得先把 logcat 弄正常才能看到。
4. **assets 布局** —— 原版有 `main.jsc` / `src/` / `script/` / `project.json`，
   确认和 `getEntryFile()` 的返回值对得上。

**建议下一步**：先把 logcat 恢复正常（`adb logcat -c` 后立刻读，或指定
`-b main -b system`），拿到引擎启动日志再定位。
