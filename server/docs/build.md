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

### 2b-2. 按「可达性」删单类（`DROP_SMALI_GROUPS`）

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
