"""批次计划验收：只使用临时合成鼠，不接触用户数据库或日历。"""
import json,tempfile,unittest
from pathlib import Path
from datetime import date
from db import Database,now_text
from experiment_plans import PlanStore

class PlanBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Database(Path(self.temp.name)/'demo.sqlite3');self.db.initialize()
        self.s=PlanStore(self.db);self.s.initialize()
        self.a=self.db.create_mouse({'sex':'M','ear_tag':'DEMO-A'})
        self.b=self.db.create_mouse({'sex':'F','ear_tag':'DEMO-B'})
    def tearDown(self):self.temp.cleanup()
    def comment(self,mouse,text):self.db.update_mouse(mouse['id'],{'comment':text})
    def request(self,mouse,title='DEMO 取材',day='2026-11-02',clock='10:00',kind='取材',details=''):
        return dict(title=title,plan_type=kind,plan_date=day,plan_time=clock,mouse_ids=[mouse['id']],details=details)
    def test_comments_are_one_batch_with_all_mice_and_one_reminder(self):
        # 两只鼠不同备注来源，在同一时刻取材，列表和提醒都只有一条。
        for m in [self.a,self.b]:self.comment(m,'计划：2026-11-02 10:00 取材 DEMO样本')
        plans=self.s.list()['plans'];self.assertEqual(len(plans),1)
        self.assertEqual({m['id'] for m in plans[0]['mice']},{self.a['id'],self.b['id']})
        self.assertEqual(len(self.s.reminders(date(2026,11,1))['items']),1)
        self.assertEqual(self.s.list()['plans'],plans)
    def test_manual_create_joins_batch_and_keeps_distinct_notes(self):
        first=self.s.save(self.request(self.a,title='DEMO group A',details='A specimen'))
        second=self.s.save(self.request(self.b,title='DEMO group B',details='B specimen'))
        self.assertEqual(first['id'],second['id']);self.assertEqual(len(second['mice']),2)
        self.assertIn('A specimen',second['details']);self.assertIn('B specimen',second['details'])
        self.assertIn('DEMO-A',second['details']);self.assertIn('DEMO-B',second['details'])
        self.assertEqual(len(self.s.list()['plans']),1)
    def test_different_times_and_operations_stay_separate(self):
        # 同鼠同日10:00和14:00是两次给药，解析来源键也必须保留时刻。
        self.comment(self.a,'计划：2026-11-02 10:00 注射Tam\n计划：2026-11-02 14:00 注射Tam')
        self.s.save(self.request(self.b,kind='取材'))
        p=self.s.list()['plans'];self.assertEqual(len(p),3)
        self.assertEqual({x['plan_time'] for x in p if x['plan_type']=='注射Tam'},{'10:00','14:00'})
    def test_specific_lab_tasks_stay_separate(self):
        self.s.save(self.request(self.a,title='DEMO PFA换PBS',kind='样本处理'))
        self.s.save(self.request(self.b,title='DEMO组织包埋',kind='样本处理'))
        self.assertEqual(len(self.s.list()['plans']),2)
    def test_old_per_mouse_rows_are_archived_and_old_id_updates_batch(self):
        """模拟升级前逐鼠行：合并归档保留原计划，旧ID更新会作用于共享批次。"""
        for m in [self.a,self.b]:self.comment(m,'计划：2026-11-02 取材 DEMO')
        with self.db.write() as c:
            for m in [self.a,self.b]:
                pid=c.execute("INSERT INTO experiment_plans(title,plan_type,plan_date,source,source_key,created_at,updated_at) VALUES('取材 DEMO','取材','2026-11-02','comment',?,?,?)",(f"comment:{m['id']}:取材:2026-11-02",now_text(),now_text())).lastrowid
                c.execute('INSERT INTO experiment_plan_mice VALUES(?,?)',(pid,m['id']))
        self.s.initialize();p=self.s.list()['plans'];self.assertEqual(len(p),1)
        with self.db.read() as c:
            archive=c.execute('SELECT * FROM experiment_plan_merges').fetchone()
            snapshot=json.loads(archive['snapshot_json']);self.assertEqual(snapshot['mouse_ids'],[self.b['id']])
            self.assertEqual(c.execute('SELECT COUNT(*) FROM experiment_plan_sources').fetchone()[0],2)
        completed=self.s.save({'status':'completed'},archive['old_plan_id'])
        self.assertEqual(len(completed['mice']),2);self.assertEqual(completed['status'],'completed')
        self.s.initialize();self.assertEqual(len(self.s.list()['plans']),1)
        with self.db.read() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM mouse_events').fetchone()[0],0)
    def test_manual_edit_does_not_recreate_original_comment_plans(self):
        for m in [self.a,self.b]:self.comment(m,'计划：2026-11-02 取材 DEMO')
        original=self.s.list()['plans'][0]
        changed=self.s.save({'plan_date':'2026-11-03','title':'DEMO改期'},original['id'])
        self.assertEqual(len(changed['mice']),2)
        self.s.initialize();p=self.s.list()['plans'];self.assertEqual(len(p),1);self.assertEqual(p[0]['plan_date'],'2026-11-03')
    def test_partial_actual_completion_does_not_complete_other_mice(self):
        for m in [self.a,self.b]:self.comment(m,'计划：2026-11-02 取材 DEMO')
        self.s.list()
        self.db.add_event(self.a['id'],{'title':'已完成取材 DEMO','event_type':'取材','event_date':'2026-11-02'})
        p=self.s.list()['plans'];self.assertEqual(len(p),2)
        pending=next(x for x in p if x['status']=='pending');done=next(x for x in p if x['status']=='completed')
        self.assertEqual([m['id'] for m in pending['mice']],[self.b['id']]);self.assertEqual([m['id'] for m in done['mice']],[self.a['id']])
    def test_removed_comment_mouse_leaves_auto_batch(self):
        for m in [self.a,self.b]:self.comment(m,'计划：2026-11-02 取材 DEMO')
        self.s.list();self.comment(self.b,'');p=self.s.list()['plans']
        self.assertEqual(len(p),1);self.assertEqual([m['id'] for m in p[0]['mice']],[self.a['id']])
        self.comment(self.a,'');self.assertEqual(self.s.list()['plans'][0]['status'],'cancelled')
    def test_new_ids_never_reuse_archived_ids(self):
        self.s.save(self.request(self.a));self.s.save(self.request(self.b))
        next_plan=self.s.save(self.request(self.b,day='2026-11-03'))
        with self.db.read() as c:archived={r[0] for r in c.execute('SELECT old_plan_id FROM experiment_plan_merges')}
        self.assertNotIn(next_plan['id'],archived)

if __name__=='__main__':unittest.main()
