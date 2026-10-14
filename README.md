# MousDB Local

在自己电脑上管理小鼠，也可让Codex通过聊天操作本地数据。支持Windows、Apple Silicon Mac和Intel Mac，后台只使用Python标准库，不需要Node.js、pip包或云数据库。

公开仓库只提供软件。每个人首次使用得到自己的空数据库，已有小鼠数据、备份、导出和实验记录都留在各自电脑上。软件不上传鼠数据或遥测。

## 让Codex部署

下载或克隆本仓库，在Codex中打开这个目录，发送：

> 请阅读AGENTS.md，把MousDB部署到我的电脑。找到Python 3.10以上，核对数据目录，首次使用创建空库，启用Git隐私检查，启动网站并创建桌面入口。不要把本机数据上传到GitHub。

已在使用旧版的人可以补充现有数据库位置。配置写在`.mousdb-local.json`中，代码更新继续使用原库。

## 自己启动

先安装[Python 3.10或更高版本](https://www.python.org/downloads/)。运行时没有第三方包依赖。

Windows双击`start_windows.bat`。Mac首次运行：

```sh
chmod +x start_mac.command
./start_mac.command
```

也可在终端运行：

```sh
python3 launcher.py
python3 install_desktop.py
```

Windows命令可用`py -3`替代`python3`。用`python launcher.py --stop`停止后台服务。安装器把启动入口放到当前用户桌面，路径适配自己的电脑。网站后台运行，关掉浏览器不会删除数据。

## 功能

- 横8列、竖9行（72格）的真实架位首页；切换房间/架子；整笼拖动、交换、临时停放。
- 笼卡显示品系、基因型、耳号、性别、整数周龄；候选生日保留范围。
- 小鼠录入、CSV预检导入、谱系、配鼠、产仔、离乳、死亡及转出历史。
- 取材、Tam/Bleo、实验、配鼠、检栓、成像等明确计划；同时间同操作的鼠合并为一条批次计划，前一天及当天站内提醒。
- 双击笼编号重命名；笼卡详情可删除空笼并保留历史。
- 最后一只活鼠离笼后自动停用空笼并释放位置；历史记录保留。
- 笼卡可标记待鉴定幼鼠，保存出生日期及未知数量；Codex可在核对和备份后按清单重建指定鼠房。
- 本机SQLite一致性备份、导出，Codex CLI写操作前自动备份。

## 数据在哪里

| 系统 | 默认数据目录 |
| --- | --- |
| Windows | `%LOCALAPPDATA%/MousDB/` |
| macOS | `~/Library/Application Support/MousDB/` |
| Linux | `~/.local/share/MousDB/` |

可用`python mousdb_cli.py doctor`查看这台电脑实际位置。配置/环境变量可指定已有数据库。详见[隐私边界](docs/PRIVACY.md)。

## 让Codex操作小鼠

可以聊天要求“录入一笼”“把指定耳号移笼”“登记死亡”“新增Tam计划”等。Codex读取[AGENTS.md](AGENTS.md)后通过本地CLI操作，和网页使用同一业务规则、同一数据库。执行示例见[Codex操作说明](docs/CODEX.md)。

提供给Codex的信息按Codex服务处理；项目不会把这些信息写入GitHub。

## 更新代码

首次克隆启用Git保护：

```sh
python scripts/enable_hooks.py
```

更新前保存本地数据库备份，然后`git pull --ff-only`，运行`python launcher.py --restart`重新启动网站。不要把数据库或导出文件加入提交。

修改后运行测试、隐私检查，再提交/推送源码：

```sh
python -m unittest discover -s tests -v
python scripts/privacy_guard.py --staged
python scripts/privacy_guard.py --history
```

GitHub Actions在Windows及两类Mac运行合成数据测试。实际CI结果看本仓库Actions；未通过的平台不宣称已验证。

## 许可与来源

MIT许可。拖动使用本地打包的SortableJS（附MIT许可），运行不依赖在线CDN。
macOS解释器安装可参考[Python官方说明](https://docs.python.org/3/using/mac.html)。
