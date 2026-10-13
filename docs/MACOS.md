# macOS部署

本仓库的后端、网页、SQLite和Codex CLI在Windows/Mac使用同一源码。Python标准库不依赖CPU架构；部署需要Python 3.10以上，支持Apple Silicon和Intel版本解释器。

1. 克隆/下载源码并在Codex中打开；让Codex读取AGENTS.md后定位`python3`。
2. 运行`python3 mousdb_cli.py init`创建自己的空库。
3. 运行`python3 launcher.py`，网站后台启动并打开默认浏览器。
4. 运行`python3 install_desktop.py`，桌面得到`MousDB.command`。

不使用Codex也可先`chmod +x start_mac.command`，然后运行它。文件路径包含空格时安装器会正确引用。

数据库默认是`~/Library/Application Support/MousDB/mousdb.sqlite3`，不会因为重新下载源码而丢失。不要把它移动进Git仓库。

Mac首次启动脚本可能需要用户按系统界面允许打开；这里不自动关闭Gatekeeper或修改系统安全设置。安装Python可参照[官方Mac说明](https://docs.python.org/3/using/mac.html)。

兼容性验证通过GitHub Actions分别运行Apple Silicon (`macos-15`)及Intel (`macos-15-intel`)测试。进度和结果以仓库Actions为准。
