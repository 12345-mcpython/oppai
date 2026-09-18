# 打包逻辑

## 一句话

**不重新打包，而是在原版 APK 上做外科手术** —— 只把真正改动过的那几个小文件换掉。

原因：`apktool b` 完整重建会把 **540 MB 的 assets 重新压缩一遍**，
慢（10 分钟以上）、而且会改变压缩方式和 zip 对齐，风险大收益小。
游戏资源一个字节都不需要动，没必要陪跑。

---

## 流水线

```
① apktool d zcsmw.apk             解包（一次性，产物 zcsmw/）
        ↓
② 改 smali / manifest             ← 三个脚本，都是幂等的，可以重复跑
     client/patch_smali.py        Java 层登录补丁
     client/modernize.py          现代化（sdk 版本 / 明文 HTTP / 运行时权限）
     tools/sdk_strip/strip.py     删 SDK + 装桩 + 清 manifest
        ↓
③ apktool b zcsmw --no-apk --no-crunch
        ↓  产物在 zcsmw/build/apk/：
        ↓    classes.dex / classes2.dex / AndroidManifest.xml / resources.arsc / res/
        ↓
④ python tools/patch_apk.py       ★ 以原版 APK 为底，逐条目替换/剔除/追加
        ↓
⑤ zipalign -f -p 4
        ↓
⑥ apksigner sign（v1 + v2）
        ↓
⑦ adb install -r -d
```

### 第 ② 步：为什么不直接改 dex

apktool 解包后是 smali 文本，不是 dex。改 smali 很直观（可读、可 diff），
但**必须重编译**才能生效 —— 这就是第 ③ 步只重编译 smali 和资源的原因。

### 第 ③ 步：`--no-apk --no-crunch` 两个参数

| 参数 | 作用 |
|---|---|
| `--no-apk` | 不要打成 APK，只要 `build/apk/` 里的中间产物（我们要自己组装） |
| `--no-crunch` | 不重新编码 PNG。省时间，也避免画质/格式被 aapt2 改动 |

这一步**不碰 assets**（apktool 的 `build/apk/` 里根本没有 assets 目录），
所以 540 MB 资源全程原封不动。

### 第 ④ 步：`patch_apk.py` 到底做了什么

打开**原版 APK**，遍历每一条 zip 条目，按规则处理：

| 规则 | 处理 |
|---|---|
| `META-INF/*.SF/.RSA/.DSA/.MF` | **跳过**（旧签名，反正要重签） |
| `--strip` 前缀命中 | **跳过**（广告图 / 百度支付插件 / quicksdk.xml） |
| `lib/` 下不在 `KEEP_LIBS` 的 `.so` | **跳过**（实测只有 `libcocos2djs.so` 会被加载） |
| `classes*.dex` | **替换**成 `build/apk/` 里重编译的 |
| `AndroidManifest.xml` | **替换**成重编译的（现代化补丁改过） |
| `resources.arsc` | **替换**成重编译的，并强制 **STORED**（不压缩） |
| `assets/srcex/urlconfig.jsc` | **原地改**：CDN 地址等长替换 |
| `assets/src/util/server.jsc` | **原地改**：登录服务地址等长替换 |
| `assets/src/data/share.jsc` | 同上 |
| `assets/src/patch/project.manifest`、`assets/project.json` | **原地改**：热更地址 |
| 其余 | **原样拷贝**，连 `compress_type` 都照抄（原来 STORED 的还 STORED） |

最后**追加**一条：`assets/src/patch/hook.js`
（先把源码里的 `__CDN_BASE__` 占位符替换成真实地址再写进去）。

### 为什么 `resources.arsc` 要 STORED

Android 从某个版本起要求 `resources.arsc` **不能压缩**，且要 4 字节对齐
（`zipalign -p 4` 负责页对齐）。原来 STORED 的条目我们也不动它。

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

* CDN 端口**必须 5 位**（因为 `cdn.shuangmawei.net` 是 19 字节）
* 登录端口**必须 4 位**（因为旧的 `:16840` 是 5 位数字 + 20 字节前缀 = 25）

代码里会检查长度，不匹配直接 `raise SystemExit`，不会打出一个坏包。

---

## 签名

* keystore 不存在时自动生成：
  ```
  keytool -genkeypair -alias oppai -keyalg RSA -keysize 2048 -validity 10000
          -storepass android -keypass android
  ```
  默认落在 `E:\code\apk\work\debug.keystore`（可用 `GS_WORK_DIR` 改）
* `apksigner` 同时开 **v1 + v2** 签名（老设备靠 v1，Android 7+ 用 v2）
* **为什么必须重签**：改了内容原签名就失效；而且签名不同会导致
  无法覆盖安装官方版（得先卸载）

---

## 路径配置

全都可以用环境变量覆盖，方便换机器：

| 变量 | 默认 | 说明 |
|---|---|---|
| `GS_APK_SRC` | `E:\code\apk\zcsmw.apk` | 原版 APK（只读，当底稿） |
| `GS_APK_DIR` | `E:\code\apk\zcsmw` | apktool 解包目录 |
| `GS_WORK_DIR` | `E:\code\apk\work` | 产物目录（也放 keystore） |
| `GS_BUILD_TOOLS` | `D:\Android\android-sdk\build-tools\36.0.0` | zipalign / apksigner |
| `GS_JAVA_HOME` | `D:\java\zulu17...` | JDK |

---

## 完整重建命令

```powershell
$env:GS_APK_DIR = "E:\code\apk\zcsmw"

# 1) 三个补丁（幂等，可重复跑）
python client\patch_smali.py
python client\modernize.py
python tools\sdk_strip\analyze.py --json tools\sdk_strip\needed.json   # SDK 删过就不用再跑
python tools\sdk_strip\strip.py
python tools\sdk_strip\gen_native_stubs.py

# 2) 只重编译 smali + 资源
cd E:\code\apk
apktool.bat b zcsmw --no-apk --no-crunch

# 3) 组装 + 对齐 + 签名
cd <repo>
python tools\patch_apk.py --host 10.110.29.230 --port 18080 --login-port 8080

# 4) 装
adb install -r -d E:\code\apk\work\zcsmw-mod-signed.apk
```

**只改服务端时不用打包** —— 那只是 Python 代码，重启 `run.py` 就行。

**只改 `client/hook.js` 时**：hook.js 是明文注入的，改完只需要重跑第 3 步
（不必再跑 apktool，因为 dex 没变）。如果想省时间可以加 `--no-dex`，
但注意那会把 dex 退回原版的（登录补丁就没了）—— 所以实际上还是要跑 apktool。

---

## 产物

```
E:\code\apk\work\
  zcsmw-mod.apk              组装好的、未对齐未签名
  zcsmw-mod-aligned.apk      zipalign 之后
  zcsmw-mod-signed.apk   ★   最终可安装
  zcsmw-mod-signed.apk.idsig apksigner 生成的 v4 签名（可以不装）
  debug.keystore             签名密钥
```

---

## 设计取舍小结

1. **不重打 540 MB assets** —— 只在原版 APK 上替换真正改动的条目
2. **原始 APK 只读** —— 所有产物都写到 `work/`，随时可以重来
3. **补丁幂等** —— `modernize.py` / `patch_smali.py` / `strip.py` 都能重复跑，
   不会越跑越坏（有「已经打过」的检测）
4. **不用 apktool 的完整 APK** —— 避免资源重编号、压缩率变化、对齐丢失等副作用
5. **长度检查前置** —— URL 替换不等长时直接报错退出，不会产出坏包
