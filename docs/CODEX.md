# Codex本地操作

在Codex中打开源码目录，先读取AGENTS.md，定位Python和本机数据库。无需连接Google日历、OneNote或其他人的数据；这些服务不属于本项目的运行依赖。

查看环境：

```sh
python mousdb_cli.py doctor
```

首次建立自己的空库：

```sh
python mousdb_cli.py init
```

查询小鼠：

```sh
python mousdb_cli.py mice --scope all
python mousdb_cli.py mice --search DEMO-001
```

数据写入通过JSON请求文件进行，避免Windows命令行引号破坏中文或特殊符号。真实请求保存在本地数据目录或系统临时目录。不要将真实请求放在仓库中。合成格式示例见`examples/request_create_mouse.json`。

```sh
python mousdb_cli.py apply --file /path/to/private-request.json
```

请求结构为`action`、`params`、`payload`。支持的动作：

| 目标 | 动作 |
| --- | --- |
| 小鼠 | `mouse.list`、`mouse.get`、`mouse.create`、`mouse.update` |
| 笼 | `cage.list`、`cage.create`、`cage.update`、`cage.delete`（仅空笼） |
| 架子 | `rack.list`、`rack.board`、`rack.create`、`rack.move`、`rack.undo` |
| 实验计划 | `plan.list`、`plan.create`、`plan.update` |
| 字典 | `references.list`、`reference.create`、`reference.update` |
| 繁育 | `mating.list`、`mating.create`、`litter.create`、`litter.wean` |
| 实际操作 | `event.create` |

小鼠定位可使用`params.mouse_id`或唯一的`params.ear_tag`。修改的实际字段放入`payload`。系统编号由后端产生且只读，不能用Codex绕过。

整笼移动和撤销先用`rack.board`读取最新`revision`，再在payload中填写`expected_revision`，与网页一样检测并发变化。写入前会自动产生`backups/before_codex_*.sqlite3`。

计划是将来要做的工作，实际事件是已经发生的操作，不能混淆。真实手术、取材、死亡信息必须依据使用者明确陈述。

Codex可以总结本地记录并解释执行结果；禁止把输出粘贴到公开GitHub记录。

实验计划使用一条批次记录：同一日期、时刻、操作的全部鼠放在一个payload.mouse_ids或ear_tags里。后端会合并同批任务，编辑或标记完成作用于整个批次。实际完成记录仍依据逐鼠事实；不同具体动作或时刻分别记录。

鼠房整体重建使用`room.replace_inventory`，params填写room_id；payload必须含expected_room_name、expected_cage_ids、expected_mouse_ids、expected_rack_revisions、reason、cages。新笼包含rack_id、code、row、column、mice及可选card_state、genotyping。先核对旧库存并备份，再执行；不会推定死亡或转出。旧库存有外部谱系、繁育或计划关联时拒绝整体删除。

待鉴定幼鼠使用`cage.genotyping.update`，params填写cage_id；payload可填pending、birth_dates（ISO日期列表）、pup_count（未知用null）、note。只标记笼位，不自动创建未知幼鼠或推断母鼠。批量重建默认生成M开头的中性系统编号。

## 鉴定图片资料

先用`genotyping.list`查询目录（`params.category`为`protocol`或`result`），`genotyping.get`读取卡片。
用`genotyping.create`新建标题/基因名/备注，`genotyping.update`修改文字，`genotyping.image.add`将用户指定的本机图片加入卡片。
`image.add`参数为`document_id`和`file_path`，可选`payload.filename`设置展示文件名；每张JPG/PNG/WebP不超过8 MB。
`genotyping.archive`只移除卡片显示，图片留在本机历史。所有写动作复用网页业务层，CLI在写入前备份。
图片按用户提供的基因名称归档，不擅自OCR或根据条带更改小鼠基因型。请求文件与上传回执存放在仓库外。
