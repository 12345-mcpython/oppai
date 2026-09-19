<#
一键构建 —— 战场双马尾私服

    .\build.ps1                     # 只打 APK（用现成的引擎 .so）
    .\build.ps1 -Engine             # 先重编引擎 libcocos2djs.so，再打 APK
    .\build.ps1 -Install            # 打完装到模拟器
    .\build.ps1 -Install -Launch    # 装完直接启动
    .\build.ps1 -NoProbe            # 出正式包（不带 probe.js / REPL / 调试台）
    .\build.ps1 -Engine -Abi x86    # 只重编 x86 引擎

为什么默认两个 ABI 都编：MuMu 是 **x86** 模拟器，只带 armeabi 的包会被
houdini（ARM->x86 二进制翻译层）接管，实测会在新手引导那段代码里被
houdini 自己 trap 掉（tombstone 里唯一一帧永远是 /system/lib/libhoudini.so）。
包里同时放 lib/x86 和 lib/armeabi，Android 按 abilist32 = x86,armeabi-v7a,armeabi
自动优先选 x86，原生跑就不会再走 houdini。

产物统一落在 out\ 下：

    out\zcsmw-mod-signed.apk        最终可安装的 APK
    out\zcsmw-mod.apk               apktool 打出来的未签名包
    out\shots\                      截图
    out\<abi>_libcocos2djs.so       引擎产物（重编后），abi = armeabi / x86
    out\engine-build-<abi>.log      引擎编译日志

⚠️ 这个文件必须是 **UTF-8 带 BOM**：Windows PowerShell 5.1 会把无 BOM 的
   UTF-8 脚本按 ANSI 读，中文注释直接把它读崩（`Unexpected token` 一大片）。
#>
param(
    [switch]$Engine,
    [switch]$Install,
    [switch]$Launch,
    [switch]$NoProbe,
    [string[]]$Abi = @("armeabi", "x86")
)

$ErrorActionPreference = "Stop"

$Root    = $PSScriptRoot
$Game    = Join-Path $Root "game"
$Server  = Join-Path $Root "server"
$Script  = Join-Path $Root "script"
$Eng     = Join-Path $Root "engine"
$Out     = Join-Path $Root "out"

$Adb     = if ($env:GS_ADB) { $env:GS_ADB } else { "D:\Android\android-sdk\platform-tools\adb.exe" }
$Serial  = if ($env:GS_ADB_SERIAL) { $env:GS_ADB_SERIAL } else { "127.0.0.1:21503" }
$Pkg     = "com.cm.zcsmw.baidu"
$Act     = "org.cocos2dx.javascript.SplashActivity"

function Step($n, $msg) { Write-Host ""; Write-Host "=== [$n] $msg" -ForegroundColor Cyan }
function Ok($msg)       { Write-Host "    $msg" -ForegroundColor Green }
function Warn($msg)     { Write-Host "    $msg" -ForegroundColor Yellow }

# 用 python 的路径自举来解析各个目录，免得两边各写一份
$env:PYTHONIOENCODING = "utf-8"

if (-not (Test-Path $Game))   { throw "找不到游戏包目录: $Game" }
if (-not (Test-Path $Script)) { throw "找不到脚本目录: $Script" }

# ---------------------------------------------------------------------------
Step 1 "环境自检"
# ---------------------------------------------------------------------------
foreach ($p in @($Game, $Server, $Script, $Eng, $Out)) {
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Force -Path $p | Out-Null }
}
$apktool = Join-Path $Script "apktool.bat"
if (-not (Test-Path $apktool)) { throw "找不到 apktool：$apktool" }
& python -c "import sys; print(sys.version.split()[0])" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "python 不在 PATH 里" }
Ok "python / apktool / 目录都在"

