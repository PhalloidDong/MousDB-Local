"""鉴定图片业务/HTTP验收：仅使用临时数据库和代码生成的DEMO图片。"""
import base64,json,tempfile,threading,unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request,urlopen
from unittest.mock import patch
import app
from db import Database,ValidationError
from genotyping_media import GenotypingMediaStore,MAX_IMAGE_BYTES
from mousdb_cli import initialize,dispatch

# 合成1像素PNG；与任何真实实验照片、鼠号或鉴定结果无关。
DEMO_PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')

class GenotypingMediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.folder=Path(self.tmp.name)
        self.db=Database(self.folder/'demo.sqlite3');initialize(self.db)
        self.store=GenotypingMediaStore(self.db)
    def tearDown(self):self.tmp.cleanup()

    def test_metadata_images_and_backup_preserve_original_bytes(self):
        """图片进入同一个私有SQLite，普通备份和重启后仍能完整读取原始字节。"""
        card=self.store.save({'category':'protocol','title':'DEMO protocol','gene':'DEMO-Driver'})
        uploaded=self.store.add_image(card['id'],DEMO_PNG,'DEMO-原图.png')
        photo=uploaded['images'][0];self.assertEqual(photo['byte_size'],len(DEMO_PNG))
        self.assertNotIn('content',photo);self.assertEqual(self.store.image(photo['id']),(DEMO_PNG,'image/png'))
        self.store.save({'title':'DEMO renamed','note':'manual note'},card['id'])
        self.assertEqual(self.store.get(card['id'])['image_count'],1)
        backup=self.folder/'backup.sqlite3';self.db.backup_to(backup)
        copy=GenotypingMediaStore(Database(backup));copy.initialize()
        self.assertEqual(copy.image(photo['id'])[0],DEMO_PNG)
        self.assertEqual(copy.get(card['id'])['title'],'DEMO renamed')

    def test_result_grouping_survives_partial_edits_and_rejects_invalid_dates(self):
        """输入独立品系/归档日；改标题保留分类和原图，无效日期整笔回滚。"""
        card=self.store.save({'category':'result','title':'DEMO batch','strain':'DEMO lineage','record_date':'2026-02-28'})
        photo=self.store.add_image(card['id'],DEMO_PNG,'DEMO.png')['images'][0]
        edited=self.store.save({'title':'DEMO renamed'},card['id'])
        self.assertEqual((edited['strain'],edited['record_date']),('DEMO lineage','2026-02-28'))
        for day in ['2026-02-30','2026-2-01','not-a-date']:
            with self.assertRaises(ValidationError):self.store.save({'record_date':day,'title':'must rollback'},card['id'])
            self.assertEqual(self.store.get(card['id']),edited)
        self.assertEqual(self.store.image(photo['id'])[0],DEMO_PNG)
        self.assertEqual(len(self.store.list('result')),1)

    def test_legacy_schema_migrates_without_guessing_or_changing_evidence(self):
        """模拟旧版库缺少两列；升级两次仍幂等，未知分类留空且原图和备注不变。"""
        card=self.store.save({'category':'result','title':'DEMO old','note':'DEMO evidence'})
        photo=self.store.add_image(card['id'],DEMO_PNG,'DEMO.png')['images'][0]
        with self.db.write() as conn:
            # 只在TemporaryDirectory合成库模拟旧结构，绝不对正式数据执行删列。
            conn.execute('ALTER TABLE genotyping_documents DROP COLUMN strain')
            conn.execute('ALTER TABLE genotyping_documents DROP COLUMN record_date')
        self.store.initialize();self.store.initialize()
        migrated=self.store.get(card['id'])
        self.assertEqual((migrated['strain'],migrated['record_date']),('',''))
        self.assertEqual((migrated['title'],migrated['note']),('DEMO old','DEMO evidence'))
        self.assertEqual(self.store.image(photo['id'])[0],DEMO_PNG)

    def test_categories_validation_and_files_do_not_use_paths(self):
        """目录隔离，拒绝无效文件；文件名即使含路径也只保存末级名称。"""
        card=self.store.save({'category':'result','title':'DEMO result'})
        self.assertEqual(self.store.list('protocol'),[])
        self.assertEqual(len(self.store.list('result')),1)
        for payload in [{'category':'bad','title':'x'},{'category':'protocol','title':''}]:
            with self.assertRaises(ValidationError):self.store.save(payload)
        with self.assertRaises(ValidationError):self.store.list('bad')
        for content in [b'',b'<svg onload="bad()"/>',b'<html>image</html>']:
            with self.assertRaises(ValidationError):self.store.add_image(card['id'],content,'DEMO.png')
        with patch('genotyping_media.MAX_IMAGE_BYTES',4):
            with self.assertRaises(ValidationError):self.store.add_image(card['id'],DEMO_PNG,'DEMO.png')
        uploaded=self.store.add_image(card['id'],DEMO_PNG,'../../DEMO.png')
        self.assertEqual(uploaded['images'][0]['filename'],'DEMO.png')
        with self.assertRaises(ValidationError):self.store.add_image(999,DEMO_PNG,'DEMO.png')

    def test_archive_hides_but_keeps_evidence_and_animal_rows(self):
        """移除只停用，私有原图证据还在；所有鼠字段/计划表不受影响。"""
        mouse=self.db.create_mouse({'ear_tag':'DEMO-001','sex':'F'})
        before=self.db.get_mouse(mouse['id'])
        card=self.store.save({'category':'protocol','title':'DEMO'})
        card=self.store.add_image(card['id'],DEMO_PNG,'DEMO-a.png')
        first=card['images'][0]['id']
        self.assertEqual(self.store.remove_image(first)['image_count'],0)
        with self.assertRaises(ValidationError):self.store.image(first)
        card=self.store.add_image(card['id'],DEMO_PNG,'DEMO-b.png')
        second=card['images'][0]['id'];self.store.archive(card['id'])
        self.assertFalse(self.store.list('protocol'))
        with self.assertRaises(ValidationError):self.store.image(second)
        with self.db.read() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM genotyping_images').fetchone()[0],2)
            self.assertEqual(conn.execute('SELECT content FROM genotyping_images WHERE id=?',(first,)).fetchone()[0],DEMO_PNG)
            self.assertFalse(conn.execute('PRAGMA foreign_key_check').fetchall())
        self.assertEqual(self.db.get_mouse(mouse['id']),before)

    def test_codex_and_web_share_the_same_store(self):
        """聊天CLI传入本机文件，网页业务层能读取完全相同的卡片与图片。"""
        card=dispatch(self.db,{'action':'genotyping.create','payload':{'category':'protocol','title':'DEMO'}})
        image=self.folder/'DEMO.png';image.write_bytes(DEMO_PNG)
        uploaded=dispatch(self.db,{'action':'genotyping.image.add','params':{'document_id':card['id'],'file_path':str(image)}})
        self.assertEqual(uploaded['image_count'],1)
        self.assertEqual(dispatch(self.db,{'action':'genotyping.get','params':{'document_id':card['id']}}),self.store.get(card['id']))

    def test_http_raw_upload_and_inline_private_image(self):
        """实际HTTP原图往返、中文文件名、上传限额和移除权限均由服务器验证。"""
        with patch.object(app,'DB',self.db):
            server=app.create_server(0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_address[1]}'
            def send(path,method='GET',data=None,content_type='application/json'):
                req=Request(base+path,method=method,data=data,headers={'Content-Type':content_type})
                return urlopen(req,timeout=5)
            try:
                with send('/api/genotyping-documents','POST',json.dumps({'category':'protocol','title':'DEMO'}).encode()) as response:
                    card=json.load(response)['data']
                with send(f'/api/genotyping-documents/{card["id"]}/images?filename='+quote('DEMO-原图.png'),'POST',DEMO_PNG,'image/png') as response:
                    uploaded=json.load(response)['data']
                photo=uploaded['images'][0]
                with send(photo['url']) as response:
                    self.assertEqual(response.read(),DEMO_PNG)
                    self.assertEqual(response.headers['Content-Type'],'image/png')
                    self.assertEqual(response.headers['Cache-Control'],'private, no-store')
                    self.assertEqual(response.headers['X-Content-Type-Options'],'nosniff')
                with send('/api/genotyping-documents?category=result') as response:self.assertFalse(json.load(response)['data'])
                with self.assertRaises(HTTPError) as bad:
                    send(f'/api/genotyping-documents/{card["id"]}/images?filename=DEMO.jpg','POST',b'<html>bad</html>','image/jpeg')
                self.assertEqual(bad.exception.code,400)
                with patch('app.MAX_IMAGE_BYTES',1):
                    with self.assertRaises(HTTPError) as large:send(f'/api/genotyping-documents/{card["id"]}/images?filename=DEMO.png','POST',DEMO_PNG,'image/png')
                    self.assertEqual(large.exception.code,400)
                with send(f'/api/genotyping-documents/{card["id"]}','DELETE') as response:self.assertTrue(json.load(response)['data']['archived'])
                with self.assertRaises(HTTPError):send(photo['url'])
            finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
