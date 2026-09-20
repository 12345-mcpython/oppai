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
     server\client\modernize.py                 现代化（sdk 版本 / 明文 HTTP / 运行时权限）
     script\sdk_strip\strip.py                  删 SDK + 装桩 + 清 manifest
     script\sdk_strip\gen_native_stubs.py       生成 .so 硬依赖的桩
     server\client\patch_smali.py               Java 层登录补丁 + 退出弹窗
                                                ⚠️ 必须最后跑，见「脚本顺序」
        ↓
③ python script\build_apk.py             ★ 改 assets + apktool b + 对齐 + 签名
        ↓
④ adb -s <设备> install -r -d
```

> ⚠️ **模拟器和真机同时连着时必须带 `-s`**。`build.ps1` 以前只有 `adb reverse` 那步带了
> `-s $Serial`，`install` / `forward` / 启动都没带 —— 单设备时看不出来，一旦手机和模拟器
> 一起插着就报 `adb: more than one device/emulator`（2026-09-20 踩到，已修）。
> 指定设备：`.\build.ps1 -Install -Serial <序列号>`，或 `$env:GS_ADB_SERIAL`。

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

### 2a-2. 删掉 `res/` 下的 SDK 资源（`DROP_RES_PREFIXES`）

渠道 SDK 的 Java 类早被 `strip.py` 删光了，`res/` 里还剩 **906 个文件 / 1613 KB**
的布局和图，永远 inflate 不到。前缀就是渠道名：

```
bdp_ 百度支付   dk_ 多酷   wallet_ 百度钱包   ebpay_ 易宝支付
bd_  百度杂项   qk_ QuickSDK
```

效果：**APK −1.58 MB**（res 条目 921 → 48）。这是整个精简里最大的一块。

#### ⚠️ 坑 1：不能「带前缀就删」

`res/values/*.xml`（不能整文件删，游戏和 SDK 的条目混在一起）里有 **46 处**
引用着 `@anim/wallet_base_slide_from_right` 这类 SDK 资源。直接删，aapt 会报
`resource anim/wallet_base_slide_from_right not found`。

所以要做**引用闭包**：

```
根 = 非 SDK 前缀的文件 + res/values*/** + AndroidManifest.xml
边 = XML 里的 @type/name
保留 = 闭包里的全部；删除 = 带 SDK 前缀且不在闭包里的
```

结果：删 873 个（1594 KB），留 33 个（19 KB，被 values 引用着的那批 + 它们的依赖）。

#### ⚠️ 坑 2：九图的名字要多剥一层

`bd_wallet_single_item_bg.9.png` 的资源名是 `bd_wallet_single_item_bg`，
而 `os.path.splitext()` 只给到 `bd_wallet_single_item_bg.9` —— 拿它比
public.xml / XML 引用永远对不上，于是「文件删了、public.xml 条目留着」，
aapt 报 `no definition for declared symbol`。见 `_res_name()`。

#### ⚠️ 坑 3（最隐蔽）：apktool 的 `build/apk/` 缓存

`build/apk/` 是 apktool 的**增量缓存，打包时原样塞进 APK**。
删掉 `game/res` 下某个资源后，缓存里编译好的那份还在 → **删了等于没删**。

实测：删 873 个资源后 APK 只小了 90 KB，一查包内还有 906 个 SDK 资源原封不动。
（`resources.arsc` 和 `classes.dex` 都会重新生成，看着一切正常，**只有 `res/`
是增量的** —— 这个坑非常容易漏。）

`classes.dex` 那边早有 `prune_stale_dex()`，`res` 一直没人管；现在补了
`prune_stale_build_res()`，做法是整个删掉 `build/apk/res/` 让它全量重编。

#### 收尾：aapt 是最后一道保险

删完仍然由 aapt 兜底：只要还有**保留的**文件引用被删资源，打包会直接**报错**，
不会出静默失效的包。真报错就把 `out/removed-res/` 里对应文件拿回来。

### 2b. 删掉死代码 smali（`DROP_SMALI`）

```
smali/android/support   1124 个文件 / 7.5 MB smali
smali/android/net       20 个文件（android.net.http.* + WebAddress）
```

判据是「整个工程一处引用都没有」：

* `android/support/**` —— `smali/com`、`smali/org`、`AndroidManifest.xml`、
  `assets/` 下的 js/jsc **全部零引用**。唯一的引用方是 `res/layout` 里百度钱包 /
  多酷的三个布局（`bd_wallet_sign_channel_list.xml`、`dk_dialog_back.xml`、
  `dk_downloadmanager_activity.xml`），而那几个 SDK 的类早被 `strip.py` 删干净了，
  这些布局永远 inflate 不到。
* MultiDex 没人用，Application 链是
  `org.cocos2dx.javascript.GameApplication` → `com.quicksdk.QuickSdkApplication`
  → `android.app.Application`，没有 `MultiDexApplication`；
  而且只有**一个** `classes.dex`，本来就不需要 multidex。
* `android/net/**` 是 **framework 类**（真正的 `android.net.Uri` /
  `WifiManager` 在 `/system/framework` 里）。app dex 里的同名类永远被
  boot classpath 挡住，放进来纯属白占地方。

效果：`classes.dex` **1,508,336 → 371,340 字节（−75%）**。

> ⚠️ 但**别用 smali 的字节数估 APK 的收益**：dex 在包里是 DEFLATE 的，
> 压缩比约 3:1，所以 APK 上只少了 **0.44 MB**（580,377,326 → 579,918,574）。
> 想看真实数字就 `python script\apk_report.py`。

### 2b-2b. 把「游戏自己的代码还吊着」的 SDK 也清掉（2026-09-20 第二轮）

上面两轮清的都是**"没人引用"**的死代码。到 2026-09-20 时 `smali_reach.py` 只剩 1 个
不可达类（还是故意留的 `ActivityAdapter`），但包里仍然躺着一堆 SDK ——
因为它们是**反过来被游戏自己的代码引用着**的：

| SDK | 谁吊着它 | 处置 |
|---|---|---|
| 微信分享（com.tencent.mm，8 类） | `GameShare` | 把 `GameShare` 改写成"保留接口、实现清空"的桩（见下） |
| 信鸽推送（com.tencent.android.tpush，3 类） | `XGAdapter` + `AppActivity` | 同上，`XGAdapter` 改桩 |
| 微博分享（com.sina.weibo，4 类） | `GameShare` | 同上 |
| 微博分享 Activity（com.kurogame，1 类） | `GameShare` | 直接删 |
| TalkingData 统计（com.tendcloud，1 类） | `AppActivity`（`init`/`onPause`/`onResume` 三处） | 删调用点 |

做法：**先删调用点 / 改桩，再删包**（顺序反了构建脚本的可达性闸门会拒绝删）。

⚠️ **桩必须保留原来的方法签名**，因为调用方有两路，都不在 smali 里：

* `assets/src/sdk/gameshare/gameshare.jsc` 用 `jsb.reflection.callStaticMethod` 点名
  `GameShare.shareToWeChat` / `shareToSina`；
* 两个 `lib/*/libcocos2djs.so` 的字符串表里也有 `org/cocos2dx/javascript/GameShare`、
  `shareToWeChat`、`shareToSina` —— **引擎侧也按名字找**。
* `assets/src/sdk/xg/xg.jsc` 点名 `XGAdapter.getDeviceToken/setTag/delTag/addNotification/
  clearNotifications/XGServiceEnabled`，`AppActivity` 调 `XGAdapter.init(Context)`。

签名一改就是运行时 `method not found`，所以只删包、不删方法。桩的行为：
`shareToWeChat/shareToSina` 走原实现那套 JS 回调
（`Cocos2dxJavascriptJavaBridge.evalString("sharegame.shareFailed(...)")`）→ 点分享弹一条失败提示；
`XGServiceEnabled()` 返回 false、`getDeviceToken()` 返回空串 → 推送相关红点自然不出现。

实测（同一份包，两台设备）：

* `classes.dex` **144,404 → 134,276 字节**（`GameShare` 37.6 KB smali → 3 KB、
  `XGAdapter` 8 KB → 2 KB，另删 17 个类）；smali 151 → **134 个类**；
* 运行时用 `jsb.reflection` 直接问一遍：`XGAdapter.getDeviceToken()` → `""`、
  `XGServiceEnabled()` → `false`、`GameShare.shareToWeChat(...)` 调用不抛异常；
* 模拟器（Android 9）+ 真机（PL16，Android 16）都冷启动正常、能登录，
  logcat 无 `ClassNotFound` / `NoSuchMethod` / `FATAL`。

原件备份在 `out/removed-smali-20260920/`（`game/` 不在 git 里，这是唯一的后悔药）。

> ⚠️ **坑：注释里别写斜杠形式的包名。** `smali_users_of()` 是**纯文本**匹配
> `com/sina` 这种斜杠前缀 —— 我在桩文件的注释里写了一句"微博（com/sina/weibo）"，
> 结果构建脚本认为"还有人引用"，那几个包怎么都删不掉。注释里统一写点号包名。

### 2b-3. 按「可达性」删单类（`DROP_SMALI_GROUPS`）

`android/support` 那批是「整包没人要」，这批是**散落的死类**。判据不能靠 grep，
要靠可达性：`script/smali_reach.py` 从真正的入口做闭包——

```
根 = AndroidManifest 声明的组件
   ∪ lib/*.so 里出现的类名（JNI 的 FindClass / jsb.reflection 的目标）
   ∪ assets 下的 js/js c 里出现的类名
```

算出来 6 组不可达，删掉 **1347 KB** smali：

| 组 | 大小 | 为什么是死的 |
|---|---|---|
| `com/cm/zcsmw/baidu/R*` | 1269 KB | 见下面那段 |
| `org/json/alipay/*` | 46 KB | 支付宝 SDK 自带的一份 `org.json`（和框架那个不是一回事） |
| `org/cocos2dx/lib/GameController*` | 20 KB | `Cocos2dxActivity` 并没 `implements GameControllerDelegate`，8 个文件只自引用 |
| `com/tendcloud/tenddata/TDGA*` | 5 KB | TalkingData 的数据类（主类留着，这几个没人调） |
| `wxapi/WXEntryActivity` + `mm/sdk/openapi/BaseResp` | 5 KB | 微信回调 Activity，**清单里压根没声明** |
| `org/cocos2dx/lib/Cocos2dxLuaJavaBridge` | 0.6 KB | Lua 桥——这游戏是 cocos2d-js，根本不走 Lua |

#### `R` 为什么是死代码（别以为是误删）

`R$*.smali` 不是普通常量表：字段全是**只声明不给值**
（`.field public static final dk_float_big_bubble_in:I`），值在 `<clinit>` 里调
`Lcom/quicksdk/apiadapter/baidu/ActivityAdapter;->getResId(名字, 类型)I` 现查现填
—— 百度/多酷那套加固手法。而 `ActivityAdapter` 是**我们自己写的桩**
（用 `Resources.getIdentifier` 顶替）。

所以 R 是「给已经被 `strip.py` 删掉的百度渠道 SDK 用的资源表」。SDK 没了就没人读
它的字段：三个清单组件、所有 quicksdk 桩、两个 `.so`、**13861 个 asset** 全搜过，
零引用。两个 `.so` 和 asset 里 `zcsmw` 只出现在编译器塞进去的源码路径
（`E:/code/zcsmw/engine/src/...`），拼不出 `com.cm.zcsmw.baidu.R$*` 这种名字。

**但 `ActivityAdapter` 故意留着**：就 1.2 KB，是那个渠道适配的说明性代码，
哪天把 R 从原版包解回来它还接着用。

#### 判定要按「组」而不是按文件

`R$anim.smali` 的注解里就写着 `value = Lcom/cm/zcsmw/baidu/R;`，按文件判会自己把
自己当成引用方，于是永远不敢删。所以 `DROP_SMALI_GROUPS` 每组是 `((glob…), 说明)`，
判定时用**组里所有类的名字**扫**组外**的文件。

删之前一律 `smali_reach` 式复查（`smali_users_of_classes()`），有人引用就 warn + 整组保留。

### 2b-3. 累计效果

| | 最初 | 现在 |
|---|---|---|
| smali | 1331 个 / 9.63 MB | **151 个 / 0.75 MB** |
| `classes.dex` | 1,508,336 B | **144,424 B（−90%）** |
| APK | 580,377,326 B | **579,857,134 B（−0.52 MB）** |

**smali 到头了。** `classes.dex` 压缩后才 63,777 B，全删也就 0.06 MB。
再想瘦包只能动 `assets/res`（`sound/jp` 98.7 MB 是最大的一块，
但它是**默认语音语言**，删了变中文语音）——那部分现在是红线，别碰。

删错了都能回来：原版包在 `game/original/zcsmw-original.apk`
（它的 `classes.dex` 2,983,976 B，上面每一个类都在里面），重新 `apktool d` 即可。

删错了也不要紧：原版包在 `game/original/zcsmw-original.apk`，
`out/removed-smali/` 里也留了一份现成的。

**但这条删法和「装桩删 SDK」是绑在一起的**：`android/support` 之所以是死代码，
是因为用它的那几个 SDK 已经被 `strip.py` 删了。哪天把百度钱包 / 多酷的 smali
加回来，就必须把 `DROP_SMALI` 里对应的那条一起去掉。

所以删之前会**再查一遍**（`smali_users_of()`）：smali 里只要还有一处
`Landroid/support/...` 的描述符，就打印 warn 并**保留不删** ——
宁可出一个大一点的包，也不要出一个跑起来才崩的包。

```
[build][warn] smali/android/support 现在还有 1 处引用，**保留不删**：
[build][warn]     smali\com\cm\FakeRef.smali
[build][warn]     这是新加回来的 SDK 依赖？那就把 DROP_SMALI 里对应那条去掉。
```

> ⚠️ 判定前缀**不能拿目录名去拼**。`smali/android/net` 要是拿 `android/net`
> 当前缀，会把满地的 `Landroid/net/Uri;`、`Landroid/net/wifi/WifiManager;`
> （框架类，在 `/system/framework` 里）全算成命中，于是永远不敢删。
> 所以 `DROP_SMALI` 每条是 `(目录, 前缀元组)`，`android/net` 只查它实际带的
> `android/net/http` 和 `android/net/compatibility`。

### 2c. 那 3 个引用 `android.support` 的布局不用管

`res/layout/` 里只有三个布局用到 `android.support.v4.view.ViewPager`：
`bd_wallet_sign_channel_list.xml`、`dk_dialog_back.xml`、
`dk_downloadmanager_activity.xml`。它们**在我删 `android/support` 之前就已经
inflate 不了了** —— 同一批布局还引用着 `com.baidu.wallet.base.widget.BdActionBar`
和 `com.duoku.platform.view.NewSegmentedLayout`，而这两个包早被 `strip.py` 删光了。

而且这 3 个布局的 R id 在 `R$layout` / `R$drawable` 里只是常量定义，
**R 类之外一处都没有被读**（搜过），所以没有任何代码会去 inflate 它们。

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

## ABI：打包哪几个 `.so`，以及能编哪几个

### 包里现有的三份（`game\lib\<abi>\libcocos2djs.so`）

| ABI | 来源 | 未压缩 | 包里（DEFLATE） |
|---|---|---|---|
| `armeabi` | 原版包自带 | 19.0 MB | 7.2 MB |
| `armeabi-v7a` | **2026-09-20 自己编**（`.\build.ps1 -Engine -Abi armeabi-v7a`） | 18.1 MB | 6.9 MB |
| `x86` | 自己编（MuMu 是 x86 模拟器，不走 houdini） | 24.0 MB | 8.3 MB |

`manifest` 里是 `android:extractNativeLibs="true"`，所以 `.so` 在包里是**压缩**存放的
（18 MB 的 so 只占 6.9 MB）—— 别拿 `libs/` 的字节数估包体积。

### 只带一部分：`--abis` / `-PackAbis`

```powershell
.\build.ps1 -Install -Serial <手机序列号> -PackAbis armeabi-v7a,armeabi   # 真机
.\build.ps1 -Install -PackAbis x86                                       # 模拟器
python script\build_apk.py --abis armeabi-v7a --out out\zcsmw-mod-arm.apk # 只要 v7a
```

* 留空（默认）= `game\lib` 里现有的**全带上**，行为和以前一样。
* 没选中的 ABI **不删**，挪到 `out\lib-abi-cache\<abi>\`；下次选上自动挪回来（幂等、能来回切）。
  —— `game\lib` 是 gitignore 的解包树，删了只能重新 `apktool d`，所以这里绝不真删。
* 要的 ABI 到处都没有时直接报错，并提示怎么编（`arm64-v8a` 会额外说明它为什么编不了）。

实测体积：三份全带 **558.2 MB** → 只带 arm 两份 **549.9 MB**（省 8.3 MB = x86 那份压缩后的大小）；
真机再用 `--abis armeabi-v7a` 单独打还能再省 7.2 MB（现代 ARM 设备都支持 v7a，`armeabi` 只剩兼容意义）。

### 设备实际会挑哪一份

Android 按包的 `lib/<abi>/` 和设备的 `abilist` 自己挑，**编出来的包不用管**；查结果：

```powershell
adb -s <设备> shell "dumpsys package com.cm.zcsmw.baidu | grep -iE 'primaryCpuAbi'"
```

实测（同一份「v7a + armeabi」的包）：

* 一加 PLZ110（Android 16，`abilist=arm64-v8a`、`abilist32` 空 → 走厂商 32 位兼容层）
  → **`primaryCpuAbi=armeabi-v7a`**（挑的是 v7a，不是 armeabi）；装上后冷启动正常、能登录、
  logcat 无 `UnsatisfiedLinkError` / `dlopen failed`。
* MEmu（Android 9，x86）→ `primaryCpuAbi=x86`。

### 能编哪些 ABI

编 `libcocos2djs.so` 要用 NDK r10e 把 cocos2d-x 3.6 + SpiderMonkey 33.1.1 一起编，
依赖一堆**预编译库**。cocos 官方依赖包在 3.6 那个年代**只有三套 ABI**
（`armeabi` / `armeabi-v7a` / `x86`），**arm64 是我们自己凑出来的**：

| ABI | 怎么来的 |
|---|---|
| `armeabi` | 原版包自带（引擎 `.so` 是自己重编的，依赖用官方 3.6 时代那套） |
| `armeabi-v7a` | 官方依赖包里有，`.\build.ps1 -Engine -Abi armeabi-v7a` 直接编 · NEON + 硬浮点 |
| `x86` | 同上（MEmu 跑原生 x86，不走 houdini） |
| **`arm64-v8a`** | **2026-09-20 自建**，见下 |

#### arm64-v8a 是怎么编出来的

```powershell
python script\build_arm64_deps.py          # ① 备依赖（幂等，一条命令）
.\build.ps1 -Engine -Abi arm64-v8a         # ② 编 .so（会自动先跑 ①）
.\build.ps1 -PackAbis arm64-v8a -Install -Serial <手机>   # ③ 只带 arm64 打包并安装
```

`build_arm64_deps.py` 干五件事，**其中 2、3 两个坑是踩出来的**：

1. **大部分库**：从官方依赖仓库的较新 tag（`v3-deps-140`）里取 arm64 版本
   （那里面 10 个库 + openssl 都有 arm64）+ SpiderMonkey 的 arm64 `libjs_static.a`。
   > 版本和 3.6 时代略有出入（png 1.6.16 vs 1.6.2、curl 7.52 vs 7.26、freetype 2.5.5 vs 2.5.0、
   > openssl 1.1 vs 1.0；jpeg/tiff/webp/zlib 一致）。实测编出来能跑（登录 + 主界面轮询都正常），
   > 但**这是唯一一处"版本没严格对齐"**的地方，要完全干净得把这几套也从源码编出来。
2. **chipmunk 必须自己编**：依赖包里是 **7.0**（`cpSpaceAddStaticShape` 等 6.x API 被删了），
   而 cocos2d-x 3.6 要 **6.2.1** → 从上游 `slembcke/Chipmunk2D` tag `Chipmunk-6.2.1` 编。
   ⚠️ 约束实现在 `src/constraints/*.c` **子目录**里，只编 `src/*.c` 会缺 26 个
   `cp*JointNew` / `cp*GetClass` 符号（链接期才炸，日志一大片 `undefined reference`）。
3. **libwebsockets 必须自己编 + 补一个结构体字段**：依赖包里是 **2.1.0**（API 改名成 `lws_*`），
   3.6 要 `v1.23-chrome32-firefox24` → 从上游拿那个 tag 编。三个坑：
   * 它要一个 CMake 生成的 `config.h`（仓库里没有）→ 按官方 `config.h.cmake` 写一份 Android 版
     （无 SSL + 带扩展，和 cocos 那份预编译 `.a` 的符号对得上）；
   * bionic 没有 BSD 的 `getdtablesize()`（libwebsockets 直接调）→ 补一个小 shim；
   * **`struct lws_context_creation_info` 里 cocos 比上游多 3 个字段**
     （`token_limits` / `http_proxy_address` / `http_proxy_port`）。引擎（`WebSocket.cpp`）
     是按 cocos 的头文件编译的，库里若按上游布局读 `info->gid` 就会读到 0 →
     去调 `setgid(0)` → **Android seccomp 直接 `SIGSYS` 打死进程**
     （真机 tombstone：`Cause: seccomp prevented call to disallowed arm64 system call 144`，
     帧在 `libwebsocket_create_context+564` → `setgid+12`）。所以编库前要把那 3 个字段补回
     **同一个位置**，让两边布局一致。
4. **头文件按 ABI 分**：SpiderMonkey 的 `js-config`（32 位 `JS_NUNBOX32` / 64 位 `JS_PUNBOX64`）
   和 curl 的 `curlbuild` 都得和链接的那份 `.a` 对得上 —— 脚本会挂出
   `spidermonkey/include/android64/js-config.h` 和 `curl/include/android64/`，
   并给两个 `Android.mk` 加上「arm64 用 64 位那套」的 `ifeq`。
   > 对不上会怎样：`js-config` 错 = jsval 表示错（ABI 直接崩）；curl 那个错 =
   > `curlrules.h` 的编译期自检当场报 `size of array '__curl_rule_01__' is negative`。
5. 把上面这些落到 `engine\src\...\external\**` 和那两个 `Android.mk` 里
   （`engine\src` 被 .gitignore 挡着不进仓库，所以**规则必须留在这个脚本里**）。

`build.ps1` 侧只要两处适配（已内置）：arm64 用 **toolchain 4.9**（r10e 的 arm64 没有 GCC 4.8）
和 `APP_PLATFORM=android-21`（`Application.mk` 里写的是 android-9，arm64 最低 21）。

实测（一加 PLZ110 / Android 16）：`primaryCpuAbi=arm64-v8a`、冷启动正常、能登录、
主界面轮询（`boss.getbosslist` / `sync.syncupclient`）正常、无 JS 报错 ——
**原生 64 位跑，不再走厂商的 32 位兼容层**。产物 `libcocos2djs.so` 21.6 MB。

---

## URL 怎么进客户端：默认**运行时改写**，老路子才是「等长替换」

`.jsc` 里的字符串是 **长度前缀**存储的（像 Pascal 字符串），
改长度会让后面的字节码整体错位 → 引擎直接崩。

所以**老路子**（`--patch-jsc-urls` / `build.ps1 -JscUrlPatch`）只能等长替换：

| 旧 | 长度 | 新（例） | 长度 |
|---|---|---|---|
| `cdn.shuangmawei.net` | 19 | `10.110.29.230:18080` | 19 ✅ |
| `http://114.55.66.97:16840` | 25 | `http://10.110.29.230:8080` | 25 ✅ |

**这就倒推出了老路子的硬约束**：host 必须 **13 个字符**、CDN 端口 **5 位**、
登录端口 **4 位**。长度不匹配时脚本直接报错退出，不会产出坏包。
⚠️ 还有个更阴的坑：那几个文件是**就地改写**的，换地址时替换逻辑
「找不到旧串」会**静默跳过** —— 表现是新包还带着旧地址（实测踩过：DHCP 换了 IP）。

**默认（现在的做法）完全不碰 jsc**：打包时先按 `game/original/zcsmw-original.apk`
把带地址的文件恢复成原始串，地址交给 `patch.js` 的 `URL-REWRITE` 在运行时改写
（拦 `cc.loader.getXMLHttpRequest()` 与 `window.WebSocket`）。
于是没有长度约束（`127.0.0.1` 也行）、打包幂等、换服务器只要重打包 assets。

`project.manifest` 是**纯文本 JSON**，两种路子下都按 JSON 重写（只换 host、
路径原样保留）—— 它走原生 curl，JS 层拦不到，必须在打包时改对。

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

## ⚠️ 脚本顺序：`patch_smali.py` 必须排在 `strip.py` **之后**

`strip.py` 会把 `smali\com\quicksdk` **整个删掉**，再按 `needed.json` 重新生成桩类
（方法体是空的 `return-void`）。所以顺序反了就等于白改：

```
patch_smali.py     →  往 com\quicksdk\Sdk.smali 里写真实实现
strip.py           →  删掉 com\quicksdk，重新生成空桩        ✗ 修复被冲掉
```

正确顺序：`modernize.py` → `strip.py` → `gen_native_stubs.py` → **`patch_smali.py`** → `build_apk.py`。

`build.ps1` 里不含 `strip.py`，所以不受影响；但照下面「完整重建命令」跑要按这个顺序。
（`strip.py --dry-run` 就能看到它准备删 `smali\com\quicksdk`。）

---

## ⚠️ `.ps1` 必须带 UTF-8 BOM（不只是 `build.ps1`）

Windows PowerShell 会把**无 BOM** 的 UTF-8 脚本按 ANSI 读（中文系统 = GBK），
中文注释/字符串被误解析，**顺手把引号吃掉**，于是报一片 `Unexpected token` /
`Missing closing '}'`。

最坑的不是报错，而是：**解析失败 = 脚本一行都没执行**。
（曾拿一个解析失败的验证脚本当成"跑过了、只是没效果"，白查一轮。）

* `build.ps1` 保持带 BOM。改它别用会丢 BOM 的工具；用 Python 时 `encoding="utf-8-sig"` 读写。
* 临时验证脚本**直接写纯 ASCII 源码**最省心 —— 中文只出现在输出的数据里，不写进源码。

#### ⚠️ 有些编辑器 / AI 工具会**悄悄吞掉 BOM**

实测：**「替换文本」类的编辑操作是按 UTF-8（无 BOM）整份回写的** ——
改一句注释就能把 BOM 弄丢。丢完之后本机可能还能跑（有的环境会容错），
换台机器 / 换个 PowerShell 版本就炸，而且**报的是一大片语法错**，看不出根因。

**每次改完 `.ps1` 都验一下**：

```powershell
$b = [System.IO.File]::ReadAllBytes((Resolve-Path build.ps1))
if ($b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF) { "BOM 在" }
else {
    $t = [System.IO.File]::ReadAllText((Resolve-Path build.ps1))
    [System.IO.File]::WriteAllText((Resolve-Path build.ps1), $t,
        (New-Object System.Text.UTF8Encoding($true)))     # $true = 带 BOM
}
```

`.gitattributes` 里的 `* text=auto eol=lf` **不会**帮你补 BOM（git 不碰 BOM），
所以这个只能靠改完自己查。

### 附带：PowerShell 的别名优先级高于函数

`ps` / `ls` / `cat` / `rm` 都是内置别名（→ `Get-Process` 等），**同名函数盖不过别名**。
曾把辅助函数命名成 `PS`，结果打印出来的是**整张 Windows 进程表**而不是模拟器进程。
给脚本函数起名避开这些别名。

### 附带：量出来的"脚本坏了"，先怀疑量法

`& python x.py --help 2>&1 | Select-Object -First 1` —— `-First 1` 会提前掐断管道并
**杀掉上游进程**，于是 Python 抛 BrokenPipe traceback、`$LASTEXITCODE` 变 `-1`，
看着像"脚本坏了"，其实脚本是好的（`repl.py` / `jsd.py` 就这么被误判过）。
要拿退出码就别截断管道：`$all = (& ... | Out-String)`。

---

## 一键构建（`build.ps1`）为什么要跑 `patch_smali.py`

`build.ps1` 原本第 3 步只跑 `patch_js_debugger.py`，**从不调用 `patch_smali.py`**
—— 而本文档一直把 `patch_smali.py` 列为构建链的一环。后果：重新 `apktool d`
解包后跑一键构建，Java 层补丁（登录 + 退出弹窗）会**静默丢失**，没有任何提示。

现在补成 **Step 3b**，位置在 Step 4（打包）之前 —— 必须之前，因为 apktool 是从
`game\smali` 编 `classes.dex` 的；同时显式设了 `$env:GS_APK_DIR = $Game`
（`patch_smali.py` 的默认路径是写死的，换机器会找不到解包目录）。

---

## 项目重排留下的两个脚本 bug（已修）

`script/` 下的脚本靠往上找 `_paths.py` 自举 `sys.path`。重排项目时有两处搞坏了，
**表现为脚本从任何目录都跑不起来**：

| 脚本 | 症状 | 原因 |
|---|---|---|
| `script\sdk_strip\strip.py` | `NameError: name '_paths' is not defined` | `BASE_DIR = _paths.SERVER` 写在了自举代码**上面**（第 26 行 vs 第 35 行） |
| `script\sdk_strip\native_stubs.py` | `NameError: name 'sys' is not defined` | 用了 `sys.path.insert` 却**没 `import sys`**；连带 `gen_native_stubs.py`（import 它）一起挂 |

写新脚本照抄现成的自举块，**顺序必须是**：`import` → `HERE` → 自举 → 才轮到
`_paths.X` 和本地模块的 import。

---

## 路径配置

全部可用环境变量覆盖，方便换机器：

| 变量 | 默认 | 说明 |
|---|---|---|
| `GS_APK_DIR` | `E:\code\zcsmw\game` | apktool 解包目录 |
| `GS_WORK_DIR` | `E:\code\zcsmw\out` | 产物目录（也放 keystore） |
| `GS_APKTOOL` | `E:\code\zcsmw\script\apktool.bat` | apktool |
| `GS_BUILD_TOOLS` | `D:\Android\android-sdk\build-tools\36.0.0` | zipalign / apksigner |
| `GS_JAVA_HOME` | `D:\java\zulu17...` | JDK |

> 注意：原版 APK **不在** `E:\code\zcsmw\game.apk`，而是
> `game\original\zcsmw-original.apk`（仓库里唯一一份，别删）。
> 早期文档里的 `GS_APK_SRC` 环境变量**没有任何代码在用**，已从表里去掉。

---

## 完整重建命令

```powershell
# 在仓库根 E:\code\zcsmw 下跑。script\ 下的脚本自带 _paths 自举，从哪个目录调都行。

# 1) 四个补丁（幂等）—— ⚠️ patch_smali.py 必须最后，strip.py 会重建 com\quicksdk 的桩
python server\client\modernize.py
python script\sdk_strip\analyze.py --json script\sdk_strip\needed.json   # SDK 没动过可跳过
python script\sdk_strip\strip.py
python script\sdk_strip\gen_native_stubs.py
python server\client\patch_smali.py

# 2) 改 assets + 打包 + 签名（一步）
python script\build_apk.py --host 10.110.29.230

# 3) 装
adb install -r -d E:\code\zcsmw\out\zcsmw-mod-signed.apk
```

`build_apk.py` 常用参数：

| 参数 | 说明 |
|---|---|
| `--host` / `--port` / `--login-port` | 服务端地址。`--host` 留空 = 读服务端配置（`gamesrv/config.py` 的 `PUBLIC_HOST`） |
| `--print-host` | 只打印上面那个默认地址然后退出（`build.ps1` 用它，保证只有一处配置） |
| `--patch-jsc-urls` | 【老路子，默认关】等长替换 jsc；开了才有 13 字符/端口位数那套约束 |
| `--no-probe` | 正式包：不带 probe.js |
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
