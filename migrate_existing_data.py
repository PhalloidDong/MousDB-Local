"""旧版 MousDB 数据库的一键安全迁移工具。

流程是：只读识别源库 -> 转换到临时库 -> 完整性检查 -> 备份现有目标 -> 原子替换。
任何一步失败都不会替换正式目标。旧记录属于恢复数据，所以保留其已有 code；迁移后新鼠
仍由 db.py 自动编号。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from db import Database, now_text


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_TARGET = BASE_DIR / "data" / "mousdb.sqlite3"


def table_names(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def locate_source(selected: Path) -> Path:
    """文件直接使用；文件夹优先寻找 Python 版，再寻找 Wrangler 版。"""

    selected = selected.resolve()
    if selected.is_file(): return selected
    if not selected.is_dir(): raise ValueError("选择的旧程序或数据库不存在")
    preferred = selected / "data" / "mousdb.sqlite3"
    if preferred.is_file(): return preferred
    candidates = [p for p in (selected / ".wrangler" / "state").rglob("*.sqlite*") if p.is_file()] if (selected/".wrangler"/"state").exists() else []
    if not candidates: candidates = [p for p in selected.rglob("*.sqlite*") if p.is_file()]
    if not candidates: raise ValueError("所选文件夹中未找到 SQLite 数据库")
    return max(candidates, key=lambda p:p.stat().st_mtime)


def validate_sqlite(path: Path) -> tuple[set[str], str]:
    if not path.is_file() or path.stat().st_size < 100: raise ValueError("源文件不是有效的 SQLite 数据库")
    with path.open("rb") as handle:
        if handle.read(16) != b"SQLite format 3\x00": raise ValueError("源文件缺少 SQLite 文件头")
    uri = path.resolve().as_uri() + "?mode=ro"
    conn=sqlite3.connect(uri,uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        tables = table_names(conn)
    finally: conn.close()
    if integrity != "ok": raise ValueError(f"源数据库完整性检查失败：{integrity}")
    if "mice" not in tables: raise ValueError("源数据库没有 mice 表，不属于支持的格式")
    kind = "python" if {"schema_meta","mouse_movements","mouse_lines"} & tables else "node"
    return tables, kind


def counts(path: Path) -> dict[str, int]:
    names=("mice","cages","strains","mouse_lines","genotypes","mouse_movements","matings","litters","mouse_events")
    conn=sqlite3.connect(path)
    try:
        tables=table_names(conn)
        return {name:(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] if name in tables else 0) for name in names}
    finally: conn.close()


def copy_python_source(source: Path, temporary: Path) -> None:
    """SQLite backup API 能正确合并 WAL 中已提交的数据，比复制单个文件安全。"""

    src=sqlite3.connect(f"file:{source.resolve().as_posix()}?mode=ro",uri=True); dst=sqlite3.connect(temporary)
    try: src.backup(dst)
    finally: src.close(); dst.close()
    Database(temporary).initialize()


def value(row: sqlite3.Row, *names: str, default: Any = "") -> Any:
    keys=set(row.keys())
    for name in names:
        if name in keys and row[name] is not None: return row[name]
    return default


def convert_node_source(source: Path, temporary: Path) -> None:
    """保守转换早期 Node/Wrangler 常见字段；源库没有的信息保持空，不凭空编造。"""

    target=Database(temporary); target.initialize()
    src=sqlite3.connect(source)
    try:
      src.row_factory=sqlite3.Row; tables=table_names(src); stamp=now_text()
      with target.write() as dst:
        # 先建立字典。旧库可能直接在 mice 中保存文本，使用 DISTINCT 合并大小写重复。
        mouse_rows=list(src.execute("SELECT * FROM mice"))
        dictionary={"strains":("strain","strain_name"),"mouse_lines":("line","line_name"),"genotypes":("genotype","genotype_name")}
        maps={}
        for table,names in dictionary.items():
            names_found=[]
            for row in mouse_rows:
                text=str(value(row,*names,default="") or "").strip()
                if text and text.casefold() not in {x.casefold() for x in names_found}: names_found.append(text)
            maps[table]={}
            for name in names_found:
                old=dst.execute(f"SELECT id FROM {table} WHERE name=? COLLATE NOCASE",(name,)).fetchone()
                if old: maps[table][name.casefold()]=old[0]
                else:
                    cur=dst.execute(f"INSERT INTO {table}(name,description,active,created_at,updated_at) VALUES(?,'旧版迁移',1,?,?)",(name,stamp,stamp))
                    maps[table][name.casefold()]=cur.lastrowid
        # 恢复旧 cage。若早期表没有 room/rack 字段，为了保住 cage 关联，只创建名称明确
        # 标注为“旧版未记录”的容器；这不是推断真实位置，用户迁移后可在界面修正。
        cage_map={}
        if "cages" in tables:
            room_cache={}; rack_cache={}
            for cage_row in src.execute("SELECT * FROM cages"):
                old_cage_id=value(cage_row,"id",default=None)
                cage_code=str(value(cage_row,"code","name","cage_number",default=f"LEGACY-CAGE-{old_cage_id}") or f"LEGACY-CAGE-{old_cage_id}")
                room_name=str(value(cage_row,"room","room_name",default="旧版未记录房间") or "旧版未记录房间")
                rack_name=str(value(cage_row,"rack","rack_name",default="旧版未记录架位") or "旧版未记录架位")
                if room_name.casefold() not in room_cache:
                    old=dst.execute("SELECT id FROM rooms WHERE name=? COLLATE NOCASE",(room_name,)).fetchone()
                    room_cache[room_name.casefold()]=old[0] if old else dst.execute("INSERT INTO rooms(name,building,note,active,created_at,updated_at) VALUES(?,'','旧版迁移',1,?,?)",(room_name,stamp,stamp)).lastrowid
                rack_key=(room_cache[room_name.casefold()],rack_name.casefold())
                if rack_key not in rack_cache:
                    old=dst.execute("SELECT id FROM racks WHERE room_id=? AND name=? COLLATE NOCASE",(rack_key[0],rack_name)).fetchone()
                    rack_cache[rack_key]=old[0] if old else dst.execute("INSERT INTO racks(room_id,name,capacity,note,active,created_at,updated_at) VALUES(?,?,NULL,'旧版迁移',1,?,?)",(rack_key[0],rack_name,stamp,stamp)).lastrowid
                capacity=value(cage_row,"capacity",default=5)
                try: capacity=max(1,int(capacity))
                except (TypeError,ValueError): capacity=5
                cur=dst.execute("INSERT OR IGNORE INTO cages(rack_id,code,capacity,purpose,color,active,note,created_at,updated_at) VALUES(?,?,?,?,'',1,?,?,?)",
                    (rack_cache[rack_key],cage_code,capacity,str(value(cage_row,"purpose",default="") or ""),"旧版迁移",stamp,stamp))
                new=dst.execute("SELECT id FROM cages WHERE code=? COLLATE NOCASE",(cage_code,)).fetchone()
                if old_cage_id is not None and new: cage_map[old_cage_id]=new[0]
        # 恢复小鼠主体；父母关系需要等所有小鼠都有新内部 ID 后再做第二遍更新。
        id_map={}
        for row in mouse_rows:
            old_id=value(row,"id",default=None); code=str(value(row,"code","mouse_id","ear_tag",default="") or "").strip()
            if not code: code=f"LEGACY-{old_id}"
            sex=str(value(row,"sex","gender",default="U") or "U").upper(); sex=sex if sex in ("M","F","U") else "U"
            status=str(value(row,"status",default="Active") or "Active"); status=status if status in ("Active","Experimental","Transferred","Dead") else "Active"
            strain=str(value(row,"strain","strain_name",default="") or ""); line=str(value(row,"line","line_name",default="") or ""); genotype=str(value(row,"genotype","genotype_name",default="") or "")
            cur=dst.execute("""INSERT INTO mice(code,ear_tag,sex,dob,status,strain_id,line_id,genotype_id,generation,coat_color,cage_id,
                father_id,mother_id,source_type,arrival_date,death_date,death_reason,transfer_date,transfer_destination,transfer_reason,
                experimental_status,owner,project,comment,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'Import',?,?,?,?,?,?,?,?,?,?,?,?)""",
                (code,str(value(row,"ear_tag","tag",default="") or ""),sex,value(row,"dob","birth_date",default=None),status,
                 maps["strains"].get(strain.casefold()),maps["mouse_lines"].get(line.casefold()),maps["genotypes"].get(genotype.casefold()),
                 str(value(row,"generation",default="") or ""),str(value(row,"coat_color",default="") or ""),cage_map.get(value(row,"cage_id","cageId",default=None)),None,None,
                 value(row,"arrival_date",default=None),value(row,"death_date",default=None),str(value(row,"death_reason",default="") or ""),
                 value(row,"transfer_date",default=None),str(value(row,"transfer_destination",default="") or ""),"",
                 str(value(row,"experimental_status",default="") or ""),str(value(row,"owner",default="") or ""),
                 str(value(row,"project",default="") or ""),str(value(row,"comment","notes",default="") or ""),stamp,stamp))
            if old_id is not None: id_map[old_id]=cur.lastrowid
        for row in mouse_rows:
            new_id=id_map.get(value(row,"id",default=None))
            if new_id:
                dst.execute("UPDATE mice SET father_id=?,mother_id=? WHERE id=?",(id_map.get(value(row,"father_id","fatherId",default=None)),id_map.get(value(row,"mother_id","motherId",default=None)),new_id))
        # experiments/activities 的字段在不同 Node 版差异较大；仅复制能明确关联到小鼠的记录。
        event_count=0
        for event_table in ("experiments","activities"):
            if event_table not in tables: continue
            for event in src.execute(f"SELECT * FROM {event_table}"):
                mouse_id=id_map.get(value(event,"mouse_id","mouseId",default=None))
                if not mouse_id: continue
                event_date=str(value(event,"event_date","date","created_at",default=datetime.now().date().isoformat()) or datetime.now().date().isoformat())[:10]
                title=str(value(event,"title","type","name",default=event_table) or event_table)
                details=str(value(event,"details","description","notes",default="") or "")
                dst.execute("INSERT INTO mouse_events(mouse_id,event_type,event_date,title,details,created_at) VALUES(?,?,?,?,?,?)",(mouse_id,event_table,event_date,title,details,stamp));event_count+=1
        target.audit(dst,"migration","database",None,f"从早期 Node/Wrangler 数据库迁移 {len(mouse_rows)} 只小鼠")
    finally: src.close()


def migrate_database(source_selection: str|Path, target_path: str|Path=DEFAULT_TARGET) -> dict[str, Any]:
    source=locate_source(Path(source_selection)); target=Path(target_path).resolve()
    if source.resolve()==target: raise ValueError("源数据库和目标数据库不能是同一文件")
    _,kind=validate_sqlite(source); target.parent.mkdir(parents=True,exist_ok=True)
    before=counts(source); backup=None
    with tempfile.TemporaryDirectory(prefix="mousdb_migration_") as tmp:
        temporary=Path(tmp)/"converted.sqlite3"
        (copy_python_source if kind=="python" else convert_node_source)(source,temporary)
        check=Database(temporary).integrity_check()
        if check!="ok": raise RuntimeError(f"临时目标完整性检查失败：{check}")
        after=counts(temporary)
        if target.exists():
            backup=target.parent/"backups"/f"pre_migration_{datetime.now():%Y%m%d_%H%M%S}.sqlite3"
            Database(target).backup_to(backup)
        replacement=target.with_suffix(target.suffix+".new")
        shutil.copy2(temporary,replacement); os.replace(replacement,target)
    with Database(target).write() as conn:
        Database.audit(conn,"migration","database",None,f"迁移来源 {source.name}；类型 {kind}")
    return {"source":str(source),"target":str(target),"kind":kind,"before":before,"after":after,"backup":str(backup) if backup else None,"integrity":"ok"}


def choose_source() -> Path|None:
    import tkinter as tk
    from tkinter import filedialog, messagebox
    root=tk.Tk(); root.withdraw(); root.attributes("-topmost",True)
    choice=messagebox.askyesnocancel("选择旧数据来源","选择“是”：旧 SQLite 数据库文件\n选择“否”：旧版完整程序文件夹\n选择“取消”：不执行迁移",parent=root)
    if choice is None:
        root.destroy(); return None
    if choice:
        selected=filedialog.askopenfilename(title="选择旧 MousDB SQLite 数据库",filetypes=[("SQLite 数据库","*.sqlite3 *.sqlite *.db"),("所有文件","*.*")],parent=root)
    else:
        selected=filedialog.askdirectory(title="选择旧 MousDB 完整程序文件夹",parent=root)
    root.destroy(); return Path(selected) if selected else None


def main() -> None:
    parser=argparse.ArgumentParser(description="安全迁移旧 MousDB 数据")
    parser.add_argument("source",nargs="?",help="旧数据库或旧程序文件夹")
    parser.add_argument("--target",default=str(DEFAULT_TARGET),help="新数据库路径")
    args=parser.parse_args(); source=Path(args.source) if args.source else choose_source()
    if not source: print("已取消，未修改任何数据。"); return
    result=migrate_database(source,args.target)
    print("迁移成功，完整性检查：ok"); print(f"源：{result['source']}"); print(f"目标：{result['target']}")
    if result["backup"]: print(f"迁移前目标备份：{result['backup']}")
    print("关键数量（源 -> 新）：")
    for key in result["after"]: print(f"  {key}: {result['before'][key]} -> {result['after'][key]}")


if __name__=="__main__": main()
