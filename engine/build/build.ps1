# 编译 libcocos2djs.so
#
#   .\build.ps1              # 用 Application.mk 里的 APP_ABI
#   .\build.ps1 -Abi x86     # 临时指定 ABI
#
# 命令照抄 cocos2d-x/build/android-build.py：
#   ndk-build -jN -C <app> NDK_TOOLCHAIN_VERSION=4.8 NDK_DEBUG=0 NDK_MODULE_PATH=...
# 其中 NDK_MODULE_PATH = <js-bindings>;<cocos2d-x>;<cocos2d-x>/external;<cocos2d-x>/cocos

param(
    [string]$Abi = "",
    [switch]$Clean,
    [int]$Jobs = 0
)

$ErrorActionPreference = "Stop"

$Move    = Split-Path -Parent $PSScriptRoot   # E:\code\zcsmw\engine（本脚本在 move\build\ 下，只往上一层）
$NDK     = Join-Path $Move "ndk\android-ndk-r10e"
$JSB     = Join-Path $Move "src\cocos2d-js\frameworks\js-bindings"
$COCOS   = Join-Path $JSB  "cocos2d-x"
$APP     = Join-Path $PSScriptRoot "oppai-engine"

if (-not (Test-Path $NDK))   { throw "找不到 NDK: $NDK" }
if (-not (Test-Path $COCOS)) { throw "找不到 cocos2d-x: $COCOS" }

$jobs = if ($Jobs -gt 0) { $Jobs } else { [Environment]::ProcessorCount }

# NDK_MODULE_PATH：让 $(call import-module,bindings) 能找到 js-bindings/bindings
$env:NDK_MODULE_PATH = "$JSB;$COCOS;$COCOS\external;$COCOS\cocos"

Write-Host "NDK      : $NDK"
Write-Host "APP      : $APP"
Write-Host "ABI      : $(if ($Abi) { $Abi } else { '(Application.mk)' })"
Write-Host "Jobs     : $jobs"
Write-Host "MODULE_PATH: $env:NDK_MODULE_PATH"
Write-Host ""

$args = @("-j$jobs", "-C", $APP, "NDK_TOOLCHAIN_VERSION=4.8", "NDK_DEBUG=0")
if ($Abi) { $args += "APP_ABI=$Abi" }
if ($Clean) { $args = @("-C", $APP) + "clean" }

$sw = [System.Diagnostics.Stopwatch]::StartNew()
& "$NDK\ndk-build.cmd" @args
$code = $LASTEXITCODE
$sw.Stop()

Write-Host ""
Write-Host ("ndk-build 退出码 {0}，耗时 {1:N1} 秒" -f $code, $sw.Elapsed.TotalSeconds)

# 把产物收集到 out/
if ($code -eq 0) {
    $out = Join-Path $Move "out"
    New-Item -ItemType Directory -Force -Path $out | Out-Null
    Get-ChildItem (Join-Path $APP "libs") -Recurse -Filter "libcocos2djs.so" -ErrorAction SilentlyContinue | ForEach-Object {
        $dest = Join-Path $out "$($_.Directory.Name)_libcocos2djs.so"
        Copy-Item $_.FullName $dest -Force
        "产物: {0}  ({1:N2} MB)  -> {2}" -f $_.Directory.Name, ($_.Length/1MB), $dest
    }
}
exit $code
