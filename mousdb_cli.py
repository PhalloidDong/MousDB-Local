"""Codex可调用的本地命令接口。

输入：JSON请求文件（一个动作+参数）或查询子命令；输出：JSON结果。
查询/写入均复用网站的Database/RackLayoutStore/PlanStore规则，不直接拼SQL改鼠。
写入前自动备份本机SQLite；备份在私有数据目录，不在公开代码目录。
"""
import argparse,json,sys
from pathlib import Path
from datetime import datetime
from db import Database,ValidationError
from runtime_paths import storage_paths
from rack_layout import RackLayoutStore
from experiment_plans import PlanStore

READ_ACTIONS={'mouse.list','mouse.get','cage.list','rack.list','rack.board','plan.list','references.list','mating.list'}
ACTIONS=READ_ACTIONS|{'mouse.create','mouse.update','cage.create','cage.update','cage.delete','rack.create','rack.move','rack.undo',
                    'plan.create','plan.update','reference.create','reference.update','mating.create','litter.create','litter.wean','event.create'}

def mouse_id(db,params):
    if params.get('mouse_id') is not None:return int(params['mouse_id'])
    tag=str(params.get('ear_tag') or '')
    with db.read() as c:rows=c.execute('SELECT id FROM mice WHERE ear_tag=?',(tag,)).fetchall()
    if len(rows)!=1:raise ValidationError('耳号不存在或不唯一，请提供mouse_id')
    return rows[0][0]

def dispatch(db,request):
    """明确白名单动作：参数由原业务层再次校验；系统编号仍只读。"""
    action=request.get('action');params=request.get('params') or {};payload=request.get('payload') or {}
    if action not in ACTIONS:raise ValidationError('不支持的操作：'+str(action))
    if not isinstance(params,dict) or not isinstance(payload,dict):raise ValidationError('params/payload必须为对象')
    if action=='mouse.list':return db.list_mice(**params)
    if action=='mouse.get':return db.get_mouse(mouse_id(db,params))
    if action=='mouse.create':return db.create_mouse(payload)
    if action=='mouse.update':return db.update_mouse(mouse_id(db,params),payload)
    if action=='cage.list':return db.list_cages()
    if action=='cage.create':return db.create_cage(payload)
    if action=='cage.delete':return db.delete_cage(int(params['cage_id']))
    if action=='cage.update':return db.update_cage(int(params['cage_id']),payload)
    if action=='rack.list':return RackLayoutStore(db).list_racks()
    if action=='rack.create':return RackLayoutStore(db).save_rack(payload)
    if action=='rack.board':return RackLayoutStore(db).board(int(params['rack_id']))
    if action=='rack.move':return RackLayoutStore(db).move(int(params['rack_id']),payload)
    if action=='rack.undo':return RackLayoutStore(db).undo(int(params['rack_id']),payload)
    if action=='plan.list':return PlanStore(db).list()
    if action=='plan.create':return PlanStore(db).save(payload)
    if action=='plan.update':return PlanStore(db).save(payload,int(params['plan_id']))
    if action=='references.list':return db.list_references()
    if action=='reference.create':return db.create_reference(params['kind'],payload)
    if action=='reference.update':return db.update_reference(params['kind'],int(params['reference_id']),payload)
    if action=='mating.list':return db.list_matings()
    if action=='mating.create':return db.create_mating(payload)
    if action=='litter.create':return db.create_litter(int(params['mating_id']),payload)
    if action=='litter.wean':return db.wean_litter(int(params['litter_id']),payload)
    if action=='event.create':return db.add_event(mouse_id(db,params),payload)

def initialize(db):
    db.initialize();RackLayoutStore(db).initialize();PlanStore(db).initialize()

def main():
    parser=argparse.ArgumentParser(description='MousDB本地数据操作；数据不发送到网络')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('doctor',help='输出运行环境和实际数据位置')
    sub.add_parser('init',help='首次使用时创建自己的空库；已有库仅进行兼容迁移')
    query=sub.add_parser('mice',help='查询小鼠');query.add_argument('--scope',choices=['alive','dead','all'],default='alive');query.add_argument('--search',default='')
    apply=sub.add_parser('apply',help='执行一个明确的JSON动作');apply.add_argument('--file',required=True)
    args=parser.parse_args();data_dir,path=storage_paths();db=Database(path)
    if args.command=='doctor':return {'python':sys.version.split()[0],'database':str(path),'database_exists':path.exists(),'data_dir':str(data_dir)}
    if args.command=='init':initialize(db);return {'database':str(path),'ready':True}
    if not path.exists():raise ValidationError('本机数据库尚不存在，请先init；不会猜测或导入其他人的数据')
    initialize(db)
    if args.command=='mice':return db.list_mice(scope=args.scope,search=args.search)
    request=json.loads(Path(args.file).read_text(encoding='utf-8-sig'))
    if not isinstance(request,dict):raise ValidationError('请求必须为JSON对象')
    if request.get('action') not in ACTIONS:raise ValidationError('不支持的操作')
    backup=None
    if request['action'] not in READ_ACTIONS:
        backup=data_dir/'backups'/('before_codex_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.sqlite3')
        db.backup_to(backup)
    result=dispatch(db,request)
    return {'action':request['action'],'result':result,'backup':str(backup) if backup else None}

if __name__=='__main__':
    if sys.stdout is not None:sys.stdout.reconfigure(encoding='utf-8')
    try:print(json.dumps({'ok':True,'data':main()},ensure_ascii=False,default=str))
    except Exception as error:
        print(json.dumps({'ok':False,'error':str(error)},ensure_ascii=False));raise SystemExit(1)