# ---------------------------------------------------------------------------
if ($Engine) {
    Step 2 "重编引擎 libcocos2djs.so（$($Abi -join ', ')）"
    $ndk   = Join-Path $Eng "ndk\android-ndk-r10e"
    $jsb   = Join-Path $Eng "src\cocos2d-js\frameworks\js-bindings"
    $cocos = Join-Path $jsb "cocos2d-x"
    $app   = Join-Path $Eng "build\oppai-engine"
    if (-not (Test-Path $ndk)) { throw "找不到 NDK：$ndk" }

    # ⚠️ 直接调 ndk-build，不要走 engine\build\build.ps1：
    # 那个脚本里有 $ErrorActionPreference = "Stop"，而 ndk-build 把编译警告
    # 写 stderr，PowerShell 会当成终止错误直接中断构建。
    # 引擎源码补丁。这三个都是幂等的（已打过就打印「跳过」），所以每次 -Engine
    # 都重跑一遍，保证「照着一份干净源码也能一键编出同样的 .so」。
    # 其余补丁（add_*.py / fix_*.py）是一次性的，结果已经落在 engine\src 里了，
    # 清单见 engine\README.md。
    foreach ($fx in @("enable_js_debugger.py", "fix_js_log.py", "fix_null_texture.py")) {
        $fp = Join-Path (Join-Path $Eng "build") $fx
        if (-not (Test-Path $fp)) { throw "找不到引擎补丁脚本 $fp" }
        & python $fp
        if ($LASTEXITCODE -ne 0) { throw "$fx 退出码 $LASTEXITCODE" }
    }

    $env:NDK_MODULE_PATH = "$jsb;$cocos;$cocos\external;$cocos\cocos"
    foreach ($a in $Abi) {
        # x86 有原生 64 位原子指令，不需要 libatomic（NDK r10e 也没给 x86 编）。
        # 命令行上传的 APP_* 会覆盖 Application.mk 里的同名设置。
        $ld  = if ($a -eq "armeabi") { "-latomic" } else { "" }
        $log = Join-Path $Out "engine-build-$a.log"
        & "$ndk\ndk-build.cmd" -j24 -C $app NDK_TOOLCHAIN_VERSION=4.8 NDK_DEBUG=0 APP_ABI=$a "APP_LDFLAGS=$ld" *> $log
        if ($LASTEXITCODE -ne 0) {
            Warn "编译失败，看 $log 的最后 30 行："
            Get-Content $log -Tail 30 | ForEach-Object { Write-Host "      $_" }
            throw "ndk-build($a) 退出码 $LASTEXITCODE"
        }
        $so = Join-Path $app "libs\$a\libcocos2djs.so"
        if (-not (Test-Path $so)) { throw "编译完没找到 $so" }
        New-Item -ItemType Directory -Force -Path (Join-Path $Game "lib\$a") | Out-Null
        Copy-Item $so (Join-Path $Out "${a}_libcocos2djs.so") -Force
        Copy-Item $so (Join-Path $Game "lib\$a\libcocos2djs.so") -Force
        Ok ("$a 引擎编好了：{0:N2} MB" -f ((Get-Item $so).Length / 1MB))
    }
} else {
    Step 2 "引擎（跳过，用现成的 .so）"
    Warn "要重编引擎加 -Engine"
}

# ---------------------------------------------------------------------------
Step 3 "调试器 JS 换成明文（不重编引擎就能改调试器）"
& python (Join-Path $Script "patch_js_debugger.py")
if ($LASTEXITCODE -ne 0) { Warn "patch_js_debugger 失败，继续" } else { Ok "ok" }

# ---------------------------------------------------------------------------
Step 4 "打包 APK（apktool 完整打包 + zipalign + 签名）"
$buildArgs = @((Join-Path $Script "build_apk.py"))
if ($NoProbe) { $buildArgs += "--no-probe" }
& python @buildArgs
if ($LASTEXITCODE -ne 0) { throw "build_apk.py 退出码 $LASTEXITCODE" }
$apk = Join-Path $Out "zcsmw-mod-signed.apk"
if (-not (Test-Path $apk)) { throw "没找到产物 $apk" }
Ok ("APK: $apk  ({0:N1} MB)" -f ((Get-Item $apk).Length / 1MB))

if ($NoProbe) {
    Warn "这是正式包（--no-probe）：没有 probe.js / REPL / 调试台。"
    # 之前这里写的是「没有…引擎调试器」，实测是错的：enable_js_debugger.py 是
    # **编译期**补丁（ScriptingEngine 里 JS_SetDebugMode + 起 5086 监听），
    # 已经烙进 libcocos2djs.so，-NoProbe 只动 APK 里的 assets，关不掉它。
    # 验证过：正式包装上后 `adb forward tcp:5086 tcp:5086` 再 jsd.py tabs
    # 照样能连上（会列出 Hello Cocos2d-X JSB）。真要关得重编引擎，把
    # engine\build\enable_js_debugger.py 从 build.ps1 的 $fixes 里去掉再 -Engine。
    Warn "注意：引擎 JS 调试器（5086）是编进 .so 的，这个包里**依然开着**。"
}

# ---------------------------------------------------------------------------
if ($Install) {
    Step 5 "安装到模拟器"
    & $Adb connect $Serial | Out-Null
    & $Adb install -r -d $apk
    if ($LASTEXITCODE -ne 0) { throw "adb install 失败" }
    Ok "装好了"
    & $Adb forward tcp:5086 tcp:5086 | Out-Null
    Ok "端口转发 5086 -> 引擎调试器"
} else {
    Step 5 "安装（跳过）"
    Warn "手动装： adb install -r -d `"$apk`""
}

if ($Launch) {
    Step 6 "启动游戏"
    & $Adb connect $Serial | Out-Null
    & $Adb shell am force-stop $Pkg
    & $Adb shell am start -n "$Pkg/$Act" | Out-Null
    Ok "已启动（等 20 秒左右进主界面）"
}

Write-Host ""
Write-Host "全部完成。" -ForegroundColor Green
Write-Host "  起服务端 : python script\serve.py"
Write-Host "  调试台   : http://127.0.0.1:18080/devtools"
Write-Host "  引擎调试 : python script\jsd.py repl"
