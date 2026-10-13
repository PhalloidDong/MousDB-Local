import tempfile
import unittest
from pathlib import Path
from db import Database, ValidationError

class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.db=Database(Path(self.tmp.name)/"x.sqlite3"); self.db.initialize()
    def tearDown(self): self.tmp.cleanup()
    def test_08_preview_commit_autocreate_and_ignore_code(self):
        text="小鼠编号,耳标,性别,出生日期,房间,架位,笼号,strain,line,genotype\nUSER-1,E1,M,2026/01/02,R1,A,C1,S1,L1,WT\n"
        pre=self.db.preview_import({"csv_text":text,"auto_create":True}); self.assertTrue(pre["valid"]); self.assertTrue(pre["ignored_code_column"])
        result=self.db.commit_import({"csv_text":text,"auto_create":True}); self.assertEqual(result["created_mice"][0]["code"],"M0001"); self.assertEqual(self.db.get_cage(1)["ear_tags"],["E1"])
    def test_09_bad_row_rolls_back_batch(self):
        text="耳标,性别\nE1,M\nE2,X\n"
        self.assertFalse(self.db.preview_import({"csv_text":text})["valid"])
        with self.assertRaises(ValidationError): self.db.commit_import({"csv_text":text})
        self.assertEqual(self.db.list_mice(),[])

