<p align="center">
  <img src="assets/icons/retirement_pet.png" width="112" alt="RetirementPet 图标">
</p>

# RetirementPet 退休倒计时桌宠

[![Tests](https://github.com/Bpig-C/RetirementPet/actions/workflows/tests.yml/badge.svg)](https://github.com/Bpig-C/RetirementPet/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows%2011-0078D4.svg)](docs/ALPHA_TRIAL.md)

RetirementPet 是一个本地优先的 Windows 透明桌宠：退休倒计时是稳定的基础
能力，角色、动作和待办是其上可演进的模块。当前目标是可回滚的 **Alpha 初步
试用版**，不是已经完成全部真机与长期认证的正式发行版。

[查看 Alpha 发布页](https://github.com/Bpig-C/RetirementPet/releases) ·
[阅读试用说明](docs/ALPHA_TRIAL.md) ·
[参与贡献](CONTRIBUTING.md)

> **公开发行状态：源码预览。** 现有 1.1.1 本机构建没有随包携带完整第三方许可
> 材料，因此不会直接上传到 GitHub Release。发布页自动生成的 Source code 压缩包
> 也不是可双击运行的 Windows 程序；便携版必须从公开提交重新构建并通过许可门禁。

文档从 [文档中心](docs/README.md) 进入。当前实现与门禁见
[实施状态](docs/IMPLEMENTATION_STATUS.md)，版本变化见
[项目更新](docs/PROJECT_UPDATES.md)，角色创作从
[角色库扩展指南](docs/CHARACTER_LIBRARY_GUIDE.md) 开始。

当前实现包括：

- 无边框、透明、可拖动的桌宠窗口；托盘可恢复置顶、穿透、隐藏和位置；
- 退休倒计时，以及工作、休息、吃饭、健身、会议、音乐和随机动作决策；
- PetPack 1.0 角色协议、不可变角色库和两阶段安全切换；
- 首次手动启动的一次性角色选择：保留官方退休猫，或在固定身份校验后直接启用
  随附的半写实本地预览；开机启动不弹出面板、不抢焦点；
- 官方角色包 1.0.1 显示管线：512px 角色正文直接映射屏幕像素，动作字幕、气泡和通用效果由
  引擎实时绘制；缺少中文字形时使用确定的 ASCII 文案，不显示缺字方框；
- 八页控制面板，页面按需创建并保留到面板关闭，同一时间只激活并显示一页；
- 本地待办：短/中/长期、最多六层任务树、同级上移/下移、截止日期、完成/恢复和
  单焦点；桌宠/托盘右键可直接打开，Windows 默认全局快捷键为 `Ctrl+Alt+T`；
- 当前用户开机自启、单实例、异常恢复和本地日志；
- 默认零联网、无遥测，不记录按键内容。

完整设计见 [v2 总规范](docs/DESIGN_V2.md)，首次运行按
[Alpha 试用说明](docs/ALPHA_TRIAL.md)操作。项目尚未完成的真机门禁和角色库后续
工作分别记录在[实施状态](docs/IMPLEMENTATION_STATUS.md)与
[未来工作](docs/FUTURE_WORK.md)。

## 当前 Alpha 边界

应用 1.1.1 的私有参考构建已取得 `ALPHA-GO`，同时保持
`PRODUCTION-NO-GO:PENDING_ENVIRONMENT`。精确身份留在私有追加式证据域，不能
继承给公开源码提交或后续 artifact。

- 安全回退仍只自动启用一个官方退休猫。升级到 1.1.1 后，下一次手动启动会显示
  一次性角色选择区；用户可继续使用官方猫，或明确确认本地内容边界后一键导入并
  切换到半写实退休猫 `0.1.1`。关闭而不选择不会写完成标记；开机启动留到下一次
  手动启动再询问。
- 启动 1.1.1 前必须先从托盘退出仍在运行的旧版。所有版本共用单实例名称；若
  1.1.0 仍在运行，双击 1.1.1 只会唤起旧进程，看到的仍会是旧简笔猫，也不会有
  新的一次性角色选择。
- “布局”和“可见性”下拉框目前是明确禁用的预览；文案页只做安全预览，均不
  保存、也不改变桌宠。
- Todo 是单机轻量记录工具，不含账号、云同步、协作、附件或复杂提醒。
- 音乐只读取用户选择的本地文件；会议模式是本地手动/时间段规则，不连接日历。
- Alpha 允许在当前机器初试；24 小时稳定性、多机性能预算、干净 VM、真实注销
  自启、混合 DPI、睡眠/唤醒仍是 Production 门禁。

从 1.0.0 升级时，程序只会自动迁移精确匹配的旧内置退休猫；迁移使用与手动换角
相同的准备、首帧验证和原子提交流程。第三方角色不会被替换，失败时继续使用旧
角色。旧官方包仍随程序保留用于精确恢复，但角色菜单只展示新版。

## 源码运行

仓库可克隆到任意普通开发目录；源码、虚拟环境和构建产物应留在同一个项目树内，
不要依赖维护者机器的盘符或个人目录。

```powershell
git clone https://github.com/Bpig-C/RetirementPet.git
Set-Location RetirementPet
# 仅在首次建立源码开发环境且 .venv 不存在时执行下一行
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_dev.ps1
```

本项目固定使用 PySide6 6.8.3。若再次遇到 `QtCore: DLL load failed`，先确认运行
的是项目 `.venv`，且没有从 MiKTeX 等目录抢先加载另一份 Qt DLL。

## 可信构建

公开跟踪的 `scripts/build_lock.json` 只冻结 Python/Qt/包版本、架构和 packaging，
不记录发行版、编译器、本机文件哈希或目录指纹。正式构建仍要求一份精确工具链
证明；准备好发布环境后运行下列命令生成并验证。完整证明只写入已忽略的
`.release`，不得提交；公开产物只携带完整 canonical 证明的 SHA-256，并通过
build ID 与 receipt 绑定。摘要基于整份高熵证明及 public-lock 摘要，不是对用户
名或机器名等低熵字段单独散列。

```powershell
.venv\Scripts\python.exe -I -B scripts\release_identity.py attest-toolchain
.venv\Scripts\python.exe -I -B scripts\release_identity.py verify-toolchain
```

不要在发布前用 `python -m venv` 重建或覆盖 `.venv`。任何环境字节或 public lock
变化都会让旧证明失效，须明确重新生成；普通源码开发和测试无需该私有证明，可用
`verify-public-lock` 只检查公开逻辑约束。
脚本从固定 commit 的 Git archive 测试并打包，嵌入编译身份，验证完整目录清单，
对候选 EXE 做真实首启和原生窗口检查，验证成功后才替换公开 `dist`；发布失败会
恢复旧产物。

```powershell
Set-Location <project-root>
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build.ps1
```

产物是整个 `dist\RetirementPet\` 目录，不是单独一个 EXE。构建末尾会打印本次
`release-receipt.json` 的精确路径；后续验收必须使用该路径，不能拿历史 receipt
或另一份 `dist` 混用。

```powershell
# 把下方路径替换为 build.ps1 最后打印的精确 receipt
$receipt = "<project-root>\.release\<run>\evidence\post-publish\<manifest>\release-receipt.json"
.venv\Scripts\python.exe scripts\go_no_go.py `
  --profile alpha `
  --artifact-dir <project-root>\dist\RetirementPet `
  --receipt $receipt
```

验收有两个独立结论：`ALPHA-GO` 仅表示该精确 artifact 可在当前机器初试；
`PRODUCTION-NO-GO:PENDING_ENVIRONMENT` 表示正式发布门禁仍未完成。候选目录或
receipt 不一致通常给出 `ALPHA-NO-GO`；只有运行前提、证据或工具超时使结果无法
判定时才返回 `INVALID_RUN`。以命令打印的 Evidence 目录内 `acceptance.json` 的
`alpha_trial_verdict` 和 `production_release_verdict` 为准。

## 使用入口

- 双击桌宠：展开或收起退休倒计时卡片。
- 右键桌宠或托盘图标：选择“打开待办”，或管理显示/隐藏、置顶、鼠标穿透、
  控制面板、自启和退出。
- Windows 下按 `Ctrl+Alt+T` 可从其他程序直接打开待办页。快捷键由系统事件触发，
  不做轮询；若该组合已被其他程序占用，RetirementPet 会继续正常启动，可改用右键
  菜单，并在本地日志中留下不可用记录。
- 控制面板 → 待办：新增根任务或子任务（最多六层），设置短/中/长期与截止日期，
  用“上移/下移”调整同级顺序，开始单一专注任务。删除任务会删除整个子树且不可
  撤销。
- 控制面板 → 动作：开关随机小动作。
- 首次手动启动 → 角色：选择“继续使用官方退休猫”，或选择“确认本地内容并启用
  半写实猫”。后一项只接受随程序固定摘要的预览包，成功后立即安全切换。
- 控制面板 → 角色：点击“导入本地角色包…”。文件选择器默认打开随附示例目录；
  选择 `realistic-retirement-cat-0.1.1.petpack` 后，再选择“半写实退休猫”并切换。
- 控制面板 → 声音与日程：设置声音、退休时间、吃饭和健身时刻。
- 鼠标穿透后无法直接点击桌宠属于预期行为，可从托盘菜单关闭穿透。

## 自制角色包

半写实猫的可编辑源目录是
`character-work\realistic-retirement-cat\`，作者说明见
[半写实猫作者说明](character-work/realistic-retirement-cat.AUTHORING.md)。如果要从
零增加系列或角色，先读[角色库扩展指南](docs/CHARACTER_LIBRARY_GUIDE.md)。修改素材和 manifest 后，
必须提升包版本，再构建和验证：

```powershell
$rpPackVersion = "0.2.0" # 必须与 petpack.json 中的新版本一致
$rpPackOutput = ".release\author-packs\realistic-retirement-cat-$rpPackVersion.petpack"

.venv\Scripts\python.exe scripts\petpack_cli.py build `
  character-work\realistic-retirement-cat `
  $rpPackOutput
.venv\Scripts\python.exe scripts\petpack_cli.py validate `
  $rpPackOutput
.venv\Scripts\python.exe scripts\petpack_cli.py preflight `
  $rpPackOutput
```

当前导入预检只允许运行时已经完整支持的透明 PNG `static/sequence` 主体，并验证
ID/引用闭包、声明尺寸、真实 Alpha、透明四角及每个角色的 idle 首帧；压缩包读取和
图片检查在后台完成，并设置 64 MiB 归档/96 MiB 解压上限。确认框会固定标注“本地
内容”、发布者/权利未经验证以及警告代码，确认记录进入不可变 receipt。它不会执行
包内代码。普通“导入本地角色包”不会自动切换角色；首次选择区的固定半写实按钮
是用户同时明确授权导入与激活的唯一例外。`0.1.1` 的 idle/work/rest 暂时共用同一
静态透明母版，其他核心动作回退到该角色 idle；这是用于先确认画风和实际显示比例
的竖切版本，不代表动作美术已经完成。

## 开机自启

托盘菜单中勾选“开机自动启动”。程序只写当前用户的：

```text
HKCU\Software\Microsoft\Windows\CurrentVersion\Run
RetirementPet = "<当前 RetirementPet.exe 的绝对路径>" --startup
```

不需要管理员权限；取消时只删除自己的值。移动整个安装目录后，使用托盘中的
“修复开机自启”更新路径。Alpha 已自动验证命令引用的是当前 frozen EXE，但真实
注销/登录后的行为仍需人工验证。`--startup` 不显示首次角色选择面板；若尚未选择，
用户之后手动再次启动 EXE 时才会打开角色页。

## 数据与备份

默认数据位于 `%APPDATA%\RetirementPet\`：

- `settings.json`：用户设置；
- `state.json`：窗口和运行状态；
- `tasks.db`：Todo 树与专注状态；
- `library\`：角色库、活动选择、journal 和 receipts；
- `logs\`：运行日志与性能 marker。

程序不会把运行数据写到 EXE 旁边。若设置了 `RETIREMENT_PET_DATA_DIR`，实际数据
目录就是该变量指向的位置，而不是 `%APPDATA%`。备份或回滚前先从托盘正常退出，
再复制整个实际数据目录；不要只复制
`tasks.db` 而漏掉同目录的 SQLite 辅助文件。

## 安全回滚

1. 初试前先保存上一版的完整程序目录，并从托盘选择“退出”，确认桌宠和托盘图标
   都消失。
2. 备份实际数据目录：默认是 `%APPDATA%\RetirementPet\`；设置过
   `RETIREMENT_PET_DATA_DIR` 时备份其指向目录。
3. 回滚时同时恢复上一份完整程序目录和试用前的完整数据目录。旧程序不保证能
   读取新版本写过的数据；不要混合两个版本的 `_internal` 文件。
4. 启动旧版；若安装路径改变，修复开机自启。

仅在同一构建机原先已有公开产物时，可信构建才会把它保留在本次
`.release\<run>\previous-RetirementPet\`；试用者不能把它当作唯一回滚来源。

## 测试

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.venv\Scripts\python.exe -I -B -m pytest -p no:cacheprovider tests

# 真实 Windows 原生层（会创建窗口/注册表沙箱）
$env:RP_RUN_NATIVE = "1"
.venv\Scripts\python.exe -I -B -m pytest -p no:cacheprovider tests\native -q
```

私有 `evidence\` 中的原始报告只证明当时的源码或产物，不能作为公开仓库内容。
当前候选是否可试用，以其编译身份、完整目录哈希、release receipt 和本次 Alpha
acceptance bundle 为准；公开与私有证据的分界见[证据治理说明](evidence/README.md)。

## 参与贡献、安全与许可

项目接受 Pull Request，但 `main` 禁止直接推送；合并前必须通过必需测试并取得
至少一次维护者审核。角色、美术、声音或文字内容必须逐项填写素材来源、许可、
再分发范围和生成式 AI 使用情况。完整流程见[贡献指南](CONTRIBUTING.md)和
[PR 模板](.github/PULL_REQUEST_TEMPLATE.md)，安全问题请按[安全政策](SECURITY.md)
私下报告。

项目原创源代码和文档采用 [MIT License](LICENSE)。仓库内的角色包、图片、声音、
字体及第三方依赖可能采用各自的许可，不会因为位于本仓库而自动变成 MIT；分发前
请同时阅读[许可说明](docs/LICENSING.md)和[第三方声明](THIRD_PARTY_NOTICES.md)。
