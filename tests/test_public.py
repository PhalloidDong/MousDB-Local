"""公开版跨平台与Codex接口验收；只使用临时合成记录，不访问真实鼠数据。"""
import importlib.util,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path,PurePosixPath
from unittest.mock import patch
from datetime import date
from db import Database
from runtime_paths import ROOT,storage_paths,default_data_dir
from mousdb_cli import initialize,dispatch
from experiment_plans import PlanStore
from install_desktop import mac_shortcut
spec=importlib.util.spec_from_file_location('privacy_guard',ROOT/'scripts/privacy_guard.py')
privacy=importlib.util.module_from_spec(spec);spec.loader.exec_module(privacy)
class PublicTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve()
        self.db=Database(self.root/'mice.sqlite3');initialize(self.db)
    def tearDown(self):self.tmp.cleanup()
    def test_platform_data_outside_repository(self):
        # 使用临时home模拟系统，证明每个平台默认数据库均不在公开代码目录中。
        for platform in ['win32','darwin','linux']:
            with patch('runtime_paths.sys.platform',platform),patch('runtime_paths.Path.home',return_value=self.root),patch.dict(os.environ,{},clear=True):
                self.assertFalse(default_data_dir().is_relative_to(ROOT))
        with patch('runtime_paths.configuration',return_value={}),patch.dict(os.environ,{'MOUSDB_DB_PATH':str(self.root/'mine.sqlite3')},clear=True):
            folder,database=storage_paths();self.assertEqual(folder,self.root);self.assertEqual(database,self.root/'mine.sqlite3')
    def test_codex_death_preserves_number_and_history(self):
        # CLI和网页共用同一后端；死亡移笼/编号只读不能因聊天接口而绕过。
        room=self.db.create_reference('rooms',{'name':'Demo room'})
        rack=self.db.create_rack({'room_id':room['id'],'name':'Demo rack'})
        cage=self.db.create_cage({'rack_id':rack['id'],'code':'Demo cage'})
        m=dispatch(self.db,{'action':'mouse.create','payload':{'ear_tag':'DEMO-001','sex':'M','cage_id':cage['id'],'dob':'2026-01-01'}})
        changed=dispatch(self.db,{'action':'mouse.update','params':{'ear_tag':'DEMO-001'},'payload':{'code':'OVERRIDE','status':'Dead','death_date':'2026-01-15'}})
        self.assertEqual(changed['code'],m['code']);self.assertEqual(changed['age_label'],'2周');self.assertIsNone(changed['cage_id'])
        self.assertEqual(self.db.get_cage(cage['id'])['active'],0);self.assertTrue(changed['movements'])
    def test_cli_backup_and_utf8(self):
        # 真实请求路径必须在仓库外；此处仅验证合成中文耳号和备份回执。
        request=self.root/'request.json';request.write_text(json.dumps({'action':'mouse.create','payload':{'ear_tag':'DEMO-中文','sex':'M'}},ensure_ascii=False),encoding='utf-8')
        env={**os.environ,'MOUSDB_DB_PATH':str(self.db.path),'MOUSDB_DATA_DIR':str(self.root)}
        result=json.loads(subprocess.check_output([sys.executable,str(ROOT/'mousdb_cli.py'),'apply','--file',str(request)],env=env,encoding='utf-8'))
        self.assertTrue(result['ok']);self.assertTrue(Path(result['data']['backup']).exists());self.assertEqual(result['data']['result']['ear_tag'],'DEMO-中文')
    def test_mac_shortcut_quotes_spaces(self):
        target=mac_shortcut(self.root,PurePosixPath('/opt/example python/bin/python3'),PurePosixPath('/opt/example app/launcher.py'))
        self.assertTrue(target.read_text().startswith('#!/bin/sh'));self.assertIn("'/opt/example python/bin/python3'",target.read_text())
        if sys.platform!='win32':
            subprocess.run(['sh','-n',str(target)],check=True);self.assertTrue(target.stat().st_mode&0o111)
    def test_explicit_plan_previous_day(self):
        self.db.create_mouse({'sex':'F','ear_tag':'DEMO-002','comment':'计划：2026-11-02 10:00 检栓'})
        store=PlanStore(self.db);items=store.reminders(date(2026,11,1))['items']
        self.assertEqual(len(items),1);self.assertEqual(items[0]['when'],'tomorrow');self.assertEqual(items[0]['plan_time'],'10:00')
        store.save({'status':'completed'},items[0]['id']);self.assertFalse(store.reminders(date(2026,11,2))['items'])
    def test_privacy_rejects_private_files(self):
        for path,data in [('data/mice.sqlite3',b'anything'),('renamed.py',b'SQLite format 3\x00'),('export.csv',b'rows'),('private.json',b'{}'),('token.py',('ghp_'+'x'*30).encode())]:
            self.assertIsNotNone(privacy.inspect(path,data))
        self.assertIsNone(privacy.inspect('example.py',b'print("example")'))
if __name__=='__main__':unittest.main()
