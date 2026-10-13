# GitHub与本机数据的边界

GitHub只保存Python/网页代码、文档、图标以及合成DEMO示例。仓库不会携带任何使用者的小鼠数据库、实验记录、截图、备份或实际CSV。

软件只监听`127.0.0.1`，所有网页读写和Codex CLI操作都在本机SQLite中完成。没有遥测、上传API、云数据库或在线CDN；互联网只用于下载/更新程序和用户主动使用的Codex服务。

默认数据目录位于用户目录、Git仓库之外。数据库可以通过环境变量或被忽略的`.mousdb-local.json`定位，不把作者或其他人的路径固定在公共代码中。旧版存在`data/mousdb.sqlite3`时沿用该库；数据目录依然被忽略。

保护包含：

1. `.gitignore`排除数据库、备份、导出、截图、日志和私人配置。
2. `scripts/enable_hooks.py`安装本地提交/推送检查。
3. `privacy_guard.py`检查文件类型、SQLite文件头、私人目录、令牌和Git全部提交历史。
4. GitHub CI再次检查源码；CI只生成临时合成测试数据。

更新应始终从独立的源码目录提交，不要把整个实验记录目录变成发布仓库。请求文件和实际导出放在数据目录或系统临时目录。需要共享数据时由数据持有人另外选择方式，不通过本项目发布流程。

用户主动提供给Codex的信息可以由Codex处理；这不等于将其提交到GitHub。GitHub issue、PR、提交说明和Actions日志也不能包含实际鼠信息。

可参考[GitHub关于敏感数据与Git历史的说明](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)。
