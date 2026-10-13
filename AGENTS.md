# 此项目的Codex协作规则

这是公开代码、私有本机数据的小鼠管理程序。当前电脑的鼠数据可以由Codex读取和操作，严禁写入GitHub仓库、提交历史、issue、PR正文、CI日志或发布附件。

## 部署

1. 找到Python 3.10以上解释器。Windows可用py -3；macOS可用python3；Codex自带运行时也可使用。不要默认使用旧系统Python。
2. 运行`python mousdb_cli.py doctor`确定实际数据目录。默认数据库在用户目录、仓库外。
3. 首次使用运行`python mousdb_cli.py init`建立空库。现有用户若提供数据库路径，只写入被忽略的`.mousdb-local.json`或设置`MOUSDB_DB_PATH`；不复制到仓库。
4. 运行`python launcher.py`。运行`python install_desktop.py`创建当前电脑的桌面入口。
5. Git克隆用户运行`python scripts/enable_hooks.py`启用提交及推送数据检查。

## 用户鼠操作

- 使用`mousdb_cli.py mice`或JSON动作查询目标，核对耳号、性别、当前状态和笼位后操作。
- 通过`mousdb_cli.py apply --file <仓库外请求文件>`执行明确操作。数据操作复用网站业务层，写入前有本地备份。
- 可以直接使用同一软件网页操作；CLI和网页共享同一个本机数据库。
- 不直接用SQL修改动物或编号，不因页面显示需要虚构基因型、出生日期、剂量或完成事实。
- 候选生日保持范围，周龄用已满周数。计划与实际完成分开记录。
- 死亡/转出和空笼清理由原业务层处理；保留历史科研记录。
- 请求JSON、读取结果、CSV、截图、备份都放到用户数据目录或系统临时目录，不放入源码目录。

## 修改与发布

- 新增/修改代码用中文备注用途、输入输出、关键步骤和专业假设；回答默认简洁中文。
- 保持Windows、Apple Silicon Mac、Intel Mac共用同一程序；不要加入作者电脑的固定路径。
- 不加入遥测、数据库同步或外部数据库服务。网站继续仅监听127.0.0.1。
- 测试用TemporaryDirectory和合成DEMO数据；不得把真实鼠数据做成fixture或截图。
- 提交前运行适当测试和`python scripts/privacy_guard.py --staged`；推送前运行`python scripts/privacy_guard.py --history`。
- 只有程序代码、文档、合成示例和静态资源能发布；`.gitignore`不能替代检查Git历史。
- 不覆盖别人已有的修改。运行状态写任务记录，AGENTS.md仅保留稳定约定。
