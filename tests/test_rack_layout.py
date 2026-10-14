"""架位事务的关键验收；全部在临时SQLite中执行，不改真实动物房数据。
覆盖整笼交换、72格全满仍可停放、撤销、刷新、并发过期与建笼回滚。
"""
import tempfile,unittest,sqlite3
from pathlib import Path
from unittest.mock import patch
from db import Database,ValidationError
from rack_layout import RackLayoutStore,LayoutConflict

class RackTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=Database(Path(self.tmp.name)/'test.sqlite3');self.db.initialize()
        room=self.db.create_reference('rooms',{'name':'Test room'})
        self.rack=self.db.create_rack({'room_id':room['id'],'name':'Rack 1'})['id']
        self.other=self.db.create_rack({'room_id':room['id'],'name':'Rack 2'})['id']
        self.cages=[self.db.create_cage({'rack_id':self.rack,'code':f'T{i}','mice':[{'sex':'M','ear_tag':str(i)}]}) for i in range(3)]
        self.s=RackLayoutStore(self.db);self.s.initialize()
    def tearDown(self):self.tmp.cleanup()
    def move(self,cage,row,column):
        board=self.s.board(self.rack);return self.s.move(self.rack,{'cage_id':cage,'row':row,'column':column,'expected_revision':board['rack']['revision']})
    def pos(self,board,cage):
        c=next(x for x in board['cards'] if x['id']==cage);return c['row_index'],c['column_index']
    def test_old_cages_are_unlocated_and_restart_preserves(self):
        b=self.s.board(self.rack);self.assertEqual((b['placed'],b['parked'],b['mouse_count']),(0,3,3))
        self.move(self.cages[0]['id'],2,8);self.s.initialize();b=self.s.board(self.rack)
        self.assertEqual(self.pos(b,self.cages[0]['id']),(2,8));self.assertEqual(b['rack']['rows']*b['rack']['columns'],72)
    def test_swap_keeps_mouse_cage_and_can_undo(self):
        a,b=self.cages[0]['id'],self.cages[1]['id'];self.move(a,1,1);self.move(b,1,2)
        changed=self.move(a,1,2);self.assertEqual(self.pos(changed,b),(1,1))
        with self.db.read() as c:self.assertEqual(c.execute('SELECT cage_id FROM mice WHERE ear_tag=?',('0',)).fetchone()[0],a)
        restored=self.s.undo(self.rack,{'expected_revision':changed['rack']['revision']})
        self.assertEqual(self.pos(restored,a),(1,1));self.assertEqual(self.pos(restored,b),(1,2))
    def test_stale_invalid_foreign_and_half_coordinates_rejected(self):
        rev=self.s.board(self.rack)['rack']['revision'];self.move(self.cages[0]['id'],1,1)
        with self.assertRaises(LayoutConflict):self.s.move(self.rack,{'cage_id':self.cages[1]['id'],'row':1,'column':3,'expected_revision':rev})
        for row,col in [(10,1),(1,9),(None,1),(1,None),(-1,1),(1.5,2)]:
            with self.assertRaises(ValidationError):self.move(self.cages[0]['id'],row,col)
        with self.assertRaises(ValidationError):self.s.move(self.other,{'cage_id':self.cages[0]['id'],'row':1,'column':1,'expected_revision':self.s.board(self.other)['rack']['revision']})
    def test_atomic_slot_create_and_occupied_conflict(self):
        rev=self.s.board(self.rack)['rack']['revision']
        with self.assertRaises(ValidationError):self.s.create_slot_cage(self.rack,{'row':2,'column':2,'expected_revision':rev,'cage':{'code':'Invalid','mice':[{'sex':'wrong'}]}})
        with self.db.read() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM cages WHERE code='Invalid'").fetchone()[0],0)
        made=self.s.create_slot_cage(self.rack,{'row':2,'column':2,'expected_revision':rev,'cage':{'code':'Good','mice':[{'sex':'F','ear_tag':'new'}]}})
        self.assertEqual(self.pos(made,made['created_cage_id']),(2,2))
        with self.assertRaises(LayoutConflict):self.s.create_slot_cage(self.rack,{'row':2,'column':2,'expected_revision':made['rack']['revision'],'cage':{'code':'Blocked'}})
    def test_full_rack_parking_and_restore(self):
        # 满架是核心条件：不是只测4张卡。72个真实cage填满后仍能移动到NULL临时区。
        with self.db.write() as c:
            for i in range(72):
                cage=c.execute('INSERT INTO cages(rack_id,code,capacity,created_at,updated_at) VALUES(?,?,5,?,?)',(self.rack,'FULL'+str(i),'2026-10-12','2026-10-12')).lastrowid
                c.execute('INSERT INTO cage_positions(cage_id,rack_id,row_index,column_index) VALUES(?,?,?,?)',(cage,self.rack,i//8+1,i%8+1))
        b=self.s.board(self.rack);self.assertEqual(b['placed'],72)
        selected=next(x['id'] for x in b['cards'] if x['row_index']==1 and x['column_index']==1)
        p=self.move(selected,None,None);self.assertEqual(p['placed'],71)
        p=self.move(self.cages[0]['id'],1,1);self.assertEqual(p['placed'],72)
        p=self.move(selected,1,1);self.assertEqual(self.pos(p,self.cages[0]['id']),(None,None));self.assertEqual(p['placed'],72)
    def test_state_age_and_retired_cage(self):
        b=self.s.board(self.rack);a=self.cages[0]['id'];self.s.card_state(self.rack,a,{'card_state':'quarantine','expected_revision':b['rack']['revision']})
        with self.assertRaises(ValidationError):self.s.card_state(self.rack,a,{'card_state':'empty','expected_revision':self.s.board(self.rack)['rack']['revision']})
        # 固定计算日，避免两个候选生日碰巧取整到同一周时，误把正常显示判成错误。
        with patch('db.today_text',return_value='2026-10-12'):
            c=self.s._age({'dob':None,'comment':'原始生日：2026-06-13 / 2026-06-17；无需对应单只。'})
        self.assertEqual(c['age_label'],'16–17周');self.assertEqual(self.s._age({'dob':None,'comment':''})['age_label'],'待确认')
        empty=self.db.create_cage({'rack_id':self.rack,'code':'retired'})
        self.s.board(self.rack);self.db.update_cage(empty['id'],{'active':0})
        self.assertNotIn(empty['id'],[x['id'] for x in self.s.board(self.rack)['cards']])

    def test_orientation_upgrade_preserves_mice_and_parks_old_column_nine(self):
        """模拟旧版8行9列数据库：升级仅改变布局约束，不改变动物及既有历史。

        原有效坐标继续使用，第9列整笼进入临时区；再次初始化应无重复迁移。
        """
        self.move(self.cages[0]['id'],2,8)
        with self.db.write() as c:
            mice_before=[dict(x) for x in c.execute('SELECT * FROM mice ORDER BY id')]
            layouts=[dict(x) for x in c.execute('SELECT * FROM rack_layouts')]
            positions=[dict(x) for x in c.execute('SELECT * FROM cage_positions')]
            history_before=c.execute('SELECT COUNT(*) FROM rack_layout_history').fetchone()[0]
            c.execute('DROP TABLE cage_positions');c.execute('DROP TABLE rack_layouts')
            c.execute("""CREATE TABLE rack_layouts(
                rack_id INTEGER PRIMARY KEY REFERENCES racks(id),
                rows INTEGER NOT NULL DEFAULT 8 CHECK(rows=8),
                columns INTEGER NOT NULL DEFAULT 9 CHECK(columns=9),
                revision INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL)""")
            c.execute("""CREATE TABLE cage_positions(
                cage_id INTEGER PRIMARY KEY REFERENCES cages(id),rack_id INTEGER NOT NULL REFERENCES racks(id),
                row_index INTEGER,column_index INTEGER,card_state TEXT,
                CHECK((row_index IS NULL AND column_index IS NULL) OR
                    (row_index IS NOT NULL AND column_index IS NOT NULL AND row_index BETWEEN 1 AND 8 AND column_index BETWEEN 1 AND 9)))""")
            for x in layouts:c.execute('INSERT INTO rack_layouts VALUES(?,?,?,?,?)',(x['rack_id'],8,9,x['revision'],x['updated_at']))
            for x in positions:
                if x['cage_id']==self.cages[1]['id']:
                    x.update(row_index=2,column_index=9,card_state='quarantine')
                c.execute('INSERT INTO cage_positions VALUES(?,?,?,?,?)',tuple(x.values()))
        self.s.initialize();b=self.s.board(self.rack)
        self.assertEqual((b['rack']['rows'],b['rack']['columns']),(9,8))
        self.assertEqual(self.pos(b,self.cages[0]['id']),(2,8))
        self.assertEqual(self.pos(b,self.cages[1]['id']),(None,None))
        self.assertEqual(next(x['state'] for x in b['cards'] if x['id']==self.cages[1]['id']),'quarantine')
        self.assertIsNone(b['undo_id'])
        with self.db.read() as c:
            self.assertEqual([dict(x) for x in c.execute('SELECT * FROM mice ORDER BY id')],mice_before)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM rack_layout_history').fetchone()[0],history_before+len(layouts))
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
        revision=b['rack']['revision'];self.s.initialize()
        self.assertEqual(self.s.board(self.rack)['rack']['revision'],revision)

    def test_ninth_row_supported_and_ninth_column_rejected_in_sql(self):
        # I8是新架子的右下角；同时检查业务层与数据库约束。
        b=self.move(self.cages[0]['id'],9,8)
        self.assertEqual(self.pos(b,self.cages[0]['id']),(9,8))
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.write() as c:
                c.execute('UPDATE cage_positions SET column_index=9 WHERE cage_id=?',(self.cages[0]['id'],))

    def test_delete_empty_cage_keeps_history_and_prevents_undo(self):
        """空笼删除释放真实位置，同时保留已死亡鼠及移笼/实验事件的历史。"""
        cid=self.cages[0]['id'];mouse=self.cages[0]['created_mice'][0]
        self.db.add_event(mouse['id'],{'event_type':'观察','event_date':'2026-01-01','title':'DEMO history'})
        self.db.update_mouse(mouse['id'],{'status':'Dead','death_date':'2026-01-02'})
        # 最后一只鼠离笼会自动停用；模拟用户重新启用旧空笼后手动删除。
        self.db.update_cage(cid,{'active':1});self.move(cid,1,1)
        before=self.db.get_mouse(mouse['id']);revision=self.s.board(self.rack)['rack']['revision']
        receipt=self.db.delete_cage(cid);self.assertTrue(receipt['history_preserved'])
        b=self.s.board(self.rack);self.assertNotIn(cid,[x['id'] for x in b['cards']])
        self.assertGreater(b['rack']['revision'],revision);self.assertIsNone(b['undo_id'])
        self.assertEqual(self.db.get_cage(cid)['active'],0)
        after=self.db.get_mouse(mouse['id'])
        self.assertEqual(after['events'],before['events']);self.assertEqual(after['movements'],before['movements'])
        with self.assertRaises(ValidationError):self.s.undo(self.rack,{'expected_revision':b['rack']['revision']})

    def test_occupied_delete_rejected_and_rename_keeps_identity(self):
        """即使跳过网页按钮直接调用后端，也不能删除有在养鼠的笼。

        重命名只改变显示名称，动物所属笼和物理坐标稳定；空名/重名应拒绝。
        """
        cid=self.cages[0]['id'];self.move(cid,9,8)
        with self.assertRaises(ValidationError):self.db.delete_cage(cid)
        renamed=self.db.update_cage(cid,{'code':'DEMO renamed'})
        self.assertEqual(renamed['id'],cid);self.assertEqual(renamed['occupied'],1)
        self.assertEqual(self.pos(self.s.board(self.rack),cid),(9,8))
        for name in ['   ',self.cages[1]['code']]:
            with self.assertRaises(ValidationError):self.db.update_cage(cid,{'code':name})
        self.assertEqual(self.db.get_cage(cid)['code'],'DEMO renamed')

if __name__=='__main__':unittest.main()
