"""MousDB Local 的数据库与业务规则层。

本文件刻意不依赖 Flask、SQLAlchemy 等第三方包。可以把它理解成三层：

输入：HTTP 接口或测试传入的 Python 字典；
处理：在 SQLite 事务中检查规则、生成编号、写入数据；
输出：适合直接转换为 JSON 的普通字典和列表。

最重要的科研数据安全规则（自动编号、死亡移笼、批量回滚）都在这里执行，
而不是只在网页里隐藏输入框。即使有人绕过网页直接调用 API，也不能绕过规则。
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator
from html.parser import HTMLParser


SCHEMA_VERSION = "1.4.2"
ALIVE_STATUSES = ("Active", "Experimental")
ALL_STATUSES = ("Active", "Experimental", "Transferred", "Dead")
DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y")


class ValidationError(ValueError):
    """用户数据不符合业务规则时抛出的可读错误。"""


class _RichNameSanitizer(HTMLParser):
    """只保留词条需要的轻量格式，防止用户输入 HTML/脚本注入。

    允许的标签只有粗体、斜体、上下标和下划线。所有属性、链接、图片、脚本和
    事件处理器都会被丢弃。这样既能保留科研命名中的格式，又不会把用户输入当作
    可执行网页代码。HTMLParser 会自动把常见实体（例如 ``&alpha;``）还原成字符。
    """

    ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "sup", "sub"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._open: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self.ALLOWED_TAGS:
            self.parts.append(f"<{tag}>")
            self._open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # 自闭合格式标签没有实际文字，忽略即可。
        return

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.ALLOWED_TAGS and tag in self._open:
            # 只关闭最近的同类标签；畸形 HTML 也不会泄漏出危险内容。
            self.parts.append(f"</{tag}>")
            for index in range(len(self._open) - 1, -1, -1):
                if self._open[index] == tag:
                    del self._open[index]
                    break

    def handle_data(self, data: str) -> None:
        self.parts.append(html.escape(data, quote=False))

    def finish(self) -> str:
        # 用户可能只输入了一个开始标签；补上标签使返回值总是合法片段。
        for tag in reversed(self._open):
            self.parts.append(f"</{tag}>")
        return "".join(self.parts).strip()


def sanitize_rich_html(value: Any) -> str:
    """清洗富文本 HTML，只返回白名单标签片段。"""

    text = str(value or "")
    if not text.strip():
        return ""
    parser = _RichNameSanitizer()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        # 富文本不是核心科研字段；格式损坏时退回为纯文本，不能阻止小鼠保存。
        return html.escape(re.sub(r"<[^>]*>", "", text), quote=False).strip()
    return parser.finish()


def rich_plain_text(value: Any) -> str:
    """从清洗后的富文本中提取用于唯一约束和搜索的纯文本。"""

    cleaned = sanitize_rich_html(value)
    parser = HTMLParser(convert_charrefs=True)
    parts: list[str] = []
    parser.handle_data = parts.append  # type: ignore[method-assign]
    try:
        parser.feed(cleaned)
        parser.close()
    except Exception:
        return re.sub(r"<[^>]*>", "", cleaned).strip()
    return "".join(parts).strip()


def normalize_rich_name(markup: Any, plain: Any = "") -> tuple[str, str]:
    """返回 ``(纯文本 name, 安全的 name_html)``。

    新版前端会发送 name_html；旧版 API 或迁移程序只发送 name 时，自动把纯文本
    HTML 转义后保存，因此旧调用方式仍然兼容。
    """

    source_html = str(markup or "").strip()
    if source_html:
        safe_html = sanitize_rich_html(source_html)
        plain_text = rich_plain_text(safe_html)
    else:
        plain_text = str(plain or "").strip()
        safe_html = html.escape(plain_text, quote=False)
    if not plain_text:
        raise ValidationError("名称不能为空")
    if len(plain_text) > 200:
        raise ValidationError("名称不能超过 200 个字符")
    return plain_text, safe_html


def now_text() -> str:
    """生成带秒的本地时间；SQLite 内统一保存可排序的 ISO 文本。"""

    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def today_text() -> str:
    return date.today().isoformat()


def parse_date(value: Any, field: str, *, required: bool = False) -> str | None:
    """把三种允许的日期写法统一为 YYYY-MM-DD。"""

    text = str(value or "").strip()
    if not text:
        if required:
            raise ValidationError(f"{field}不能为空")
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValidationError(f"{field}格式无效，请使用 YYYY-MM-DD、YYYY/MM/DD 或 DD.MM.YYYY")


def integer(value: Any, field: str, *, minimum: int = 0) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field}必须是整数") from None
    if result < minimum:
        raise ValidationError(f"{field}不能小于 {minimum}")
    return result


def optional_id(value: Any) -> int | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError("关联项目 ID 必须是整数") from None


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class Database:
    """一个数据库文件对应一个实例；测试可传入临时路径避免污染正式库。"""

    def __init__(self, path: str | Path, schema_path: str | Path | None = None):
        self.path = Path(path)
        self.schema_path = Path(schema_path or Path(__file__).with_name("schema.sql"))

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def initialize(self) -> None:
        """首次运行建表；重复执行不会删除或覆盖用户数据。"""

        with self.read() as conn:
            conn.executescript(self.schema_path.read_text(encoding="utf-8"))
            # 从 v1.4.0 就地升级时补充修复字段。ALTER TABLE 只在字段确实缺失时执行，
            # 因而重复启动是安全的；真实迁移工具仍会先复制到临时库再运行这里。
            additions = {
                "mice": {"transfer_date":"TEXT", "transfer_destination":"TEXT NOT NULL DEFAULT ''", "transfer_reason":"TEXT NOT NULL DEFAULT ''"},
                "litters": {"mother_id":"INTEGER REFERENCES mice(id) ON DELETE RESTRICT", "unknown_count":"INTEGER NOT NULL DEFAULT 0"},
                "strains": {"name_html":"TEXT NOT NULL DEFAULT ''"},
                "mouse_lines": {"name_html":"TEXT NOT NULL DEFAULT ''"},
                "genotypes": {"name_html":"TEXT NOT NULL DEFAULT ''"},
            }
            for table, columns in additions.items():
                existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                for column, declaration in columns.items():
                    if column not in existing:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
            stamp = now_text()
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', ?)",
                (SCHEMA_VERSION,),
            )
            for name in ("待检测", "WT", "+/+", "+/−", "−/−", "flox/flox", "Cre+", "Cre−"):
                conn.execute(
                    """INSERT OR IGNORE INTO genotypes
                       (name, description, active, created_at, updated_at)
                       VALUES (?, '', 1, ?, ?)""",
                    (name, stamp, stamp),
                )

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """只读查询也必须显式 close；Windows 会锁住仍打开的 SQLite 文件。"""

        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """BEGIN IMMEDIATE 会先取得写锁，使编号生成和保存不可被并发插入打断。"""

        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            # 写入前记录有活鼠的笼；同一事务结束时检查最后一只是否已离笼。
            # 输入为事务前后笼内的Active/Experimental小鼠；输出为自动停用原空笼。
            # 新建但尚未录鼠的笼不在此集合内，避免录入过程中刚建笼就消失。
            occupied_before = {r[0] for r in conn.execute(
                "SELECT DISTINCT cage_id FROM mice WHERE cage_id IS NOT NULL AND status IN ('Active','Experimental')")}
            yield conn
            self.retire_empty_cages(conn, occupied_before)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def retire_empty_cages(self, conn, candidates):
        """停用曾有鼠、现无活鼠的笼并释放位置，保留cages和移笼历史。

        candidates为待检查笼ID；仅在无Active/Experimental小鼠时处理。
        删除的是当前架位，不是小鼠、笼或历史记录；记录审计便于回查。
        架位revision递增，使旧浏览器无法将已释放的位置写回。
        """
        layout_exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='cage_positions'").fetchone()
        for cid in candidates:
            cage = conn.execute("SELECT * FROM cages WHERE id=? AND active=1", (cid,)).fetchone()
            if not cage or conn.execute("SELECT 1 FROM mice WHERE cage_id=? AND status IN ('Active','Experimental')", (cid,)).fetchone():
                continue
            conn.execute("UPDATE cages SET active=0,updated_at=? WHERE id=?", (now_text(),cid))
            if layout_exists:
                position = conn.execute("SELECT rack_id FROM cage_positions WHERE cage_id=?",(cid,)).fetchone()
                conn.execute("DELETE FROM cage_positions WHERE cage_id=?",(cid,))
                if position:
                    conn.execute("UPDATE rack_layouts SET revision=revision+1,updated_at=? WHERE rack_id=?",(now_text(),position[0]))
            self.audit(conn,'auto_retire','cage',cid,'最后一只活鼠离笼，自动清理空笼：'+cage['code'])

    @staticmethod
    def audit(conn: sqlite3.Connection, action: str, entity: str, entity_id: int | None, summary: str) -> None:
        conn.execute(
            "INSERT INTO audit_log(action, entity_type, entity_id, summary, created_at) VALUES (?, ?, ?, ?, ?)",
            (action, entity, entity_id, summary, now_text()),
        )

    # ------------------------------------------------------------------
    # 字典与房间/笼位
    # ------------------------------------------------------------------
    REFERENCE_TABLES = {
        "rooms": "rooms",
        "strains": "strains",
        "lines": "mouse_lines",
        "genotypes": "genotypes",
    }

    def list_references(self, *, include_inactive: bool = True) -> dict[str, list[dict[str, Any]]]:
        suffix = "" if include_inactive else " WHERE active = 1"
        with self.read() as conn:
            result = {}
            for public_name, table in self.REFERENCE_TABLES.items():
                result[public_name] = [dict(r) for r in conn.execute(f"SELECT * FROM {table}{suffix} ORDER BY name COLLATE NOCASE")]
            result["racks"] = [dict(r) for r in conn.execute(
                """SELECT k.*, r.name AS room_name FROM racks k
                   JOIN rooms r ON r.id = k.room_id ORDER BY r.name, k.name"""
            )]
            return result

    def create_reference(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        table = self.REFERENCE_TABLES.get(kind)
        if not table:
            raise ValidationError("不支持的字典类型")
        # 房间仍是普通文本；strain/line/genotype 允许安全的富文本格式。
        if table == "rooms":
            name = str(payload.get("name") or "").strip()
            name_html = ""
        else:
            name, name_html = normalize_rich_name(payload.get("name_html"), payload.get("name"))
        stamp = now_text()
        with self.write() as conn:
            try:
                if table == "rooms":
                    cur = conn.execute(
                        """INSERT INTO rooms(name, building, note, active, created_at, updated_at)
                           VALUES (?, ?, ?, 1, ?, ?)""",
                        (name, str(payload.get("building") or "").strip(), str(payload.get("note") or "").strip(), stamp, stamp),
                    )
                else:
                    cur = conn.execute(
                        f"INSERT INTO {table}(name, name_html, description, active, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
                        (name, name_html, str(payload.get("description") or "").strip(), stamp, stamp),
                    )
            except sqlite3.IntegrityError as exc:
                raise ValidationError(f"名称“{name}”已存在") from exc
            self.audit(conn, "create", kind, cur.lastrowid, f"新建 {kind}: {name}")
            return dict(conn.execute(f"SELECT * FROM {table} WHERE id = ?", (cur.lastrowid,)).fetchone())

    def update_reference(self, kind: str, item_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        table = self.REFERENCE_TABLES.get(kind)
        if not table:
            raise ValidationError("不支持的字典类型")
        with self.write() as conn:
            old = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (item_id,)).fetchone()
            if not old:
                raise ValidationError("字典项目不存在")
            if table == "rooms":
                name = str(payload.get("name", old["name"])).strip()
                name_html = ""
            else:
                old_html = old["name_html"] if "name_html" in old.keys() else ""
                name, name_html = normalize_rich_name(payload.get("name_html", old_html), payload.get("name", old["name"]))
            active = 1 if payload.get("active", old["active"]) in (1, True, "1", "true") else 0
            desc_field = "note" if table == "rooms" else "description"
            desc = str(payload.get(desc_field, old[desc_field]) or "").strip()
            extra_sql, extra_values = "", []
            if table == "rooms":
                extra_sql = ", building = ?"
                extra_values.append(str(payload.get("building", old["building"]) or "").strip())
            try:
                conn.execute(
                    f"UPDATE {table} SET name = ?, {'' if table == 'rooms' else 'name_html = ?, '}{desc_field} = ?, active = ?, updated_at = ?{extra_sql} WHERE id = ?",
                    ((name, desc, active, now_text(), *extra_values, item_id) if table == "rooms" else
                     (name, name_html, desc, active, now_text(), *extra_values, item_id)),
                )
            except sqlite3.IntegrityError as exc:
                raise ValidationError(f"名称“{name}”已存在") from exc
            self.audit(conn, "update", kind, item_id, f"更新 {kind}: {name}")
            return dict(conn.execute(f"SELECT * FROM {table} WHERE id = ?", (item_id,)).fetchone())

    def create_rack(self, payload: dict[str, Any], conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        if conn is None:
            with self.write() as tx:
                return self.create_rack(payload, tx)
        room_id = optional_id(payload.get("room_id"))
        name = str(payload.get("name") or "").strip()
        if not room_id or not name:
            raise ValidationError("room 和 rack 名称不能为空")
        if not conn.execute("SELECT 1 FROM rooms WHERE id = ?", (room_id,)).fetchone():
            raise ValidationError("room 不存在")
        capacity = payload.get("capacity")
        capacity = None if capacity in (None, "") else integer(capacity, "rack 容量", minimum=1)
        stamp = now_text()
        try:
            cur = conn.execute(
                """INSERT INTO racks(room_id, name, capacity, note, active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 1, ?, ?)""",
                (room_id, name, capacity, str(payload.get("note") or "").strip(), stamp, stamp),
            )
        except sqlite3.IntegrityError as exc:
            raise ValidationError("同一 room 中不能有同名 rack") from exc
        self.audit(conn, "create", "rack", cur.lastrowid, f"新建 rack: {name}")
        return dict(conn.execute("SELECT * FROM racks WHERE id = ?", (cur.lastrowid,)).fetchone())

    def create_cage(self, payload: dict[str, Any]) -> dict[str, Any]:
        """建 cage 与随附多只小鼠共用一个事务，任何一行错误都会全部回滚。"""

        with self.write() as conn:
            rack_id = optional_id(payload.get("rack_id"))
            code = str(payload.get("code") or "").strip()
            if not rack_id or not code:
                raise ValidationError("rack 和笼号不能为空")
            if not conn.execute("SELECT 1 FROM racks WHERE id = ?", (rack_id,)).fetchone():
                raise ValidationError("rack 不存在")
            stamp = now_text()
            try:
                cur = conn.execute(
                    """INSERT INTO cages(rack_id, code, capacity, purpose, color, active, note, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                    (rack_id, code, integer(payload.get("capacity", 5), "笼容量", minimum=1),
                     str(payload.get("purpose") or "").strip(), str(payload.get("color") or "").strip(),
                     str(payload.get("note") or "").strip(), stamp, stamp),
                )
            except sqlite3.IntegrityError as exc:
                raise ValidationError(f"笼号“{code}”已存在") from exc
            cage_id = cur.lastrowid
            created_mice = []
            rows = payload.get("mice") or []
            if len(rows) > 100:
                raise ValidationError("一次最多随 cage 新建 100 只小鼠")
            for mouse in rows:
                item = dict(mouse)
                item["cage_id"] = cage_id
                created_mice.append(self._create_mouse(conn, item))
            self.audit(conn, "create", "cage", cage_id, f"新建 cage: {code}，随附小鼠 {len(rows)} 只")
            result = dict(conn.execute("SELECT * FROM cages WHERE id = ?", (cage_id,)).fetchone())
            result["created_mice"] = created_mice
            return result

    def update_cage(self, cage_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        with self.write() as conn:
            old = conn.execute("SELECT * FROM cages WHERE id = ?", (cage_id,)).fetchone()
            if not old:
                raise ValidationError("cage 不存在")
            rack_id = optional_id(payload.get("rack_id", old["rack_id"]))
            code = str(payload.get("code", old["code"])).strip()
            if not code:raise ValidationError("笼号不能为空")
            try:
                conn.execute(
                    """UPDATE cages SET rack_id=?, code=?, capacity=?, purpose=?, color=?, active=?, note=?, updated_at=?
                       WHERE id=?""",
                    (rack_id, code, integer(payload.get("capacity", old["capacity"]), "笼容量", minimum=1),
                     str(payload.get("purpose", old["purpose"]) or "").strip(),
                     str(payload.get("color", old["color"]) or "").strip(),
                     1 if payload.get("active", old["active"]) in (1, True, "1", "true") else 0,
                     str(payload.get("note", old["note"]) or "").strip(), now_text(), cage_id),
                )
            except sqlite3.IntegrityError as exc:
                raise ValidationError("rack 不存在或笼号重复") from exc
            self.audit(conn, "update", "cage", cage_id, f"更新 cage: {code}")
            return self.get_cage(cage_id, conn)

    def delete_cage(self, cage_id: int) -> dict[str, Any]:
        """删除当前空笼卡，释放坐标；cage及科研历史保留。

        输入为笼ID；有Active/Experimental小鼠时拒绝删除，必须先移笼。
        输出为删除回执。不会删除动物、移笼历史或谱系；同一事务更新架位revision，
        并写入删除历史，防止旧客户端撤销操作把已经删除的笼卡放回架子。
        """
        with self.write() as conn:
            old=conn.execute('SELECT * FROM cages WHERE id=?',(cage_id,)).fetchone()
            if not old:raise ValidationError('笼不存在')
            occupied=conn.execute("SELECT COUNT(*) FROM mice WHERE cage_id=? AND status IN ('Active','Experimental')",(cage_id,)).fetchone()[0]
            if occupied:raise ValidationError('该笼还有'+str(occupied)+'只在养小鼠，请先移笼后再删除')
            conn.execute('UPDATE cages SET active=0,updated_at=? WHERE id=?',(now_text(),cage_id))
            exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='cage_positions'").fetchone()
            position=conn.execute('SELECT * FROM cage_positions WHERE cage_id=?',(cage_id,)).fetchone() if exists else None
            if position:
                rid=position['rack_id']
                conn.execute('DELETE FROM cage_positions WHERE cage_id=?',(cage_id,))
                conn.execute('UPDATE rack_layouts SET revision=revision+1,updated_at=? WHERE rack_id=?',(now_text(),rid))
                revision=conn.execute('SELECT revision FROM rack_layouts WHERE rack_id=?',(rid,)).fetchone()[0]
                conn.execute("INSERT INTO rack_layout_history(rack_id,action,before_json,after_json,revision,created_at) VALUES(?,'delete_cage',?,'[]',?,?)",
                             (rid,json.dumps([dict(position)],ensure_ascii=False),revision,now_text()))
            self.audit(conn,'delete_cage','cage',cage_id,'删除空笼卡，保留历史：'+old['code'])
            return {'id':cage_id,'code':old['code'],'deleted':True,'history_preserved':True}

    # ------------------------------------------------------------------
    # 小鼠编号、校验与写入
    # ------------------------------------------------------------------
    def _next_codes(self, conn: sqlite3.Connection, count: int = 1) -> list[str]:
        rows = conn.execute("SELECT code FROM mice ORDER BY id DESC").fetchall()
        prefix, width = "M", 4
        for row in rows:
            match = re.match(r"^(.*?)(\d+)$", row["code"])
            if match:
                prefix, digits = match.groups()
                width = len(digits)
                break
        maximum = 0
        for row in conn.execute("SELECT code FROM mice"):
            match = re.match(r"^(.*?)(\d+)$", row["code"])
            if match and match.group(1).casefold() == prefix.casefold():
                maximum = max(maximum, int(match.group(2)))
        result, candidate = [], maximum + 1
        while len(result) < count:
            code = f"{prefix}{candidate:0{width}d}"
            exists = conn.execute("SELECT 1 FROM mice WHERE code = ? COLLATE NOCASE", (code,)).fetchone()
            if not exists:
                result.append(code)
            candidate += 1
        return result

    def next_code(self) -> str:
        with self.read() as conn:
            return self._next_codes(conn)[0]

    @staticmethod
    def _check_fk(conn: sqlite3.Connection, table: str, value: int | None, label: str) -> None:
        if value is not None and not conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (value,)).fetchone():
            raise ValidationError(f"选择的 {label} 不存在")

    def _resolve_reference_input(
        self,
        conn: sqlite3.Connection,
        table: str,
        plain: Any,
        markup: Any,
        label: str,
    ) -> int | None:
        """把富文本选择器的直接输入解析成字典 ID。

        先按大小写不敏感的纯文本寻找已有词条；找不到时在当前写事务中创建新词条。
        因此“录入小鼠 + 即时新增品系”要么一起成功，要么一起回滚，不会出现小鼠已经
        写入而字典半截失败的状态。旧版客户端没有这两个字段时仍返回 None。
        """

        if not str(plain or "").strip() and not str(markup or "").strip():
            return None
        name, safe_html = normalize_rich_name(markup, plain)
        row = conn.execute(f"SELECT id FROM {table} WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
        if row:
            return row[0]
        stamp = now_text()
        try:
            cur = conn.execute(
                f"INSERT INTO {table}(name, name_html, description, active, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
                (name, safe_html, f"由录入小鼠时直接创建的 {label}", stamp, stamp),
            )
        except sqlite3.IntegrityError as exc:
            # 并发事务已经创建相同词条时，重新读取即可；其他约束错误继续抛出。
            row = conn.execute(f"SELECT id FROM {table} WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
            if row:
                return row[0]
            raise ValidationError(f"无法创建 {label}“{name}”") from exc
        self.audit(conn, "create", label, cur.lastrowid, f"直接录入新{label}：{name}")
        return cur.lastrowid

    def _normalize_mouse(self, conn: sqlite3.Connection, payload: dict[str, Any], old: sqlite3.Row | None = None) -> dict[str, Any]:
        get = lambda key, default=None: payload.get(key, old[key] if old is not None and key in old.keys() else default)
        sex = str(get("sex", "U") or "U").upper()
        status = str(get("status", "Active") or "Active")
        source = str(get("source_type", "Other") or "Other")
        if sex not in ("M", "F", "U"):
            raise ValidationError("性别只能是 M、F 或 U")
        if status not in ALL_STATUSES:
            raise ValidationError("小鼠状态无效")
        if source not in ("Import", "Bred", "Transfer", "Other"):
            raise ValidationError("来源类型无效")
        # 这三个字段来自网页的富文本选择器。若用户选择旧词条，会传 ID；若直接
        # 输入新词条，则传 *_name 与 *_html，由 _resolve_reference_input 自动新建。
        strain_id = optional_id(get("strain_id"))
        line_id = optional_id(get("line_id"))
        genotype_id = optional_id(get("genotype_id"))
        if strain_id is None:
            strain_id = self._resolve_reference_input(conn, "strains", get("strain_name"), get("strain_html"), "strain")
        if line_id is None:
            line_id = self._resolve_reference_input(conn, "mouse_lines", get("line_name"), get("line_html"), "line")
        if genotype_id is None:
            genotype_id = self._resolve_reference_input(conn, "genotypes", get("genotype_name"), get("genotype_html"), "genotype")
        values = {
            "ear_tag": str(get("ear_tag", "") or "").strip(), "sex": sex,
            "dob": parse_date(get("dob"), "出生日期"), "status": status,
            "strain_id": strain_id, "line_id": line_id,
            "genotype_id": genotype_id, "generation": str(get("generation", "") or "").strip(),
            "coat_color": str(get("coat_color", "") or "").strip(), "cage_id": optional_id(get("cage_id")),
            "father_id": optional_id(get("father_id")), "mother_id": optional_id(get("mother_id")),
            "source_type": source, "arrival_date": parse_date(get("arrival_date"), "到达日期"),
            "death_date": parse_date(get("death_date"), "死亡日期"),
            "death_reason": str(get("death_reason", "") or "").strip(),
            "transfer_date": parse_date(get("transfer_date"), "转出日期"),
            "transfer_destination": str(get("transfer_destination", "") or "").strip(),
            "transfer_reason": str(get("transfer_reason", "") or "").strip(),
            "experimental_status": str(get("experimental_status", "") or "").strip(),
            "owner": str(get("owner", "") or "").strip(), "project": str(get("project", "") or "").strip(),
            "comment": str(get("comment", "") or "").strip(),
        }
        # 关系字段在网页上是“输入关键词 + 点击候选项”。文本只用于搜索，
        # 真正保存必须依靠隐藏的外键 ID；如果用户输入了文字却没有选择候选项，
        # 这里明确拒绝保存，避免出现看起来有房间/父母、实际却没有关联的记录。
        for relation_key, text_key, label in (
            ("cage_id", "cage_input", "房间/笼位"),
            ("father_id", "father_input", "父鼠"),
            ("mother_id", "mother_input", "母鼠"),
        ):
            if values[relation_key] is None and str(get(text_key, "") or "").strip():
                raise ValidationError(f"{label}请从下方匹配项中选择，不能只保存手工输入文字")
        for table, key, label in (("strains", "strain_id", "strain"), ("mouse_lines", "line_id", "line"),
                                  ("genotypes", "genotype_id", "genotype"), ("cages", "cage_id", "cage")):
            self._check_fk(conn, table, values[key], label)
        if values["father_id"]:
            father = conn.execute("SELECT sex FROM mice WHERE id = ?", (values["father_id"],)).fetchone()
            if not father or father["sex"] != "M":
                raise ValidationError("父鼠必须是数据库中性别为 M 的小鼠")
        if values["mother_id"]:
            mother = conn.execute("SELECT sex FROM mice WHERE id = ?", (values["mother_id"],)).fetchone()
            if not mother or mother["sex"] != "F":
                raise ValidationError("母鼠必须是数据库中性别为 F 的小鼠")
        if status == "Dead":
            values["death_date"] = values["death_date"] or today_text()
            values["cage_id"] = None
        elif status == "Transferred":
            values["transfer_date"] = values["transfer_date"] or today_text()
            values["cage_id"] = None
        return values

    def _warnings(self, conn: sqlite3.Connection, values: dict[str, Any], exclude_id: int | None = None) -> list[str]:
        warnings: list[str] = []
        if values["ear_tag"]:
            sql = "SELECT COUNT(*) FROM mice WHERE ear_tag = ? COLLATE NOCASE"
            params: list[Any] = [values["ear_tag"]]
            if exclude_id:
                sql += " AND id <> ?"
                params.append(exclude_id)
            if conn.execute(sql, params).fetchone()[0]:
                warnings.append("该耳标已被其他小鼠使用，请核对；本系统允许记录真实重复耳标。")
        cage_id = values["cage_id"]
        if cage_id and values["status"] in ALIVE_STATUSES:
            cage = conn.execute("SELECT capacity FROM cages WHERE id = ?", (cage_id,)).fetchone()
            sql = "SELECT sex FROM mice WHERE cage_id = ? AND status IN ('Active','Experimental')"
            params = [cage_id]
            if exclude_id:
                sql += " AND id <> ?"
                params.append(exclude_id)
            occupants = [r["sex"] for r in conn.execute(sql, params)]
            if len(occupants) + 1 > cage["capacity"]:
                warnings.append("保存后该 cage 将超过标称容量；记录已保留，请尽快核对实际笼位。")
            known = {s for s in occupants + [values["sex"]] if s in ("M", "F")}
            if known == {"M", "F"}:
                warnings.append("该 cage 存在雌雄混笼；记录已保留，请确认是否为繁育安排。")
        return warnings

    def _create_mouse(self, conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
        values = self._normalize_mouse(conn, payload)
        code = self._next_codes(conn)[0]  # 明确忽略 payload 中任何 code。
        warnings = self._warnings(conn, values)
        stamp = now_text()
        columns = list(values)
        cur = conn.execute(
            f"INSERT INTO mice(code, {', '.join(columns)}, created_at, updated_at) VALUES (?, {', '.join('?' for _ in columns)}, ?, ?)",
            (code, *(values[k] for k in columns), stamp, stamp),
        )
        mouse_id = cur.lastrowid
        if values["cage_id"]:
            conn.execute(
                "INSERT INTO mouse_movements(mouse_id, from_cage_id, to_cage_id, moved_at, note, created_at) VALUES (?, NULL, ?, ?, ?, ?)",
                (mouse_id, values["cage_id"], values["arrival_date"] or today_text(), "初始入笼", stamp),
            )
        self.audit(conn, "create", "mouse", mouse_id, f"新建小鼠 {code}")
        result = self.get_mouse(mouse_id, conn)
        result["warnings"] = warnings
        return result

    def create_mouse(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.write() as conn:
            return self._create_mouse(conn, payload)

    def update_mouse(self, mouse_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        with self.write() as conn:
            old = conn.execute("SELECT * FROM mice WHERE id = ?", (mouse_id,)).fetchone()
            if not old:
                raise ValidationError("小鼠不存在")
            values = self._normalize_mouse(conn, payload, old)
            if values["father_id"] == mouse_id or values["mother_id"] == mouse_id:
                raise ValidationError("小鼠不能成为自己的父母")
            warnings = self._warnings(conn, values, mouse_id)
            old_cage, new_cage = old["cage_id"], values["cage_id"]
            columns = list(values)
            # UPDATE 列表中没有 code，因此客户端无法修改系统编号。
            conn.execute(
                f"UPDATE mice SET {', '.join(f'{k} = ?' for k in columns)}, updated_at = ? WHERE id = ?",
                (*(values[k] for k in columns), now_text(), mouse_id),
            )
            if old_cage != new_cage:
                if values["status"] == "Dead":
                    moved_at, note = values["death_date"], "记录死亡，自动移出笼位"
                elif values["status"] == "Transferred":
                    moved_at, note = values["transfer_date"], "记录转出，自动移出笼位"
                else:
                    moved_at, note = today_text(), str(payload.get("movement_note") or "调整笼位").strip()
                conn.execute(
                    "INSERT INTO mouse_movements(mouse_id, from_cage_id, to_cage_id, moved_at, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (mouse_id, old_cage, new_cage, moved_at, note, now_text()),
                )
            self.audit(conn, "update", "mouse", mouse_id, f"更新小鼠 {old['code']}，状态 {values['status']}")
            result = self.get_mouse(mouse_id, conn)
            result["warnings"] = warnings
            return result

    # ------------------------------------------------------------------
    # 查询与汇总
    # ------------------------------------------------------------------
    MOUSE_SELECT = """SELECT m.*, s.name AS strain_name, s.name_html AS strain_name_html,
        l.name AS line_name, l.name_html AS line_name_html, g.name AS genotype_name, g.name_html AS genotype_name_html,
        c.code AS cage_code, k.name AS rack_name, r.name AS room_name,
        f.code AS father_code, mo.code AS mother_code
        FROM mice m
        LEFT JOIN strains s ON s.id=m.strain_id LEFT JOIN mouse_lines l ON l.id=m.line_id
        LEFT JOIN genotypes g ON g.id=m.genotype_id LEFT JOIN cages c ON c.id=m.cage_id
        LEFT JOIN racks k ON k.id=c.rack_id LEFT JOIN rooms r ON r.id=k.room_id
        LEFT JOIN mice f ON f.id=m.father_id LEFT JOIN mice mo ON mo.id=m.mother_id"""

    @staticmethod
    def _add_ages(item: dict[str, Any]) -> dict[str, Any]:
        """统一计算周龄；输入生日/候选生日和状态，输出age_weeks及age_label。

        专业假设仅为1周=7天，显示已满整数周数（向下取整）；候选生日显示周龄范围，
        不推定单只鼠的准确生日。死亡鼠截止到死亡日期，活鼠截止到今天。
        内部age_days保留供原数据接口兼容，界面统一使用周龄。
        """
        end_text=item.get('death_date') if item.get('status')=='Dead' else today_text()
        ages=[]
        try:
            end=date.fromisoformat(end_text)
            if item.get('dob'):
                ages=[(end-date.fromisoformat(item['dob'])).days]
            else:
                part=re.search(r'(?:原始生日|原始出生日期|候选生日)[：:]\s*([^；\n]+)',item.get('comment') or '')
                for birthday in re.findall(r'\d{4}-\d{2}-\d{2}',part.group(1) if part else ''):
                    try:ages.append((end-date.fromisoformat(birthday)).days)
                    except ValueError:pass
        except (TypeError,ValueError):pass
        ages=[days for days in ages if days>=0]
        item['age_days']=ages[0] if item.get('dob') and ages else None
        item['age_weeks']=item['age_days']//7 if item['age_days'] is not None else None
        item['age_is_range']=len(set(ages))>1
        item['age_label']=(f"{min(ages)//7}–{max(ages)//7}周" if ages and min(ages)//7!=max(ages)//7
                           else f"{ages[0]//7}周" if ages else '待确认')
        return item

    def list_mice(self, scope: str = "alive", search: str = "", strain_id: int | None = None,
                  line_id: int | None = None, status: str = "") -> list[dict[str, Any]]:
        where, params = [], []
        if scope == "dead":
            where.append("m.status = 'Dead'")
        elif scope == "all":
            pass
        else:
            where.append("m.status <> 'Dead'")
        if status:
            where.append("m.status = ?"); params.append(status)
        if strain_id:
            where.append("m.strain_id = ?"); params.append(strain_id)
        if line_id:
            where.append("m.line_id = ?"); params.append(line_id)
        if search.strip():
            q = f"%{search.strip()}%"
            where.append("(m.code LIKE ? OR m.ear_tag LIKE ? OR s.name LIKE ? OR l.name LIKE ? OR g.name LIKE ? OR m.owner LIKE ? OR m.project LIKE ? OR m.comment LIKE ?)")
            params.extend([q] * 8)
        sql = self.MOUSE_SELECT + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY m.id DESC"
        with self.read() as conn:
            return [self._add_ages(dict(r)) for r in conn.execute(sql, params)]

    def get_mouse(self, mouse_id: int, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        owns = conn is None
        conn = conn or self.connect()
        try:
            row = conn.execute(self.MOUSE_SELECT + " WHERE m.id = ?", (mouse_id,)).fetchone()
            if not row:
                raise ValidationError("小鼠不存在")
            item = self._add_ages(dict(row))
            item["movements"] = [dict(r) for r in conn.execute(
                """SELECT mm.*, fc.code AS from_cage_code, tc.code AS to_cage_code FROM mouse_movements mm
                   LEFT JOIN cages fc ON fc.id=mm.from_cage_id LEFT JOIN cages tc ON tc.id=mm.to_cage_id
                   WHERE mm.mouse_id=? ORDER BY mm.moved_at DESC, mm.id DESC""", (mouse_id,))]
            item["events"] = [dict(r) for r in conn.execute(
                "SELECT * FROM mouse_events WHERE mouse_id=? ORDER BY event_date DESC, id DESC", (mouse_id,))]
            return item
        finally:
            if owns:
                conn.close()

    def add_event(self, mouse_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        with self.write() as conn:
            if not conn.execute("SELECT 1 FROM mice WHERE id=?", (mouse_id,)).fetchone():
                raise ValidationError("小鼠不存在")
            event_type = str(payload.get("event_type") or "备注").strip()
            title = str(payload.get("title") or "").strip()
            if not title:
                raise ValidationError("事件标题不能为空")
            cur = conn.execute(
                "INSERT INTO mouse_events(mouse_id,event_type,event_date,title,details,created_at) VALUES(?,?,?,?,?,?)",
                (mouse_id, event_type, parse_date(payload.get("event_date") or today_text(), "事件日期", required=True),
                 title, str(payload.get("details") or "").strip(), now_text()),
            )
            self.audit(conn, "event", "mouse", mouse_id, f"记录事件：{title}")
            return dict(conn.execute("SELECT * FROM mouse_events WHERE id=?", (cur.lastrowid,)).fetchone())

    def list_cages(self) -> list[dict[str, Any]]:
        with self.read() as conn:
            rows = conn.execute(
                """SELECT c.*, k.name AS rack_name, r.name AS room_name,
                   COUNT(m.id) AS occupied
                   FROM cages c JOIN racks k ON k.id=c.rack_id JOIN rooms r ON r.id=k.room_id
                   LEFT JOIN mice m ON m.cage_id=c.id AND m.status IN ('Active','Experimental')
                   GROUP BY c.id ORDER BY r.name,k.name,c.code"""
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                mice = conn.execute(
                    """SELECT m.id,m.code,m.ear_tag,m.sex,m.status,l.name AS line_name,l.name_html AS line_name_html,
                       g.name AS genotype_name,g.name_html AS genotype_name_html,m.dob,m.comment,m.death_date
                       FROM mice m LEFT JOIN mouse_lines l ON l.id=m.line_id LEFT JOIN genotypes g ON g.id=m.genotype_id
                       WHERE m.cage_id=? AND m.status IN ('Active','Experimental') ORDER BY m.ear_tag,m.code""", (row["id"],)).fetchall()
                item["mice"] = [self._add_ages(dict(m)) for m in mice]
                item["ear_tags"] = [m["ear_tag"] for m in mice if m["ear_tag"]]
                item["missing_ear_tags"] = sum(1 for m in mice if not m["ear_tag"])
                item["lines"] = sorted({m["line_name"] or "未设置" for m in mice})
                item["line_items"] = [{"name": name, "name_html": next((m["line_name_html"] for m in mice if m["line_name"] == name), "")}
                                      for name in item["lines"]]
                result.append(item)
            return result

    def get_cage(self, cage_id: int, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        for cage in self.list_cages() if conn is None else self._list_cages_conn(conn):
            if cage["id"] == cage_id:
                return cage
        raise ValidationError("cage 不存在")

    def _list_cages_conn(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        # 事务内不能另开连接，否则未提交的新 cage 不可见；使用同样的查询生成简版详情。
        rows = conn.execute("""SELECT c.*,k.name rack_name,r.name room_name FROM cages c
            JOIN racks k ON k.id=c.rack_id JOIN rooms r ON r.id=k.room_id ORDER BY c.code""").fetchall()
        result=[]
        for row in rows:
            item=dict(row); mice=[dict(x) for x in conn.execute("""SELECT m.id,m.code,m.ear_tag,m.sex,m.status,
                l.name line_name,l.name_html line_name_html,g.name genotype_name,g.name_html genotype_name_html,m.dob,m.comment,m.death_date FROM mice m LEFT JOIN mouse_lines l ON l.id=m.line_id
                LEFT JOIN genotypes g ON g.id=m.genotype_id WHERE m.cage_id=? AND m.status IN ('Active','Experimental')""",(row["id"],))]
            item.update(occupied=len(mice),mice=[self._add_ages(x) for x in mice],ear_tags=[x["ear_tag"] for x in mice if x["ear_tag"]],
                        missing_ear_tags=sum(not x["ear_tag"] for x in mice),lines=sorted({x["line_name"] or "未设置" for x in mice}))
            item["line_items"]=[{"name":name,"name_html":next((x["line_name_html"] for x in mice if x["line_name"]==name),"")} for name in item["lines"]]
            result.append(item)
        return result

    def dashboard(self) -> dict[str, Any]:
        with self.read() as conn:
            counts = {
                "current_mice": conn.execute("SELECT COUNT(*) FROM mice WHERE cage_id IS NOT NULL AND status IN ('Active','Experimental')").fetchone()[0],
                "occupied_cages": conn.execute("SELECT COUNT(DISTINCT cage_id) FROM mice WHERE cage_id IS NOT NULL AND status IN ('Active','Experimental')").fetchone()[0],
                "active_matings": conn.execute("SELECT COUNT(*) FROM matings WHERE status='Active'").fetchone()[0],
                "pending_weaning": conn.execute("SELECT COUNT(*) FROM litters WHERE status='Born'").fetchone()[0],
            }
            counts["recent_audit"] = [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 10")]
            counts["litters"] = [dict(r) for r in conn.execute("SELECT * FROM litters WHERE status='Born' ORDER BY birth_date")]
            return counts

    def audit_rows(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.read() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 500),))]

    # ------------------------------------------------------------------
    # 繁育、产仔与离乳
    # ------------------------------------------------------------------
    def create_mating(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.write() as conn:
            code = str(payload.get("code") or "").strip(); male_id = optional_id(payload.get("male_id"))
            female_ids = [optional_id(x) for x in (payload.get("female_ids") or [])]
            female_ids = [x for x in female_ids if x]
            if not code or not male_id or not female_ids:
                raise ValidationError("交配编号、1 只雄鼠和至少 1 只雌鼠均为必填")
            male = conn.execute("SELECT sex FROM mice WHERE id=?", (male_id,)).fetchone()
            if not male or male["sex"] != "M": raise ValidationError("繁育雄鼠性别必须为 M")
            for fid in female_ids:
                female = conn.execute("SELECT sex FROM mice WHERE id=?", (fid,)).fetchone()
                if not female or female["sex"] != "F": raise ValidationError("繁育雌鼠性别必须为 F")
            offspring_strain_id = optional_id(payload.get("offspring_strain_id"))
            offspring_line_id = optional_id(payload.get("offspring_line_id"))
            if offspring_strain_id is None:
                offspring_strain_id = self._resolve_reference_input(conn, "strains", payload.get("offspring_strain_name"), payload.get("offspring_strain_html"), "strain")
            if offspring_line_id is None:
                offspring_line_id = self._resolve_reference_input(conn, "mouse_lines", payload.get("offspring_line_name"), payload.get("offspring_line_html"), "line")
            self._check_fk(conn, "strains", offspring_strain_id, "后代 strain")
            self._check_fk(conn, "mouse_lines", offspring_line_id, "后代 line")
            stamp=now_text()
            try:
                cur=conn.execute("""INSERT INTO matings(code,male_id,cage_id,start_date,end_date,status,offspring_strain_id,
                    offspring_line_id,purpose,project,note,created_at,updated_at) VALUES(?,?,?,?,NULL,'Active',?,?,?,?,?,?,?)""",
                    (code,male_id,optional_id(payload.get("cage_id")),parse_date(payload.get("start_date"),"开始日期",required=True),
                     offspring_strain_id,offspring_line_id,
                     str(payload.get("purpose") or ""),str(payload.get("project") or ""),str(payload.get("note") or ""),stamp,stamp))
            except sqlite3.IntegrityError as exc: raise ValidationError("交配编号重复或关联数据无效") from exc
            conn.executemany("INSERT INTO mating_females(mating_id,female_id) VALUES(?,?)",[(cur.lastrowid,x) for x in female_ids])
            self.audit(conn,"create","mating",cur.lastrowid,f"建立繁育 {code}")
            return self._get_mating(conn,cur.lastrowid)

    def _get_mating(self, conn: sqlite3.Connection, mating_id: int) -> dict[str, Any]:
        row=conn.execute("SELECT * FROM matings WHERE id=?",(mating_id,)).fetchone()
        if not row: raise ValidationError("繁育记录不存在")
        item=dict(row); item["female_ids"]=[r[0] for r in conn.execute("SELECT female_id FROM mating_females WHERE mating_id=?",(mating_id,))]
        item["litters"]=[dict(r) for r in conn.execute("SELECT * FROM litters WHERE mating_id=? ORDER BY birth_date DESC",(mating_id,))]
        return item

    def list_matings(self) -> list[dict[str, Any]]:
        with self.read() as conn:
            return [self._get_mating(conn,r[0]) for r in conn.execute("SELECT id FROM matings ORDER BY id DESC")]

    def create_litter(self, mating_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        with self.write() as conn:
            mating=self._get_mating(conn,mating_id); mother_id=optional_id(payload.get("mother_id"))
            if mother_id not in mating["female_ids"]: raise ValidationError("该母鼠不属于这条繁育记录")
            counts={k:integer(payload.get(k,0),k,minimum=0) for k in ("male_count","female_count","unknown_count","dead_count")}
            total=sum(counts.values()); code=str(payload.get("code") or "").strip()
            if not code: raise ValidationError("窝号不能为空")
            stamp=now_text()
            try:
                cur=conn.execute("""INSERT INTO litters(mating_id,mother_id,code,birth_date,total_born,male_count,female_count,
                    unknown_count,dead_count,status,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,'Born',?,?,?)""",
                    (mating_id,mother_id,code,parse_date(payload.get("birth_date"),"出生日期",required=True),total,
                     counts["male_count"],counts["female_count"],counts["unknown_count"],counts["dead_count"],
                     str(payload.get("note") or ""),stamp,stamp))
            except sqlite3.IntegrityError as exc: raise ValidationError("窝号重复或仔鼠数量关系无效") from exc
            self.audit(conn,"create","litter",cur.lastrowid,f"报告产仔 {code}，共 {total} 只")
            return dict(conn.execute("SELECT * FROM litters WHERE id=?",(cur.lastrowid,)).fetchone())

    def wean_litter(self, litter_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        with self.write() as conn:
            litter=conn.execute("SELECT * FROM litters WHERE id=?",(litter_id,)).fetchone()
            if not litter: raise ValidationError("窝次不存在")
            if litter["status"] != "Born": raise ValidationError("只有 Born 状态的窝次可以离乳")
            mating=self._get_mating(conn,litter["mating_id"])
            genotype_id=optional_id(payload.get("genotype_id"))
            if genotype_id is None:
                genotype_id = self._resolve_reference_input(conn, "genotypes", payload.get("genotype_name"), payload.get("genotype_html"), "genotype")
            self._check_fk(conn,"genotypes",genotype_id,"genotype")
            created=[]
            specs=(("M",litter["male_count"],optional_id(payload.get("male_cage_id"))),
                   ("F",litter["female_count"],optional_id(payload.get("female_cage_id"))),
                   ("U",litter["unknown_count"],optional_id(payload.get("unknown_cage_id"))))
            for sex,count,cage_id in specs:
                for _ in range(count):
                    created.append(self._create_mouse(conn,{"sex":sex,"dob":litter["birth_date"],"status":"Active",
                        "strain_id":mating["offspring_strain_id"],"line_id":mating["offspring_line_id"],"genotype_id":genotype_id,
                        "cage_id":cage_id,"father_id":mating["male_id"],"mother_id":litter["mother_id"],
                        "source_type":"Bred","project":mating["project"],"comment":f"由窝次 {litter['code']} 离乳生成"}))
            weaned=parse_date(payload.get("weaned_date") or today_text(),"离乳日期",required=True)
            conn.execute("UPDATE litters SET status='Weaned',weaned_date=?,updated_at=? WHERE id=?",(weaned,now_text(),litter_id))
            self.audit(conn,"wean","litter",litter_id,f"窝次 {litter['code']} 离乳，生成 {len(created)} 只小鼠")
            return {"litter_id":litter_id,"weaned_date":weaned,"created_mice":created}

    # ------------------------------------------------------------------
    # CSV 预检与原子导入
    # ------------------------------------------------------------------
    CSV_ALIASES = {
        "耳标":"ear_tag", "ear_tag":"ear_tag", "性别":"sex", "sex":"sex",
        "出生日期":"dob", "dob":"dob", "房间":"room", "room":"room",
        "架位":"rack", "rack":"rack", "笼号":"cage", "cage":"cage",
        "strain":"strain", "品系":"strain", "line":"line", "鼠系":"line",
        "genotype":"genotype", "基因型":"genotype", "来源":"source_type", "source":"source_type",
        "负责人":"owner", "owner":"owner", "项目":"project", "project":"project",
        "备注":"comment", "comment":"comment", "小鼠编号":"ignored_code", "code":"ignored_code",
    }

    def _csv_rows(self, text: str) -> tuple[list[dict[str, str]], bool]:
        """解析 CSV，但不修改数据库；返回标准字段和是否出现旧编号列。"""

        clean=str(text or "").lstrip("\ufeff")
        if not clean.strip(): raise ValidationError("CSV 内容为空")
        reader=csv.DictReader(io.StringIO(clean))
        if not reader.fieldnames: raise ValidationError("CSV 缺少表头")
        mapped=[]; ignored=False
        for header in reader.fieldnames:
            key=self.CSV_ALIASES.get(str(header or "").strip().casefold())
            if key == "ignored_code": ignored=True
            mapped.append(key)
        rows=[]
        for number,raw in enumerate(reader,start=2):
            if not any(str(v or "").strip() for v in raw.values()): continue
            row={}
            for header,key in zip(reader.fieldnames,mapped):
                if key and key != "ignored_code": row[key]=str(raw.get(header) or "").strip()
            row["_row_number"]=number; rows.append(row)
        if not rows: raise ValidationError("CSV 没有可导入的数据行")
        return rows,ignored

    @staticmethod
    def _named_id(conn: sqlite3.Connection, table: str, name: str) -> int | None:
        if not name: return None
        row=conn.execute(f"SELECT id FROM {table} WHERE name=? COLLATE NOCASE",(name,)).fetchone()
        return row[0] if row else None

    def _ensure_named(self, conn: sqlite3.Connection, table: str, name: str) -> int | None:
        found=self._named_id(conn,table,name)
        if found or not name: return found
        stamp=now_text(); description="由 CSV 导入自动创建"
        cur=conn.execute(f"INSERT INTO {table}(name,description,active,created_at,updated_at) VALUES(?,?,1,?,?)",
                         (name,description,stamp,stamp))
        return cur.lastrowid

    def preview_import(self, payload: dict[str, Any]) -> dict[str, Any]:
        rows,ignored=self._csv_rows(payload.get("csv_text") or "")
        auto=bool(payload.get("auto_create",False)); results=[]
        with self.read() as conn:
            predicted=self._next_codes(conn,len(rows))
            for i,row in enumerate(rows):
                errors=[]; warnings=[]
                try:
                    sex=str(row.get("sex") or "U").upper()
                    if sex not in ("M","F","U"): raise ValidationError("性别只能是 M、F 或 U")
                    parse_date(row.get("dob"),"出生日期")
                    for table,key,label in (("strains","strain","strain"),("mouse_lines","line","line"),("genotypes","genotype","genotype")):
                        if row.get(key) and not self._named_id(conn,table,row[key]):
                            (warnings if auto else errors).append(f"{label}“{row[key]}”不存在"+("，确认后将自动创建" if auto else ""))
                    room,rack,cage=row.get("room",""),row.get("rack",""),row.get("cage","")
                    if any((room,rack,cage)) and not all((room,rack,cage)):
                        errors.append("填写位置时 room、rack、cage 必须同时填写")
                    elif cage:
                        found=conn.execute("""SELECT c.id FROM cages c JOIN racks k ON k.id=c.rack_id JOIN rooms r ON r.id=k.room_id
                            WHERE r.name=? COLLATE NOCASE AND k.name=? COLLATE NOCASE AND c.code=? COLLATE NOCASE""",(room,rack,cage)).fetchone()
                        if not found: (warnings if auto else errors).append("指定的 room/rack/cage 不存在"+("，确认后将自动创建" if auto else ""))
                except ValidationError as exc: errors.append(str(exc))
                if ignored: warnings.append("CSV 中的小鼠编号列已忽略，正式导入由系统重新编号")
                results.append({"row":row["_row_number"],"predicted_code":predicted[i],"errors":errors,"warnings":warnings,"data":row})
        return {"valid":not any(x["errors"] for x in results),"rows":results,"ignored_code_column":ignored,
                "note":"预计编号只用于预检；正式提交会根据数据库最新状态重新分配。"}

    def commit_import(self, payload: dict[str, Any]) -> dict[str, Any]:
        """重新解析并重新编号，不信任浏览器发回的预检结果。"""

        preview=self.preview_import(payload)
        if not preview["valid"]: raise ValidationError("CSV 仍有错误行，整批未导入")
        rows,_=self._csv_rows(payload.get("csv_text") or ""); auto=bool(payload.get("auto_create",False))
        created=[]
        with self.write() as conn:
            for row in rows:
                ids={}
                for table,key,out in (("strains","strain","strain_id"),("mouse_lines","line","line_id"),("genotypes","genotype","genotype_id")):
                    ids[out]=self._ensure_named(conn,table,row.get(key,"")) if auto else self._named_id(conn,table,row.get(key,""))
                cage_id=None; room=row.get("room",""); rack=row.get("rack",""); cage=row.get("cage","")
                if cage:
                    found=conn.execute("""SELECT c.id FROM cages c JOIN racks k ON k.id=c.rack_id JOIN rooms r ON r.id=k.room_id
                        WHERE r.name=? COLLATE NOCASE AND k.name=? COLLATE NOCASE AND c.code=? COLLATE NOCASE""",(room,rack,cage)).fetchone()
                    if found: cage_id=found[0]
                    elif auto:
                        room_id=self._named_id(conn,"rooms",room)
                        if not room_id:
                            stamp=now_text(); room_id=conn.execute("INSERT INTO rooms(name,building,note,active,created_at,updated_at) VALUES(?,'','CSV 自动创建',1,?,?)",(room,stamp,stamp)).lastrowid
                        rack_row=conn.execute("SELECT id FROM racks WHERE room_id=? AND name=? COLLATE NOCASE",(room_id,rack)).fetchone()
                        rack_id=rack_row[0] if rack_row else self.create_rack({"room_id":room_id,"name":rack,"note":"CSV 自动创建"},conn)["id"]
                        stamp=now_text(); cage_id=conn.execute("""INSERT INTO cages(rack_id,code,capacity,purpose,color,active,note,created_at,updated_at)
                            VALUES(?,?,5,'','','1','CSV 自动创建',?,?)""",(rack_id,cage,stamp,stamp)).lastrowid
                source=row.get("source_type") or "Import"
                source_map={"导入":"Import","繁育":"Bred","转入":"Transfer","其他":"Other"}; source=source_map.get(source,source)
                created.append(self._create_mouse(conn,{**ids,"ear_tag":row.get("ear_tag",""),"sex":row.get("sex") or "U",
                    "dob":row.get("dob"),"cage_id":cage_id,"source_type":source,"owner":row.get("owner",""),
                    "project":row.get("project",""),"comment":row.get("comment","")}))
            self.audit(conn,"import","mouse",None,f"CSV 原子导入 {len(created)} 只小鼠")
        return {"count":len(created),"created_mice":created}

    # ------------------------------------------------------------------
    # 导出与一致性备份
    # ------------------------------------------------------------------
    def export_csv(self, scope: str) -> bytes:
        mice=self.list_mice(scope=scope)
        fields=("code","ear_tag","sex","dob","age_weeks","age_label","status","strain_name","line_name","genotype_name",
                "room_name","rack_name","cage_code","owner","project","death_date","death_reason",
                "transfer_date","transfer_destination","comment")
        output=io.StringIO(newline=""); writer=csv.DictWriter(output,fieldnames=fields,extrasaction="ignore")
        writer.writeheader(); writer.writerows(mice)
        return ("\ufeff"+output.getvalue()).encode("utf-8")

    def backup_to(self, destination: str | Path) -> Path:
        dest=Path(destination); dest.parent.mkdir(parents=True,exist_ok=True)
        target = sqlite3.connect(dest)
        try:
            with self.read() as source:
                source.backup(target)
            target.close()
            check = sqlite3.connect(dest)
            try:
                if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    dest.unlink(missing_ok=True); raise RuntimeError("备份完整性检查失败")
            finally:
                check.close()
        finally:
            # close 可重复调用，确保异常路径也不会留下 Windows 文件锁。
            target.close()
        return dest

    def integrity_check(self) -> str:
        with self.read() as conn: return conn.execute("PRAGMA integrity_check").fetchone()[0]
