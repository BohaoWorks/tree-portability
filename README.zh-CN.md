# tree-portability

**搬文件之前，先检查整个目录树的命名冲突和路径预算。**

离线 Python 命令行工具：扫描指定目录，或读取路径清单，按保守的 Windows、
exFAT、跨平台规则检查名称，生成确定性的重命名**计划**。父目录改名会同步反映到
所有子路径；目标目录前缀也计入总长度。

**不会重命名、复制或读取源文件内容。运行时无第三方依赖，不联网。**

[English](README.md) · [实际终端示例](examples/demo.txt) ·
[完整 JSON](examples/demo.json) · [设计与边界](docs/design.md)

## 为什么不只替换非法字符？

把 `A:B.txt` 改成 `A_B.txt` 很容易，但 `A_B.txt` 可能已经存在；`Foo` 与 `foo`
也可能是两个不同的父目录。这个工具检查完整树结构，预留现有同级名称，先规划父目录，
再规划后代，并验证最终名称空间和路径长度。

可发现：`CON.txt` 等保留名、非法字符、末尾空格/句点、UTF-8/UTF-16 超限、
大小写/NFC 别名、文件与目录的别名冲突。`A:B.txt` 与 `A：B.txt` 会被提示可能存在
传输编码冲突，不会擅自把全角字符变成半角字符。这不是 rclone 编码模拟器或同步插件。

## 使用

需要 Python **3.10 或更高版本**。在源码目录运行：

```sh
python -m pip install .
tree-portability --path-list examples/paths.txt --destination-prefix D:/Team/Backup --path-budget 70
```

扫描明确指定的目录，输出 JSON：

```sh
tree-portability --root ./photos --profile windows --destination-prefix D:/Photos --json
tree-portability --root ./photos --output photos-plan.json
```

无需安装的 POSIX shell 用法：

```sh
PYTHONPATH=src python -m tree_portability --path-list examples/paths.txt
```

PowerShell 可先安装，或先执行 `$env:PYTHONPATH = 'src'`。运行本身离线，安装时可能
下载 setuptools 构建依赖。本项目不要求发布到包注册表；请安装本源码，不要把注册表
上的同名包自动当成本项目。

目标前缀只是文本，工具**不会访问或扫描目标目录**。目标已有内容必须纳入你准备的完整
清单，或使用空目标。未纳入清单的目标名称冲突无法检测。

## 结果和退出码

- **0**：清单和计划完整，无问题或变更
- **1**：计划完整，但存在警告或改名建议
- **2**：清单/计划不完整、参数错误或输出失败

`mapping` 只列变更，`entries` 包含全部节点和推断出的目录。父目录变化会体现在全部
后代路径中。映射是供审阅的数据，**不是可直接执行的文件操作顺序**。

`--json` 将完整报告打印到标准输出；`--output NEW.json` 另存 JSON，拒绝覆盖已有文件，
也拒绝写到扫描的源目录内部。请检查 `inventory_complete`、`plan_complete` 和
`verification`。完整只表示满足本工具的模型，并不保证真实传输一定成功。
不完整报告可能包含部分有用建议，但不能当作完整计划使用。

## 输入与规则

清单必须是 UTF-8，每行一条相对路径，使用 `/` 分隔，末尾 `/` 表示目录。
父目录自动补全，空行忽略但仍计入读取上限。组件内的反斜杠是文件名字符，不是分隔符。
绝对路径、`.`/`..`、空组件、NUL 和源文件/父目录类型矛盾都会报错。
换行符名称无法由逐行清单精确表示，请用目录扫描。

| 规则 | 单个名称上限 | 默认完整路径预算 |
| --- | --- | --- |
| `windows` | 255 个 UTF-16 单元 | 259 个 UTF-16 单元 |
| `exfat` | 255 个 UTF-16 单元 | 259 个 UTF-16 单元 |
| `portable`（默认） | 255 个 UTF-8 字节且 255 个 UTF-16 单元 | 两种单位都不超过 240 |

`--path-budget N` 可覆盖总预算（1–32767），包含前缀和分隔符。
这是**保守的互操作规则**，不是 NTFS/exFAT/Win32 的精确仿真。
exFAT 在这里采用 Windows 兼容策略，259 不是 exFAT 文件系统的固有上限。
NFC + Unicode casefold 可能比实际文件系统更严格，不支持扩展设备路径或 8.3 别名。

## 安全边界

- 仅读取目录元数据，不读取源文件内容，不跟随观察到的符号链接、junction 或 reparse point
- 如果源根目录或其祖先是链接，会拒绝扫描；链接及特殊文件不生成建议，计划标为不完整
- 权限、遍历和截断错误明确报告，不把未完成扫描显示为干净结果
- 扫描不是原子快照。不要扫描被不可信进程同时修改的目录；路径替换竞态不在 MVP 防护范围内
- 默认上限：100,000 条记录/节点、128 层、单条路径 16,384 字符
- `--max-entries` 可设为 1–1,000,000，内存消耗随规模增长
- 生成名称至少需要 9 个单位；预算不足会明确标记无法规划，不宣称其他算法也一定无解
- 不提供实际改名、复制、撤销、内容去重或目标扫描
- 报告包含相对文件名和你输入的目标前缀，分享前请检查隐私

## 开发验证

```sh
python -m pip install .
python -m unittest discover -s tests -v
python examples/make_demo.py --check
```

CI 配置覆盖 Linux/Windows 和 Python 3.10/3.12/3.14，并验证 wheel 安装与示例一致性。
[验证记录](docs/verification.md) 分别列出本地检查与初始发布提交已通过的全部 6 个远端 CI 检查，并保留实际文件系统迁移尚未验证的限制。

## 背景

[Syncthing #10476](https://github.com/syncthing/syncthing/issues/10476) 提出缩短名称并保存
映射日志；[#9395](https://github.com/syncthing/syncthing/issues/9395) 报告非法字符导致传输
失败；[rclone #9976](https://github.com/rclone/rclone/issues/9976) 报告不同名称经编码后冲突。
这些公开反馈解释项目的动机，不表示上游背书，也不表示本工具修复了上游问题。

MIT 许可证。欢迎用最小合成样例提 issue，请不要公开真实私有目录清单。
