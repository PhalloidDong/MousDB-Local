import sqlite3,tempfile,unittest
from pathlib import Path
from db import Database
from migrate_existing_data import migrate_database

class MigrationTests(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def test_11_python_database_preserved_and_target_backed_up(self):
        source=self.root/"source.sqlite3";src=Database(source);src.initialize();src.create_mouse({"sex":"M"})
        target=self.root/"target.sqlite3";old=Database(target);old.initialize();old.create_mouse({"sex":"F"})
        result=migrate_database(source,target);self.assertEqual(result["integrity"],"ok");self.assertTrue(Path(result["backup"]).exists());self.assertEqual(Database(target).list_mice("all")[0]["code"],"M0001")
    def test_12_early_node_database_converted(self):
        source=self.root/"node.sqlite";
        c=sqlite3.connect(source)
        try:
            c.execute("CREATE TABLE cages(id INTEGER PRIMARY KEY, code TEXT, room TEXT, rack TEXT, capacity INTEGER)")
            c.execute("INSERT INTO cages VALUES(9,'OLD-C9','OldRoom','OldRack',4)")
            c.execute("CREATE TABLE mice(id INTEGER PRIMARY KEY, code TEXT, ear_tag TEXT, sex TEXT, strain TEXT, genotype TEXT, notes TEXT, cage_id INTEGER)")
            c.execute("INSERT INTO mice VALUES(1,'OLD-7','E7','F','S1','WT','legacy',9)")
            c.execute("CREATE TABLE activities(id INTEGER PRIMARY KEY, mouse_id INTEGER, date TEXT, title TEXT, notes TEXT)")
            c.execute("INSERT INTO activities VALUES(1,1,'2026-01-02','称重','20g')")
            c.commit()
        finally: c.close()
        target=self.root/"new.sqlite3";result=migrate_database(source,target);self.assertEqual(result["kind"],"node")
        mouse=Database(target).list_mice("all")[0];self.assertEqual(mouse["code"],"OLD-7");self.assertEqual(mouse["strain_name"],"S1");self.assertEqual(mouse["cage_code"],"OLD-C9")
        self.assertEqual(Database(target).get_mouse(mouse["id"])["events"][0]["title"],"称重")

if __name__=="__main__":unittest.main()
