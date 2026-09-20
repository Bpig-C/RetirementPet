# RetirementPet 第三方组件声明

> 返回[许可说明](docs/LICENSING.md)和[文档中心](docs/README.md)。
> 本文件是公开源码仓库的组件入口，不替代每个二进制发行包内按实际 artifact 生成的
> 完整许可目录。

RetirementPet 自身原创源代码和文档采用 [MIT License](LICENSE)。运行、测试和打包
还使用下列主要第三方项目。

| 组件 | 当前约束 | 主要许可入口 | 用途 |
|---|---|---|---|
| Python | 3.12 | [Python license](https://docs.python.org/3/license.html) | 解释器与标准库 |
| PySide6 / Shiboken6 | 6.8.3 | [Qt for Python licensing](https://doc.qt.io/qtforpython-6/licenses.html) | Python Qt 绑定 |
| Qt | 6.8.3（随 PySide6 wheel） | [Qt licensing](https://www.qt.io/licensing/open-source-lgpl-obligations) | 窗口、绘制、音频和平台集成 |
| PyInstaller | 6.x | [PyInstaller license](https://pyinstaller.org/en/stable/license.html) | Windows onedir 打包 |
| winrt-runtime 及投影包 | 3.2.1 | [pywin32-family license (Apache-2.0)](https://pypi.org/project/winrt-runtime/) | Windows 系统媒体会话（V13-05，可选功能） |
| pytest | 开发依赖 | [pytest license](https://github.com/pytest-dev/pytest/blob/main/LICENSE) | 自动化测试，不应进入运行包 |

PySide6 和 Qt 的开源发行涉及 LGPLv3/GPLv3 以及各模块自己的第三方许可。项目选择
动态库形式的 onedir 打包，并不得阻止用户替换适用的 LGPL 动态库；具体义务必须
以实际分发模块和 Qt 官方许可清单为准。

历史 1.1.1 onedir 还观察到 QtMultimedia 关联的编解码库、OpenSSL、SQLite、Qt
Virtual Keyboard 插件和 Microsoft 运行库等文件。这份旧目录没有完整许可材料，
也可能带入未使用插件，所以不作为公开 Release。下一次构建要先删除未使用组件，
再按最终逐文件归属生成 SBOM；本段不是可替代 SBOM 的许可证结论。

## 二进制发行门禁

每个公开 Release 的便携目录必须至少包含以下材料。

- 本项目 `LICENSE` 和本文件；
- 根据冻结 artifact 扫描得到的第三方组件、版本和文件归属清单；
- 每个实际分发组件要求的完整许可、版权和 NOTICE 文本；
- LGPL 组件的对应源码获取方式，以及允许调试修改和替换/重新链接的说明；
- 角色包自身 `legal_file_ref` 指向的许可文本和必要署名；
- 一份把这些材料绑定到该 Release 文件摘要的发布记录。

如果组件清单、许可文件或对应源码入口缺失，发布结论必须保持 NO-GO，不能用根
目录 MIT、测试通过或 hash 一致代替许可履行。历史 receipt 只证明当时 artifact，
不会自动证明新的公开分发合规。

## 角色包不是第三方 Python 依赖

`.petpack` 是不执行代码的数据包，并各自携带许可和来源元数据。它们是否随程序
分发由[内容、权利与隐私政策](docs/CONTENT_POLICY.md)决定；根目录 MIT 不覆盖已有
包内许可。角色包 PR 的作者、来源、AI 使用情况、修改权和再分发权必须逐项审阅。
