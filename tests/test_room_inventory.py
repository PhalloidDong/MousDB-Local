"""指定鼠房整体重建验收；全部使用临时合成鼠、笼和计划。"""
import tempfile,unittest,json,copy
from pathlib import Path
from db import Database,ValidationError
from rack_layout import RackLayoutStore
from experiment_plans import PlanStore
from room_inventory import RoomInventoryStore

class RoomInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Database(Path(self.temp.name)/'demo.sqlite3');self.db.initialize()
        self.room=self.db.create_reference('rooms',{'name':'DEMO target'})
        self.other=self.db.create_reference('rooms',{'name':'DEMO other'})
        self.rack=self.db.create_rack({'room_id':self.room['id'],'name':'DEMO rack'})
        self.outside_rack=self.db.create_rack({'room_id':self.other['id'],'name':'DEMO outside'})
        self.old=self.db.create_cage({'rack_id':self.rack['id'],'code':'DEMO old','mice':[{'sex':'M','ear_tag':'DEMO-OLD'}]})
        self.outside=self.db.create_cage({'rack_id':self.outside_rack['id'],'code':'DEMO outside','mice':[{'sex':'F','ear_tag':'DEMO-OUT'}]})
        self.layout=RackLayoutStore(self.db);self.layout.initialize();self.store=RoomInventoryStore(self.db)
    def tearDown(self):self.temp.cleanup()
    def payload(self):
        b=self.layout.board(self.rack['id'])
        return {'expected_room_name':self.room['name'],'expected_cage_ids':[self.old['id']],
                'expected_mouse_ids':[self.old['created_mice'][0]['id']],
                'expected_rack_revisions':{str(self.rack['id']):b['rack']['revision']},'reason':'DEMO user replacement',
                'cages':[{'rack_id':self.rack['id'],'code':'DEMO old','row':6,'column':7,'card_state':'breeding',
                          'mice':[{'sex':'M','ear_tag':'DEMO-NEW-M'},{'sex':'F','ear_tag':'DEMO-NEW-F'}],
                          'genotyping':{'pending':True,'birth_dates':['2026-10-01','2026-09-08'],'pup_count':None}}]}
    def test_replace_is_room_scoped_and_ids_never_reused(self):
        outside_before=self.db.get_mouse(self.outside['created_mice'][0]['id'])
        old_id=self.old['created_mice'][0]['id'];old_cage=self.old['id']
        result=self.store.replace(self.room['id'],self.payload())
        self.assertEqual((result['removed_mice'],result['created_mice']),(1,2))
        self.assertEqual(self.db.get_mouse(outside_before['id']),outside_before)
        fresh=result['created_cages'][0];self.assertGreater(fresh['cage_id'],old_cage)
        self.assertTrue(all(mid>old_id for mid in fresh['mouse_ids']))
        with self.db.read() as c:
            archive=json.loads(c.execute('SELECT snapshot_json FROM room_inventory_replacements').fetchone()[0])
            self.assertEqual(archive['mice'][0]['ear_tag'],'DEMO-OLD')
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
        with self.assertRaises(ValidationError):self.db.get_mouse(old_id)
    def test_invalid_mouse_rolls_back_renames_creation_and_archive(self):
        p=self.payload();p['cages'][0]['mice'][1]['sex']='invalid'
        with self.assertRaises(ValidationError):self.store.replace(self.room['id'],p)
        self.assertEqual(self.db.get_cage(self.old['id'])['code'],'DEMO old')
        self.assertEqual(self.db.get_cage(self.old['id'])['occupied'],1)
        with self.db.read() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM room_inventory_replacements').fetchone()[0],0)
    def test_stale_inventory_and_cross_room_slots_rejected(self):
        p=self.payload();p['expected_mouse_ids']=[]
        with self.assertRaises(ValidationError):self.store.replace(self.room['id'],p)
        p=self.payload();p['cages'][0]['rack_id']=self.outside_rack['id']
        with self.assertRaises(ValidationError):self.store.replace(self.room['id'],p)
    def test_external_pedigree_and_plans_block_deletion(self):
        self.db.update_mouse(self.outside['created_mice'][0]['id'],{'father_id':self.old['created_mice'][0]['id']})
        with self.assertRaises(ValidationError):self.store.replace(self.room['id'],self.payload())
        self.db.update_mouse(self.outside['created_mice'][0]['id'],{'father_id':None})
        plans=PlanStore(self.db);plans.initialize();plans.save({'title':'DEMO planned','plan_type':'取材','plan_date':'2026-11-02','mouse_ids':[self.old['created_mice'][0]['id']]})
        with self.assertRaises(ValidationError):self.store.replace(self.room['id'],self.payload())
    def test_unknown_pups_are_flagged_without_inventing_individuals(self):
        result=self.store.replace(self.room['id'],self.payload());cid=result['created_cages'][0]['cage_id']
        card=self.layout.board(self.rack['id'])['cards'][0]
        self.assertEqual(card['state'],'breeding');self.assertTrue(card['genotyping']['pending'])
        self.assertEqual(card['genotyping']['birth_dates'],['2026-09-08','2026-10-01']);self.assertIsNone(card['genotyping']['pup_count'])
        self.assertEqual(card['occupied'],2)
        edited=self.db.update_cage(cid,{'genotyping':{'pending':False}})
        self.assertFalse(edited['genotyping']['pending']);self.assertEqual(len(edited['genotyping']['birth_dates']),2)
    def test_duplicate_ears_or_slots_rejected(self):
        p=self.payload();p['cages'][0]['mice'][1]['ear_tag']='DEMO-NEW-M'
        with self.assertRaises(ValidationError):self.store.replace(self.room['id'],p)
        p=self.payload();second=copy.deepcopy(p['cages'][0]);second['code']='DEMO second';p['cages'].append(second)
        with self.assertRaises(ValidationError):self.store.replace(self.room['id'],p)
    def test_reapplying_stale_request_cannot_duplicate_inventory(self):
        p=self.payload();self.store.replace(self.room['id'],p)
        with self.assertRaises(ValidationError):self.store.replace(self.room['id'],p)
        self.assertEqual(self.layout.board(self.rack['id'])['mouse_count'],2)

    def test_unregistered_pups_keep_cage_when_last_parent_leaves(self):
        # 亲本离笼后还有无耳号幼鼠：不能按0个已登记个体自动删除笼位。
        result=self.store.replace(self.room['id'],self.payload());fresh=result['created_cages'][0]
        for mid in fresh['mouse_ids']:self.db.update_mouse(mid,{'cage_id':None})
        cage=self.db.get_cage(fresh['cage_id']);self.assertEqual(cage['active'],1);self.assertEqual(cage['occupied'],0)
        board=self.layout.board(self.rack['id']);self.assertEqual(len(board['cards']),1);self.assertTrue(board['cards'][0]['genotyping']['pending'])
        with self.assertRaises(ValidationError):self.db.delete_cage(fresh['cage_id'])
        with self.assertRaises(ValidationError):self.layout.card_state(self.rack['id'],fresh['cage_id'],{'card_state':'empty','expected_revision':board['rack']['revision']})

if __name__=='__main__':unittest.main()
