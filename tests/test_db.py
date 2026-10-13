"""核心业务验收。每个测试都使用独立临时数据库，不接触 data/ 正式文件。"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from db import Database, ValidationError, normalize_rich_name


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/"test.sqlite3"
        self.db=Database(self.path); self.db.initialize()
        self.room=self.db.create_reference("rooms",{"name":"R1"})
        self.rack=self.db.create_rack({"room_id":self.room["id"],"name":"A"})
        self.cage=self.db.create_cage({"rack_id":self.rack["id"],"code":"C01","capacity":1})
        self.strain=self.db.create_reference("strains",{"name":"C57BL/6J"})
        self.line=self.db.create_reference("lines",{"name":"Col1a1-Cre"})
    def tearDown(self): self.tmp.cleanup()

    def test_01_first_code_and_client_code_ignored(self):
        mouse=self.db.create_mouse({"code":"HACK-1","sex":"M"})
        self.assertEqual(mouse["code"],"M0001")

    def test_02_number_format_continues_and_edit_is_immutable(self):
        first=self.db.create_mouse({"sex":"M"})
        with self.db.write() as conn: conn.execute("UPDATE mice SET code='M2026-007' WHERE id=?",(first["id"],))
        second=self.db.create_mouse({"sex":"F"}); self.assertEqual(second["code"],"M2026-008")
        changed=self.db.update_mouse(second["id"],{"code":"ILLEGAL","owner":"A"})
        self.assertEqual(changed["code"],"M2026-008")

    def test_03_movement_warnings_and_death_sync(self):
        male=self.db.create_mouse({"sex":"M","cage_id":self.cage["id"],"strain_id":self.strain["id"],"line_id":self.line["id"],"dob":"2026-01-01"})
        female=self.db.create_mouse({"sex":"F","cage_id":self.cage["id"]})
        self.assertTrue(any("超过" in x for x in female["warnings"])); self.assertTrue(any("雌雄" in x for x in female["warnings"]))
        dead=self.db.update_mouse(male["id"],{"status":"Dead","death_date":"2026-02-01","death_reason":"自然死亡"})
        self.assertIsNone(dead["cage_id"]); self.assertEqual(dead["age_days"],31)
        self.assertNotIn(male["id"],[x["id"] for x in self.db.list_mice("alive")]); self.assertIn(male["id"],[x["id"] for x in self.db.list_mice("dead")])
        cage=self.db.get_cage(self.cage["id"]); self.assertEqual(cage["occupied"],1); self.assertNotIn("Col1a1-Cre",cage["lines"])
        self.assertTrue(any(x["note"]=="记录死亡，自动移出笼位" for x in dead["movements"]))
        self.assertIn("M0001",self.db.export_csv("dead").decode("utf-8-sig")); self.assertNotIn("M0001",self.db.export_csv("alive").decode("utf-8-sig"))

    def test_04_cage_with_multiple_mice_is_atomic(self):
        cage=self.db.create_cage({"rack_id":self.rack["id"],"code":"C02","mice":[{"sex":"M","ear_tag":"1","line_id":self.line["id"]},{"sex":"F","ear_tag":"2"}]})
        self.assertEqual([x["code"] for x in cage["created_mice"]],["M0001","M0002"])
        detail=self.db.get_cage(cage["id"]); self.assertEqual(detail["ear_tags"],["1","2"]); self.assertIn("Col1a1-Cre",detail["lines"])
        with self.assertRaises(ValidationError): self.db.create_cage({"rack_id":self.rack["id"],"code":"ROLLBACK","mice":[{"sex":"M"},{"sex":"X"}]})
        self.assertFalse(any(x["code"]=="ROLLBACK" for x in self.db.list_cages()))

    def test_05_strain_filter_and_duplicate_ear_warning(self):
        self.db.create_mouse({"sex":"M","strain_id":self.strain["id"],"ear_tag":"E1"})
        warning=self.db.create_mouse({"sex":"F","ear_tag":"E1"})["warnings"]
        self.assertTrue(warning); self.assertEqual(len(self.db.list_mice(strain_id=self.strain["id"])),1)

    def test_06_mating_litter_weaning_parentage(self):
        male=self.db.create_mouse({"sex":"M"}); female=self.db.create_mouse({"sex":"F"})
        mating=self.db.create_mating({"code":"MAT-1","male_id":male["id"],"female_ids":[female["id"]],"start_date":"2026-01-01","offspring_strain_id":self.strain["id"],"offspring_line_id":self.line["id"]})
        litter=self.db.create_litter(mating["id"],{"code":"L-1","mother_id":female["id"],"birth_date":"2026-02-01","male_count":2,"female_count":1,"unknown_count":1,"dead_count":1})
        self.assertEqual(litter["total_born"],5)
        result=self.db.wean_litter(litter["id"],{"weaned_date":"2026-02-22","male_cage_id":self.cage["id"],"female_cage_id":self.cage["id"]})
        self.assertEqual(len(result["created_mice"]),4); child=result["created_mice"][0]
        self.assertEqual((child["father_id"],child["mother_id"]),(male["id"],female["id"]))

    def test_07_backup_integrity(self):
        self.db.create_mouse({"sex":"U"}); backup=Path(self.tmp.name)/"backup.sqlite3"; self.db.backup_to(backup)
        conn=sqlite3.connect(backup)
        try: self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0],"ok")
        finally: conn.close()

    def test_08_transferred_mouse_leaves_cage_but_is_not_dead(self):
        mouse=self.db.create_mouse({"sex":"U","cage_id":self.cage["id"]})
        moved=self.db.update_mouse(mouse["id"],{"status":"Transferred","transfer_destination":"其他实验室"})
        self.assertIsNone(moved["cage_id"]);self.assertTrue(moved["transfer_date"])
        self.assertEqual(self.db.list_mice("dead"),[]);self.assertEqual(self.db.get_cage(self.cage["id"])["occupied"],0)

    def test_09_reference_casefold_and_deactivation_preserve_history(self):
        with self.assertRaises(ValidationError): self.db.create_reference("strains",{"name":"c57bl/6j"})
        mouse=self.db.create_mouse({"strain_id":self.strain["id"]})
        self.db.update_reference("strains",self.strain["id"],{"active":False})
        self.assertEqual(self.db.get_mouse(mouse["id"])["strain_name"],"C57BL/6J")

    def test_10_event_and_cage_edit(self):
        mouse=self.db.create_mouse({"sex":"M"});event=self.db.add_event(mouse["id"],{"event_type":"给药","event_date":"2026-01-03","title":"PBS","details":"腹腔"})
        self.assertEqual(event["title"],"PBS");self.assertEqual(self.db.get_mouse(mouse["id"])["events"][0]["event_type"],"给药")
        updated=self.db.update_cage(self.cage["id"],{"capacity":3,"purpose":"繁育"});self.assertEqual(updated["capacity"],3)

    def test_11_direct_rich_reference_input_is_saved_and_deduplicated(self):
        mouse=self.db.create_mouse({
            "sex":"M",
            "strain_name":"Col1a1-Cre+",
            "strain_html":"Col1a1-Cre<sup>+</sup>",
            "line_name":"α-SMA",
            "line_html":"<i>α-SMA</i>",
            "genotype_name":"flox/flox",
            "genotype_html":"flox<sup>/</sup>flox",
        })
        self.assertEqual(mouse["strain_name"],"Col1a1-Cre+")
        refs=self.db.list_references()
        strain=next(x for x in refs["strains"] if x["name"]=="Col1a1-Cre+")
        line=next(x for x in refs["lines"] if x["name"]=="α-SMA")
        self.assertEqual(strain["name_html"],"Col1a1-Cre<sup>+</sup>")
        self.assertEqual(line["name_html"],"<i>α-SMA</i>")
        genotype=next(x for x in refs["genotypes"] if x["name"]=="flox/flox")
        self.assertEqual(genotype["name_html"],"flox<sup>/</sup>flox")
        # 同名但不同大小写再次输入时复用旧词条，不创建第二个字典项目。
        second=self.db.create_mouse({"sex":"F","strain_name":"col1a1-cre+","strain_html":"<b>col1a1-cre+</b>"})
        self.assertEqual(second["strain_id"],mouse["strain_id"])
        self.assertEqual(len([x for x in self.db.list_references()["strains"] if x["name"].casefold()=="col1a1-cre+"]),1)

    def test_12_rich_input_strips_unsafe_tags_but_keeps_special_format(self):
        plain, safe=normalize_rich_name('<script>alert(1)</script><sup>α</sup><i>Cre+</i>','fallback')
        self.assertEqual(plain,"alert(1)αCre+"); self.assertEqual(safe,"alert(1)<sup>α</sup><i>Cre+</i>"); self.assertNotIn("script",safe.lower())


    def test_13_relation_text_requires_a_selected_database_record(self):
        # 文本框只是搜索入口；没有候选项 ID 时，后端必须拒绝保存，避免丢失谱系关系。
        with self.assertRaises(ValidationError):
            self.db.create_mouse({"sex":"M", "cage_input":"R1 / A / C01"})
        male=self.db.create_mouse({"sex":"M"})
        with self.assertRaises(ValidationError):
            self.db.create_mouse({"sex":"F", "father_input":"M0001"})
        child=self.db.create_mouse({"sex":"F", "father_id":male["id"], "father_input":"M0001"})
        self.assertEqual(child["father_id"],male["id"])

if __name__=="__main__": unittest.main()
