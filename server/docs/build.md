# 打包逻辑

## 一句话

**所有改动都落在 apktool 的解包目录里，然后让 apktool 打完整包。**

早期版本试过「以原版 APK 为底，手动拼 zip 只换改动的小文件」——确实快，
但绕过了 aapt2 的资源组装，剔除文件也只能靠字符串前缀匹配，不够可控。
实测完整 `apktool b` 只要 **1 分钟左右**（增量还更快），产物只大 0.1 MB，
所以现在统一走标准流程。

```
① apktool d zcsmw.apk                   解包（一次性，产物 zcsmw/）
        ↓
② 改解包目录                              ← 四个脚本，全部幂等，可重复跑
     client/patch_smali.py               Java 层登录补丁
     client/modernize.py                 现代化（sdk 版本 / 明文 HTTP / 运行时权限）
     tools/sdk_strip/strip.py            删 SDK + 装桩 + 清 manifest
     tools/sdk_strip/gen_native_stubs.py 生成 .so 硬依赖的桩
        ↓
③ python tools/build_apk.py              ★ 改 assets + apktool b + 对齐 + 签名
        ↓
④ adb install -r -d
```

**只改服务端时不用打包** —— 那只是 Python 代码，重启 `run.py` 就行。

---

## 第 ③ 步 `build_apk.py` 做什么

### 1. 改解包目录里的资源

| 文件 | 改动 |
|---|---|
| `assets/srcex/urlconfig.jsc` | CDN 地址**原地等长替换** |
| `assets/src/util/server.jsc` | 登录服务地址（客户端里硬编码的 `OAUTH_HOST`） |
| `assets/src/data/share.jsc` | 分享服务地址 |
| `assets/src/patch/project.manifest` | 热更地址 |
| `assets/project.json` | `jsList` 里追加 `src/patch/patch.js`（`--no-probe` 时不加 `probe.js`） |
| `assets/src/patch/patch.js` | **必须的客户端适配**（把 `__CDN_BASE__` 换成真实地址） |
| `assets/src/patch/probe.js` | 诊断探针（`--no-probe` 时不写、并从 assets 里删掉残留） |

> 早期只有一个 `hook.js`，后来拆成了 `patch.js`（必须的适配）+ `probe.js`（诊断）。
> `build_apk.py` 会自动清掉解包目录里残留的 `hook.js`。

### 2. 删掉用不到的资源

```
assets/res/adimage          广告原图（广告服务早停）
assets/res/adcolumn
assets/bdpwxpayplugin.apk   百度支付插件
assets/quicksdk.xml         QuickSDK 配置
assets/com.qk.plugin.qkfx.Manager   QuickSDK 插件管理器
assets/open_sdk_file.dat    QQ 互联 SDK
unknown/                    apktool 的「未知文件」目录
                            （里面只剩微博 CA 证书 x2 + 百度渠道号）
```

`lib/` 下没用的 `.so` 已经由 `sdk_strip/strip.py` 删过了。
依据是跑起来之后 `/proc/<pid>/maps` 里**只有 `libcocos2djs.so` 被加载**。

### 3. 剪掉 `apktool.yml` 里失效的 `doNotCompress`

apktool 把原版的压缩设置记在 `doNotCompress` 里。文件删了但条目还在时，
apktool 会照样按「不压缩」处理，有时还会把原版 APK 里的 unknown file 一起带进产物。
所以打包前会扫一遍，把**指向不存在路径**的条目剪掉
（只剪含 `/` 的路径条目，`arsc` / `png` / `mp3` 这种扩展名条目不能动）。

### 4. `apktool b <目录> -o out.apk --no-crunch`

`--no-crunch` = 不重新编码 PNG（省时间，也避免画质/格式被 aapt2 改动）。

### 5. zipalign + apksigner

* `zipalign -f -p 4`（`-p` 保证 4 字节页对齐）
* `apksigner` 同时开 **v1 + v2** 签名（老设备靠 v1，Android 7+ 用 v2）
* keystore 不存在就自动 `keytool -genkeypair` 生成（别名 `oppai`，密码 `android`）
* **必须重签**：改了内容原签名就失效；签名不同也无法覆盖安装官方版

---

## 关键约束：URL 必须「等长替换」

`.jsc` 里的字符串是 **长度前缀**存储的（像 Pascal 字符串），
改长度会让后面的字节码整体错位 → 引擎直接崩。

所以新地址的**字节数必须和旧地址完全一致**：

| 旧 | 长度 | 新 | 长度 |
|---|---|---|---|
| `cdn.shuangmawei.net` | 19 | `10.110.29.230:18080` | 19 ✅ |
| `http://114.55.66.97:16840` | 25 | `http://10.110.29.230:8080` | 25 ✅ |

