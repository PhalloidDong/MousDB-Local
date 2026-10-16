"""鉴定程序/结果的本机图片资料库。

输入：用户填写的标题、基因名、备注及图片原始字节；输出：卡片元数据或原图。
图片以BLOB保存在同一个私有SQLite中，一致性备份自动包含图片，不依赖作者电脑路径。
本模块不做OCR、条带判断、基因型推断，也不修改任何小鼠、笼位或实验计划。
"""
from __future__ import annotations
import sqlite3
from db import Database, ValidationError, now_text

MAX_IMAGE_BYTES = 8 * 1024 * 1024
CATEGORIES = {'protocol': '鉴定程序', 'result': '鉴定结果'}


class GenotypingMediaStore:
    def __init__(self, database: Database):
        self.db = database

    def initialize(self):
        """幂等创建独立资料表；既有动物表和字段保持原样。"""
        with self.db.write() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS genotyping_documents(
                id INTEGER PRIMARY KEY,category TEXT NOT NULL CHECK(category IN ('protocol','result')),
                title TEXT NOT NULL,gene TEXT NOT NULL DEFAULT '',note TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
            conn.execute('''CREATE TABLE IF NOT EXISTS genotyping_images(
                id INTEGER PRIMARY KEY,document_id INTEGER NOT NULL REFERENCES genotyping_documents(id),
                filename TEXT NOT NULL,mime_type TEXT NOT NULL,byte_size INTEGER NOT NULL,
                content BLOB NOT NULL,active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                created_at TEXT NOT NULL)''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_genotyping_images_document ON genotyping_images(document_id,active)')

    @staticmethod
    def _text(value, label, maximum, required=False):
        """只校验手工元数据的长度和必填项；不提取或解释图片内容。"""
        value = str(value or '').strip()
        if required and not value:
            raise ValidationError(label + '不能为空')
        if len(value) > maximum:
            raise ValidationError(f'{label}不能超过{maximum}个字符')
        return value

    @staticmethod
    def _row(conn, document_id):
        row = conn.execute('SELECT * FROM genotyping_documents WHERE id=? AND active=1', (document_id,)).fetchone()
        if row is None:
            raise ValidationError('这张鉴定资料卡不存在或已移除')
        return row

    @staticmethod
    def _metadata(conn, row):
        """列表和详情只查询元数据，绝不把图片BLOB塞进JSON或操作日志。"""
        item = dict(row)
        item['images'] = [dict(r) for r in conn.execute(
            'SELECT id,filename,mime_type,byte_size,created_at FROM genotyping_images WHERE document_id=? AND active=1 ORDER BY id', (row['id'],))]
        for photo in item['images']:
            photo['url'] = f'/api/genotyping-images/{photo["id"]}'
        item['image_count'] = len(item['images'])
        item['cover_url'] = item['images'][0]['url'] if item['images'] else None
        return item

    def list(self, category):
        """输入目录protocol/result；输出该目录卡片和原图地址，不含图像识别结果。"""
        if category not in CATEGORIES:
            raise ValidationError('请选择鉴定程序或鉴定结果目录')
        with self.db.read() as conn:
            return [self._metadata(conn, row) for row in conn.execute(
                'SELECT * FROM genotyping_documents WHERE category=? AND active=1 ORDER BY id', (category,))]

    def get(self, document_id):
        with self.db.read() as conn:
            return self._metadata(conn, self._row(conn, document_id))

    def save(self, payload, document_id=None):
        """新建/编辑卡片文字；已有图片和原始字节不因编辑标题而重写。"""
        with self.db.write() as conn:
            old = dict(self._row(conn, document_id)) if document_id is not None else {}
            category = payload.get('category', old.get('category'))
            if category not in CATEGORIES:
                raise ValidationError('资料目录无效')
            title = self._text(payload.get('title', old.get('title')), '标题', 200, True)
            gene = self._text(payload.get('gene', old.get('gene')), '基因名称', 150)
            note = self._text(payload.get('note', old.get('note')), '备注', 20000)
            stamp = now_text()
            if old:
                conn.execute('UPDATE genotyping_documents SET category=?,title=?,gene=?,note=?,updated_at=? WHERE id=?',
                             (category, title, gene, note, stamp, document_id))
            else:
                document_id = conn.execute('INSERT INTO genotyping_documents(category,title,gene,note,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                                           (category, title, gene, note, stamp, stamp)).lastrowid
            self.db.audit(conn, 'update' if old else 'create', 'genotyping_document', document_id, '保存' + CATEGORIES[category] + '资料卡')
            return self._metadata(conn, self._row(conn, document_id))

    @staticmethod
    def image_type(content):
        """只检查文件格式签名和大小；排除HTML/SVG，原图不压缩、不裁剪。"""
        if not content or len(content) > MAX_IMAGE_BYTES:
            raise ValidationError('每张图片须大于0字节且不超过8 MB')
        if content.startswith(b'\xff\xd8\xff'):
            return 'image/jpeg'
        if content.startswith(b'\x89PNG\r\n\x1a\n'):
            return 'image/png'
        if len(content) >= 12 and content[:4] == b'RIFF' and content[8:12] == b'WEBP':
            return 'image/webp'
        raise ValidationError('请上传JPG、PNG或WebP图片')

    def add_image(self, document_id, content, filename):
        """一次事务保存一张原图；文件名只作为展示文字，不拼接为磁盘路径。"""
        mime_type = self.image_type(content)
        filename = self._text(str(filename or '').replace('\\', '/').split('/')[-1], '图片名称', 255, True)
        with self.db.write() as conn:
            self._row(conn, document_id)
            stamp = now_text()
            conn.execute('INSERT INTO genotyping_images(document_id,filename,mime_type,byte_size,content,created_at) VALUES(?,?,?,?,?,?)',
                         (document_id, filename, mime_type, len(content), sqlite3.Binary(content), stamp))
            conn.execute('UPDATE genotyping_documents SET updated_at=? WHERE id=?', (stamp, document_id))
            self.db.audit(conn, 'create', 'genotyping_document', document_id, '添加原图；未提取内容')
            return self._metadata(conn, self._row(conn, document_id))

    def image(self, image_id):
        """只允许读取未移除资料的图片；输出原字节和经过签名核对的MIME。"""
        with self.db.read() as conn:
            row = conn.execute('''SELECT i.content,i.mime_type FROM genotyping_images i
                JOIN genotyping_documents d ON d.id=i.document_id
                WHERE i.id=? AND i.active=1 AND d.active=1''', (image_id,)).fetchone()
            if row is None:
                raise ValidationError('图片不存在或已移除')
            return bytes(row['content']), row['mime_type']

    def archive(self, document_id):
        """界面移除卡片采用停用方式；原图仍保留在私有数据库和历史备份中。"""
        with self.db.write() as conn:
            self._row(conn, document_id)
            conn.execute('UPDATE genotyping_documents SET active=0,updated_at=? WHERE id=?', (now_text(), document_id))
            self.db.audit(conn, 'archive', 'genotyping_document', document_id, '移除鉴定资料卡；原图保留')
        return {'id': document_id, 'archived': True}

    def remove_image(self, image_id):
        """移除误传的单图也只停用，不删除原始实验证据。"""
        with self.db.write() as conn:
            row = conn.execute('SELECT document_id FROM genotyping_images WHERE id=? AND active=1', (image_id,)).fetchone()
            if row is None:
                raise ValidationError('图片不存在或已移除')
            self._row(conn, row['document_id'])
            conn.execute('UPDATE genotyping_images SET active=0 WHERE id=?', (image_id,))
            conn.execute('UPDATE genotyping_documents SET updated_at=? WHERE id=?', (now_text(), row['document_id']))
            self.db.audit(conn, 'archive', 'genotyping_document', row['document_id'], '移除一张鉴定图片；原图保留')
            return self._metadata(conn, self._row(conn, row['document_id']))
