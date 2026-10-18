# 工程实践 · 离线阅读器

用于离线阅读 Anthropic Engineering 的非官方工具，支持中英文切换、主题目录、历史存档和个人思考记录。

## 两种版本

| 版本 | 下载 | 使用方式 |
|---|---|---|
| 独立 HTML 2.0.4 | [HTML Release](https://github.com/AlexUoU/engineering-library/releases/tag/html-v2.0.4) | 下载 HTML，在浏览器打开，无需 Python 或本地服务 |
| macOS App 2.0.5 | [App Release](https://github.com/AlexUoU/engineering-library/releases/tag/app-v2.0.5) | 完整解压 ZIP，双击工程实践.app；后台运行服务并打开浏览器 |

App 是后台启动器，不是独立阅读窗口。支持 Apple Silicon / Intel 原生入口，需要兼容的 Python 3.9+；未公证，首次打开可能触发 macOS 安全提示。双击停止工程实践.app 可结束服务。Windows/Linux 启动脚本仍需保留终端，未实机验收。

## 个人记录与升级

“我的思考”可导出和导入 JSON。HTML 浏览器存储只是便利功能，移动文件、清理浏览器或更换设备可能影响记录；请定期导出。升级前导出，在新版导入。发布包不含个人学习记录。

阅读器发布版本和文章历史独立管理。文章按保存日期展示历史，同正文的内部维护记录合并展示；底层存档保留以支持旧记录。新发布使用独立标签，旧发布不覆盖。

## 开发与构建

Python 标准库即可运行 `python3 server.py --no-check`。源码初始内容为导读种子；完整缓存见发行 ZIP。制作端联网准备与本地翻译属于独立流程，模型依赖见 translation-requirements.txt；读者不需要模型。

- 生成包：`python3 library.py package`（需要完整本地数据）。
- 单文件：将完整发行包保存为 output/engineering-library-latest.zip，然后 `python3 standalone/build.py 2.0.4`。已存在的发布目录拒绝覆盖。
- 测试：`python3 -m unittest discover -s tests`。
- HTML 测试：`node standalone/test-startup.cjs <HTML路径>` 和 `node standalone/test.cjs <HTML路径>`。
- 原生入口源代码：native/Launcher.m；双架构构建需 macOS/Xcode 与 Cocoa 框架。

## 验证范围

App 2.0.5：36 项库测试、6 项服务测试、包完整性和后台启动/停止/重启检查通过。86 份文章内容与 HTML 历史展示数据一致；未重新执行 Finder 视觉验收，Intel 设备未实测。

HTML 2.0.4：26 篇双语内容、内嵌资源、记录往返与完整脚本 DOM 模拟测试通过。Chrome file:// 自动化实机验收尚未完成；不得把模拟测试视为全浏览器兼容保证。

## 许可

软件代码采用 [MIT](LICENSE)。第三方文章、译文与图片不在 MIT 授权范围内，见 [内容版权说明](CONTENT-NOTICE.md)。