**这就倒推出了端口位数的硬约束**：

* CDN 端口**必须 5 位**（`cdn.shuangmawei.net` 是 19 字节）
* 登录端口**必须 4 位**（旧的是 `http://` + IP + 5 位端口 = 25 字节）

长度不匹配时脚本直接报错退出，不会产出坏包。

---

## 幂等性

四个补丁脚本都可以重复跑，不会越跑越坏：

| 脚本 | 幂等做法 |
|---|---|
| `patch_smali.py` | 检测「已经打过」的标记 |
| `modernize.py` | 用正则替换 `<uses-sdk>`；注入前检查调用是否已存在 |
| `strip.py` | 目录不存在就跳过；桩类直接覆盖 |
| `build_apk.py` | URL 替换前先看**新地址是否已在**，是就跳过 |

所以 `build_apk.py` 可以随便重跑 —— 换个 IP 再跑一次就行。

---

## 路径配置

全部可用环境变量覆盖，方便换机器：

| 变量 | 默认 | 说明 |
|---|---|---|
| `GS_APK_SRC` | `E:\code\zcsmw\game.apk` | 原版 APK（只读，解包用） |
| `GS_APK_DIR` | `E:\code\zcsmw\game` | apktool 解包目录 |
| `GS_WORK_DIR` | `E:\code\zcsmw\out` | 产物目录（也放 keystore） |
| `GS_APKTOOL` | `E:\code\zcsmw\script\apktool.bat` | apktool |
| `GS_BUILD_TOOLS` | `D:\Android\android-sdk\build-tools\36.0.0` | zipalign / apksigner |
| `GS_JAVA_HOME` | `D:\java\zulu17...` | JDK |

---

## 完整重建命令

```powershell
# 1) 四个补丁（幂等）
python client\patch_smali.py
python client\modernize.py
python tools\sdk_strip\analyze.py --json tools\sdk_strip\needed.json   # SDK 没动过可跳过
python tools\sdk_strip\strip.py
python tools\sdk_strip\gen_native_stubs.py

# 2) 改 assets + 打包 + 签名（一步）
python tools\build_apk.py --host 10.110.29.230

# 3) 装
adb install -r -d E:\code\zcsmw\out\zcsmw-mod-signed.apk
```

`build_apk.py` 常用参数：

| 参数 | 说明 |
|---|---|
| `--host` / `--port` / `--login-port` | 服务端地址（端口位数有硬约束） |
| `--skip-prepare` | 跳过改 assets，只重新打包 |
| `--keep-intermediate` | 保留 `zcsmw-mod.apk` / `-aligned.apk` 中间产物 |
| `--out` | 输出路径 |

---

## 产物

```
E:\code\zcsmw\out\
  zcsmw-mod-signed.apk   ★   最终可安装（默认会清掉中间产物）
  debug.keystore             签名密钥
```

## 解包目录里哪些可以删

```
E:\code\zcsmw\game\
  AndroidManifest.xml      ← 必需
  apktool.yml              ← 必需（记录 sdk 版本 / doNotCompress 等）
  smali/ res/ assets/ lib/ ← 必需（被打包的内容）
  build/                   ← apktool 的增量构建缓存，可以随便删
  unknown/                 ← apktool 的「未知文件」，build_apk.py 会删掉
```

**`build/` 是纯派生数据**：

| 内容 | 作用 |
|---|---|
| `build/resources.zip` | aapt2 编译资源的中间产物 |
| `build/apk/` | 编译好的 `classes.dex` / `resources.arsc` / `AndroidManifest.xml` / `res/`；打包时 apktool 直接从这里拿 |

删掉完全没问题，下次 `apktool b` 会自动重建 —— 实测全量重建 **16.7 秒**
（带缓存时 12 秒），产物完全一致。

而且**删掉更干净**：apktool 打包时会把这个目录里的东西原样塞进 APK，
如果改过 smali 目录结构（比如把 `smali_classes2` 并进 `smali`），
缓存里旧的多余 dex 会跟着进包 —— 这就是之前包里出现 3 个 dex 的原因。

`build_apk.py` 里的 `prune_stale_dex()` 会自动清掉这类陈旧 dex，
所以平时不用手动删；只是遇到「包里多了 dex / 结构对不上」时，
`rm -rf build` 是最省事的解法。


---

## 实测数据

| 项 | 值 |
|---|---|
| 完整打包耗时 | **约 1 分钟**（增量约 12 秒） |
| APK 体积 | 545.1 MB（原版 551.6 MB） |
| 条目数 | 14790 |
| `lib/` | 只有 `libcocos2djs.so` |
| `META-INF/` | 只有自己的签名 |
