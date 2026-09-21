# docs/ —— 项目文档

一篇一句话，按「建议的阅读顺序」排。**任务导向的完整索引在
[`../README.md`](../README.md) 的「文档地图」一节**（只有那一份是全的，这里只是目录速览）。

| 文档 | 一句话 |
|---|---|
| [`overview.md`](overview.md) | **全景**：一句话、端到端链路、系统分层、关键逆向成果、现状与待办 |
| [`pitfalls.md`](pitfalls.md) | **坑速查**：症状 → 真正原因 → 在哪个文件（14 条 + 一张症状总表） |
| [`differences.md`](differences.md) | **与原版的差异总账**：A 不得不改 / B 私服取舍 / C 还没做 / D **数值是猜的** |
| [`protocol.md`](protocol.md) | 协议逐项细节 + 反汇编证据（CDN / 网关 / oauth / WS 握手 / 业务包 / 各玩法模块） |
| [`build.md`](build.md) | 打包逻辑、ABI、完整重建命令、产物与解包目录的取舍 |
| [`devtools.md`](devtools.md) | 浏览器调试台：六个面板、架构取舍、怎么加字段 |
| [`engine-debug.md`](engine-debug.md) | 引擎层调试：自带远程 JS 调试器怎么打开、四个坑、复现清单 |
| [`reverse-engineering.md`](reverse-engineering.md) | 逆向方法：jsc 反汇编器原理 + 运行时探测手法 + 排障套路 |
| [`decompile.md`](decompile.md) | jsc → js 反编译器：怎么做、三个关键字节码形状、已知问题 |

## 不在本目录的几份

| 文档 | 位置 | 一句话 |
|---|---|---|
| 从零复刻操作手册 | [`../REPRODUCE.md`](../REPRODUCE.md) | 环境、要自备的外部资源、逐步操作 + 每步验证点 |
| 脚本索引 | [`../script/README.md`](../script/README.md) | 六类脚本每个一句话 + 加新玩法模块的流程 + `out/` 哪些能删 |
| 引擎移植过程 | [`../engine/README.md`](../engine/README.md) | 版本确认、里程碑、踩过的坑 |
| 引擎补丁清单 | [`../engine/ENGINE_PATCHES.md`](../engine/ENGINE_PATCHES.md) | 16 个补丁的证据链与复现脚本 |

> 写文档时的两条约定（踩过才知道）：
> ① **同一件事只留一份权威描述**，别处只给指路 —— 这份仓库原来有三份「文档地图」、
> 两份「构建命令清单」，各自过期到互相矛盾；
> ② 结论要带**证据和现象**（日志、tombstone 帧、实测数字），别只写"已修复"。
>
> 改完文档跑 `python script\check_docs.py` —— 它会查链接、目录锚点、旧路径残留，
> 以及新文档有没有被 README 的文档地图收录。
