# 为 RetirementPet 做贡献

感谢你愿意帮助改进 RetirementPet。项目目前处于 Windows Alpha 阶段，优先接受
可复现的缺陷修复、低资源占用改进、文档修正，以及来源和许可清楚的原创角色内容。

开始前请先阅读[安全报告政策](SECURITY.md)和[文档中心](docs/README.md)。涉及
PetPack 或素材时，还必须阅读[内容、权利与隐私政策](docs/CONTENT_POLICY.md)和
[角色库扩展指南](docs/CHARACTER_LIBRARY_GUIDE.md)。提交前也请确认
[许可说明](docs/LICENSING.md)中代码与内容资产的不同边界。

## 分支与合并规则

- `main` 只接受 Pull Request，不直接推送；维护者的紧急修复也应留下 PR 记录。
- 从最新 `main` 创建短期分支，一项 PR 只解决一个清楚的问题。
- 合并前必须通过所需检查和代码审阅。涉及受 CODEOWNERS 管理的文件时，需要相应
  owner 批准。
- 不要强推或改写已经公开的 tag、release、receipt、事件或历史 evidence。
- 不要为了让完整性检查通过而盲目更新 hash。先判断是实现变化、文档漂移、工具
  漂移还是原始事实变化，再提交对应修复。

仓库设置中的 branch protection/ruleset 才能真正阻止直接推送；`CODEOWNERS` 本身
只声明审阅责任。

## 建立开发环境

项目面向 Python 3.12 和 Windows，依赖 PySide6 6.8.3。仓库可克隆到任意普通路径，
不要依赖维护者机器上的盘符。

```powershell
git clone https://github.com/Bpig-C/RetirementPet.git
Set-Location RetirementPet
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_dev.ps1
```

## 修改与验证

提交前至少运行与你的修改直接相关的测试。可以运行完整的非原生测试。

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.venv\Scripts\python.exe -I -B -m pytest -p no:cacheprovider tests
```

文档改动至少运行下面的检查。

```powershell
.venv\Scripts\python.exe -I -B -m pytest -p no:cacheprovider `
  tests\test_documentation_links.py
git diff --check
```

Windows 原生窗口、注册表、自启或全局快捷键改动，还应按文档说明运行原生测试，
并在 PR 中说明 Windows 版本、DPI、屏幕数量和实际结果。不要用旧 evidence 冒充
本次测试结果，也不要把本机 `.release` 目录提交到仓库。

## 文档链

新增或移动 Markdown 时，必须从[文档中心](docs/README.md)或其管理的子索引建立
入口，并修复所有相对链接。当前事实、版本事件、未来探索和冻结规范各有自己的
文档职责；不要为了方便把同一个结论复制到多处并造成漂移。

## 角色包、图片、声音和文字

提交内容资产时，PR 必须逐项说明以下信息。

- 原作者或生成者、原始来源和可核查的 `source_ref`；
- SPDX 许可证或完整许可文本，以及许可覆盖的使用、修改和再分发范围；
- 是否使用生成式 AI，使用的工具与模型、生成日期、适用服务条款、生成方式和后续
  人工修改；
- 资产与 PetPack manifest 中 `rights_ref`、`source_ref` 的对应关系；
- 是否包含商标、影视、动漫、游戏角色或其他第三方权利元素。

“网上找到”“仅供学习”“无意侵权”和文件带有水印都不是有效授权。未经明确许可，
不要提交 Harry Potter、罗小黑或其他现有系列的角色、截图、临摹、音频和文字。
未知权利内容可以在本地自行试验，但不能进入公开仓库或官方发布包。

项目暂不要求单独签署贡献者许可协议（CLA）。除非 PR 明确声明并由维护者接受另一
适用许可，你有意提交以纳入 RetirementPet 的原创代码和文档，自提交时起按项目
MIT License 许可给项目及所有接收者。你保留著作权，并确认有权作出这项授权。
采用独立许可证的素材仍按 PR 和 PetPack 元数据中逐项披露的条款处理；维护者可以
因权利证据不足拒绝内容。

## 隐私与本机信息

Issue、PR、测试夹具和提交历史中不得包含以下内容。

- `tasks.db`、真实任务、音乐文件或整个 `%APPDATA%\RetirementPet`；
- 未脱敏日志、用户名、绝对个人路径、机器名、邮箱、令牌、证书或密钥；
- 原始崩溃转储、屏幕截图中的私人窗口，或第三方未公开角色包；
- `.venv`、`build`、`dist`、`.release` 及本机生成的验收目录。

确需展示路径时，请改写成如 `<user-profile>\...`。截图使用干净测试账户和虚构
数据。发现安全问题不要开公开 Issue，请按[安全报告政策](SECURITY.md)私下报告。

## Pull Request 内容

PR 描述应包含用户可见结果、设计边界、验证命令与结果、仍未验证的环境条件，以及
必要的前后截图。请完整勾选 PR 模板；不适用的项目应说明原因，而不是直接删除。
