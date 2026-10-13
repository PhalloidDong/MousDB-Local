"""架位事务的关键验收；全部在临时SQLite中执行，不改真实动物房数据。
覆盖整笼交换、72格全满仍可停放、撤销、刷新、并发过期与建笼回滚。
"""
import tempfile,unittest,sqlite3
from pathlib import Path
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
        for row,col in [(9,1),(1,10),(None,1),(1,None),(-1,1),(1.5,2)]:
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
                c.execute('INSERT INTO cage_positions(cage_id,rack_id,row_index,column_index) VALUES(?,?,?,?)',(cage,self.rack,i//9+1,i%9+1))
        b=self.s.board(self.rack);self.assertEqual(b['placed'],72)
        selected=next(x['id'] for x in b['cards'] if x['row_index']==1 and x['column_index']==1)
        p=self.move(selected,None,None);self.assertEqual(p['placed'],71)
        p=self.move(self.cages[0]['id'],1,1);self.assertEqual(p['placed'],72)
        p=self.move(selected,1,1);self.assertEqual(self.pos(p,self.cages[0]['id']),(None,None));self.assertEqual(p['placed'],72)
    def test_state_age_and_retired_cage(self):
        b=self.s.board(self.rack);a=self.cages[0]['id'];self.s.card_state(self.rack,a,{'card_state':'quarantine','expected_revision':b['rack']['revision']})
        with self.assertRaises(ValidationError):self.s.card_state(self.rack,a,{'card_state':'empty','expected_revision':self.s.board(self.rack)['rack']['revision']})
        c=self.s._age({'dob':None,'comment':'原始生日：2026-06-13 / 2026-06-17；无需对应单只。'})
        self.assertIn('–',c['age_label']);self.assertEqual(self.s._age({'dob':None,'comment':''})['age_label'],'待确认')
        empty=self.db.create_cage({'rack_id':self.rack,'code':'retired'})
        self.s.board(self.rack);self.db.update_cage(empty['id'],{'active':0})
        self.assertNotIn(empty['id'],[x['id'] for x in self.s.board(self.rack)['cards']])

if __name__=='__main__':unittest.main()
