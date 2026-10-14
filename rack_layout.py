"""真实架位的事务服务：横8列×竖9行及架旁临时区。

输入：rack_id、固定行列、cage_id和客户端看到的revision。
输出：同一时刻的架子/笼卡快照；移动、交换、撤销及建笼结果。
本模块只增加布局表，动物编号、基因型、死亡和给药记录仍由原db.py管理。
物理槽位与cage是两个概念：移位不会把小鼠转到另一只cage。
"""
from __future__ import annotations
import json,re,sqlite3
from datetime import date
from db import Database,ValidationError,now_text,integer

ROWS,COLUMNS=9,8
STATES={'holding':'饲养','experimental':'实验','breeding':'繁育','pending':'待处理','quarantine':'隔离','empty':'空笼'}

class LayoutConflict(ValidationError):
    """布局过期；HTTP层返回409，要求客户端重新读取而非覆盖新位置。"""

class RackLayoutStore:
    def __init__(self,database:Database):
        self.db=database

    def initialize(self):
        """幂等迁移：只建新增表，旧cage第一次进入待定位区，不猜真实坐标。"""
        with self.db.write() as c:
            # 每个架子独立revision；NULL行列表示架旁临时停放。
            c.execute('''CREATE TABLE IF NOT EXISTS rack_layouts(
                rack_id INTEGER PRIMARY KEY REFERENCES racks(id) ON DELETE CASCADE,
                rows INTEGER NOT NULL DEFAULT 9 CHECK(rows=9),
                columns INTEGER NOT NULL DEFAULT 8 CHECK(columns=8),
                revision INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL)''')
            c.execute('''CREATE TABLE IF NOT EXISTS cage_positions(
                cage_id INTEGER PRIMARY KEY REFERENCES cages(id) ON DELETE CASCADE,
                rack_id INTEGER NOT NULL REFERENCES racks(id) ON DELETE CASCADE,
                row_index INTEGER,column_index INTEGER,card_state TEXT,
                CHECK((row_index IS NULL AND column_index IS NULL) OR
                    (row_index IS NOT NULL AND column_index IS NOT NULL AND row_index BETWEEN 1 AND 9 AND column_index BETWEEN 1 AND 8)),
                CHECK(card_state IS NULL OR card_state IN
                    ('holding','experimental','breeding','pending','quarantine','empty')))''')
            c.execute('''CREATE UNIQUE INDEX IF NOT EXISTS idx_rack_unique_slot
                ON cage_positions(rack_id,row_index,column_index) WHERE row_index IS NOT NULL''')
            c.execute('''CREATE TABLE IF NOT EXISTS rack_layout_history(
                id INTEGER PRIMARY KEY,rack_id INTEGER NOT NULL REFERENCES racks(id),
                action TEXT NOT NULL,before_json TEXT NOT NULL,after_json TEXT NOT NULL,
                revision INTEGER NOT NULL,created_at TEXT NOT NULL,undo_of INTEGER,undone_at TEXT)''')
            self._upgrade_orientation(c)
            self._sync(c)
            c.execute("INSERT OR REPLACE INTO schema_meta(key,value) VALUES('rack_layout_version','2')")

    def _upgrade_orientation(self,c):
        """同一事务把旧8行9列约束升级为9行8列。

        输入：旧布局元数据、当前坐标；输出：新约束与原有效坐标。
        第9列不再存在，相关笼只进入临时区，不猜测新的物理位置。
        小鼠cage_id、品系、事件及历史表不改；reshape历史阻止撤销旧尺寸的移位。
        """
        definition=c.execute("SELECT sql FROM sqlite_master WHERE name='rack_layouts'").fetchone()['sql']
        legacy=bool(re.search(r'CHECK\s*\(\s*rows\s*=\s*8\s*\)',definition,re.I))
        if not legacy:return
        layouts=[dict(r) for r in c.execute('SELECT * FROM rack_layouts')]
        positions=[dict(r) for r in c.execute('SELECT * FROM cage_positions')]
        # 两张布局表只引用原有racks/cages；历史表引用racks，可原样保留。
        c.execute('''CREATE TABLE rack_layouts_next(
            rack_id INTEGER PRIMARY KEY REFERENCES racks(id) ON DELETE CASCADE,
            rows INTEGER NOT NULL DEFAULT 9 CHECK(rows=9),
            columns INTEGER NOT NULL DEFAULT 8 CHECK(columns=8),
            revision INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL)''')
        c.execute('''CREATE TABLE cage_positions_next(
            cage_id INTEGER PRIMARY KEY REFERENCES cages(id) ON DELETE CASCADE,
            rack_id INTEGER NOT NULL REFERENCES racks(id) ON DELETE CASCADE,
            row_index INTEGER,column_index INTEGER,card_state TEXT,
            CHECK((row_index IS NULL AND column_index IS NULL) OR
                (row_index IS NOT NULL AND column_index IS NOT NULL AND row_index BETWEEN 1 AND 9 AND column_index BETWEEN 1 AND 8)),
            CHECK(card_state IS NULL OR card_state IN
                ('holding','experimental','breeding','pending','quarantine','empty')))''')
        for layout in layouts:
            c.execute('INSERT INTO rack_layouts_next(rack_id,revision,updated_at) VALUES(?,?,?)',
                      (layout['rack_id'],layout['revision'],layout['updated_at']))
        for position in positions:
            row,col=position['row_index'],position['column_index']
            if row is not None and (row>ROWS or col>COLUMNS):row=col=None
            c.execute('INSERT INTO cage_positions_next VALUES(?,?,?,?,?)',
                      (position['cage_id'],position['rack_id'],row,col,position['card_state']))
        c.execute('DROP TABLE cage_positions')
        c.execute('DROP TABLE rack_layouts')
        c.execute('ALTER TABLE rack_layouts_next RENAME TO rack_layouts')
        c.execute('ALTER TABLE cage_positions_next RENAME TO cage_positions')
        c.execute('CREATE UNIQUE INDEX idx_rack_unique_slot ON cage_positions(rack_id,row_index,column_index) WHERE row_index IS NOT NULL')
        for layout in layouts:
            rid=layout['rack_id']
            before=[p for p in positions if p['rack_id']==rid]
            after=[dict(p) for p in c.execute('SELECT * FROM cage_positions WHERE rack_id=?',(rid,))]
            self._record(c,rid,'reshape',before,after)
            parked=sum(p['column_index']==9 for p in before)
            self.db.audit(c,'reshape','rack',rid,'架位改为横8列/竖9行；原第9列进入临时区：'+str(parked)+'笼')

    def _touch(self,c,rack_id):
        c.execute('UPDATE rack_layouts SET revision=revision+1,updated_at=? WHERE rack_id=?',(now_text(),rack_id))

    def _sync(self,c):
        """兼容旧表单/CSV新建笼及跨架编辑；未定位新笼进入临时区。
        已停用且无活鼠的旧笼保留cage及历史，释放物理位置，不占首页格子。
        """
        c.execute('''INSERT OR IGNORE INTO rack_layouts(rack_id,updated_at)
            SELECT id,? FROM racks''',(now_text(),))
        live=c.execute('''SELECT c.* FROM cages c WHERE c.active=1 OR EXISTS(
            SELECT 1 FROM mice m WHERE m.cage_id=c.id AND m.status IN ('Active','Experimental'))''').fetchall()
        live_ids={x['id'] for x in live};changed=set()
        for p in c.execute('SELECT * FROM cage_positions').fetchall():
            if p['cage_id'] not in live_ids:
                c.execute('DELETE FROM cage_positions WHERE cage_id=?',(p['cage_id'],));changed.add(p['rack_id'])
        for cage in live:
            p=c.execute('SELECT * FROM cage_positions WHERE cage_id=?',(cage['id'],)).fetchone()
            if p is None:
                c.execute('INSERT INTO cage_positions(cage_id,rack_id) VALUES(?,?)',(cage['id'],cage['rack_id']));changed.add(cage['rack_id'])
            elif p['rack_id']!=cage['rack_id']:
                c.execute('UPDATE cage_positions SET rack_id=?,row_index=NULL,column_index=NULL WHERE cage_id=?',(cage['rack_id'],cage['id']))
                changed.update([p['rack_id'],cage['rack_id']])
        for rack_id in changed:self._touch(c,rack_id)

    @staticmethod
    def _positive(value,label):
        if isinstance(value,bool) or not isinstance(value,(int,str)) or not re.fullmatch(r'\d+',str(value)):
            raise ValidationError(label+'必须是整数')
        return integer(value,label,minimum=1)

    def _slot(self,payload):
        """只能同时给出行列，或同时为NULL；禁止超过72格或半个坐标。"""
        row,col=payload.get('row'),payload.get('column')
        if row is None and col is None:return None,None
        row=self._positive(row,'行');col=self._positive(col,'列')
        if row>ROWS or col>COLUMNS:raise ValidationError('笼位超出横8列×竖9行')
        return row,col

    def _rack(self,c,rack_id,expected=None):
        row=c.execute('''SELECT k.*,r.name room_name,r.building,l.rows,l.columns,l.revision
            FROM racks k JOIN rooms r ON r.id=k.room_id JOIN rack_layouts l ON l.rack_id=k.id
            WHERE k.id=?''',(rack_id,)).fetchone()
        if not row:raise ValidationError('架子不存在')
        if expected is not None:
            if isinstance(expected,bool) or str(expected)!=str(row['revision']):
                raise LayoutConflict('架位已被其他操作更新，请刷新后重试')
        return dict(row)

    def list_racks(self):
        with self.db.write() as c:
            self._sync(c)
            return [dict(row) for row in c.execute('''SELECT k.id,k.name,k.room_id,k.note,k.active,
                r.name room_name,r.building,l.rows,l.columns,l.revision,
                (SELECT COUNT(*) FROM cage_positions p WHERE p.rack_id=k.id AND p.row_index IS NOT NULL) placed,
                (SELECT COUNT(*) FROM cage_positions p WHERE p.rack_id=k.id AND p.row_index IS NULL) parked
                FROM racks k JOIN rooms r ON r.id=k.room_id JOIN rack_layouts l ON l.rack_id=k.id
                WHERE k.active=1 ORDER BY r.name,k.name,k.id''')]

    def _age(self,m):
        """架位与小鼠档案共用周龄算法，候选生日显示同一个范围。"""
        return self.db._add_ages(m)

    def _board(self,c,rack_id):
        rack=self._rack(c,rack_id);cards=[]
        for row in c.execute('''SELECT c.*,p.row_index,p.column_index,p.card_state
            FROM cages c JOIN cage_positions p ON p.cage_id=c.id
            WHERE p.rack_id=? ORDER BY c.code,c.id''',(rack_id,)):
            card=dict(row);card['genotyping']=self.db.cage_genotyping(c,row['id'])
            mice=c.execute('''SELECT m.*,s.name strain_name,s.name_html strain_name_html,
                l.name line_name,l.name_html line_name_html,g.name genotype_name,g.name_html genotype_name_html
                FROM mice m LEFT JOIN strains s ON s.id=m.strain_id LEFT JOIN mouse_lines l ON l.id=m.line_id
                LEFT JOIN genotypes g ON g.id=m.genotype_id
                WHERE m.cage_id=? AND m.status IN ('Active','Experimental') ORDER BY m.ear_tag,m.id''',(row['id'],)).fetchall()
            card['mice']=[self._age(dict(m)) for m in mice];card['occupied']=len(mice)
            breeding=c.execute("SELECT 1 FROM matings WHERE cage_id=? AND status='Active'",(card['id'],)).fetchone()
            inferred=('pending' if not mice and card['genotyping']['pending'] else 'empty' if not mice else 'experimental' if any(m['status']=='Experimental' for m in mice)
                else 'breeding' if breeding else 'holding')
            card['state']=card['card_state'] or inferred;card['state_label']=STATES[card['state']]
            cards.append(card)
        last=c.execute('SELECT * FROM rack_layout_history WHERE rack_id=? ORDER BY id DESC LIMIT 1',(rack_id,)).fetchone()
        return {'rack':rack,'cards':cards,'states':STATES,'placed':sum(x['row_index'] is not None for x in cards),
            'parked':sum(x['row_index'] is None for x in cards),'mouse_count':sum(x['occupied'] for x in cards),
            'undo_id':last['id'] if last and last['action']=='move' and not last['undone_at'] else None}

    def board(self,rack_id):
        with self.db.write() as c:
            self._sync(c);return self._board(c,rack_id)

    def _record(self,c,rack_id,action,before,after,undo_of=None):
        self._touch(c,rack_id);rev=self._rack(c,rack_id)['revision']
        cur=c.execute('''INSERT INTO rack_layout_history(rack_id,action,before_json,after_json,
            revision,created_at,undo_of) VALUES(?,?,?,?,?,?,?)''',
            (rack_id,action,json.dumps(before,ensure_ascii=False),json.dumps(after,ensure_ascii=False),rev,now_text(),undo_of))
        self.db.audit(c,action,'rack_layout',rack_id,'整笼架位操作；revision='+str(rev))
        return cur.lastrowid

    def move(self,rack_id,payload):
        """单事务移位/交换：先清空两者槽位，再写目标，保证唯一约束不会阻塞交换。
        同一架子内移动不改变cage_id和小鼠所属笼。NULL目标可停放任意多笼。
        """
        cid=self._positive(payload.get('cage_id'),'笼ID');row,col=self._slot(payload)
        with self.db.write() as c:
            self._sync(c);self._rack(c,rack_id,payload.get('expected_revision',-1))
            source=c.execute('SELECT * FROM cage_positions WHERE cage_id=? AND rack_id=?',(cid,rack_id)).fetchone()
            if not source:raise ValidationError('该笼不在当前架子')
            if source['row_index']==row and source['column_index']==col:return self._board(c,rack_id)
            target=c.execute('SELECT * FROM cage_positions WHERE rack_id=? AND row_index=? AND column_index=?',(rack_id,row,col)).fetchone() if row else None
            before=[dict(source)]+([dict(target)] if target else [])
            for p in before:c.execute('UPDATE cage_positions SET row_index=NULL,column_index=NULL WHERE cage_id=?',(p['cage_id'],))
            c.execute('UPDATE cage_positions SET row_index=?,column_index=? WHERE cage_id=?',(row,col,cid))
            if target:c.execute('UPDATE cage_positions SET row_index=?,column_index=? WHERE cage_id=?',(source['row_index'],source['column_index'],target['cage_id']))
            after=[dict(c.execute('SELECT * FROM cage_positions WHERE cage_id=?',(p['cage_id'],)).fetchone()) for p in before]
            self._record(c,rack_id,'move',before,after)
            return self._board(c,rack_id)

    def undo(self,rack_id,payload):
        """只撤销最近一次位置操作；后续建笼/状态编辑后停止撤销，避免覆盖新数据。"""
        with self.db.write() as c:
            self._sync(c);self._rack(c,rack_id,payload.get('expected_revision',-1))
            last=c.execute('SELECT * FROM rack_layout_history WHERE rack_id=? ORDER BY id DESC LIMIT 1',(rack_id,)).fetchone()
            if not last or last['action']!='move' or last['undone_at']:raise ValidationError('没有可撤销的位置移动')
            before=json.loads(last['before_json']);after=json.loads(last['after_json'])
            for p in after:
                current=c.execute('SELECT * FROM cage_positions WHERE cage_id=?',(p['cage_id'],)).fetchone()
                if not current or dict(current)!=p:raise LayoutConflict('移动后的笼位已改变，不能撤销')
            for p in before:c.execute('UPDATE cage_positions SET row_index=NULL,column_index=NULL WHERE cage_id=?',(p['cage_id'],))
            for p in before:c.execute('UPDATE cage_positions SET row_index=?,column_index=? WHERE cage_id=?',(p['row_index'],p['column_index'],p['cage_id']))
            c.execute('UPDATE rack_layout_history SET undone_at=? WHERE id=?',(now_text(),last['id']))
            self._record(c,rack_id,'undo',after,before,last['id'])
            return self._board(c,rack_id)

    def create_slot_cage(self,rack_id,payload):
        """空位建笼和随附录鼠同一事务：任一只鼠校验失败，笼与位置全部回滚。"""
        row,col=self._slot(payload);data=payload.get('cage')
        if not isinstance(data,dict):raise ValidationError('请填写笼资料')
        code=str(data.get('code') or '').strip()
        if not code:raise ValidationError('笼号不能为空')
        mice=data.get('mice') or []
        if not isinstance(mice,list) or len(mice)>100:raise ValidationError('随笼录鼠资料无效')
        with self.db.write() as c:
            self._sync(c);self._rack(c,rack_id,payload.get('expected_revision',-1))
            if row and c.execute('SELECT 1 FROM cage_positions WHERE rack_id=? AND row_index=? AND column_index=?',(rack_id,row,col)).fetchone():
                raise LayoutConflict('该笼位已被占用，请刷新后重试')
            stamp=now_text()
            try:
                cur=c.execute('''INSERT INTO cages(rack_id,code,capacity,purpose,color,active,note,created_at,updated_at)
                    VALUES(?,?,?,?,?,1,?,?,?)''',(rack_id,code,integer(data.get('capacity',5),'笼容量',minimum=1),
                    str(data.get('purpose') or ''),str(data.get('color') or ''),str(data.get('note') or ''),stamp,stamp))
            except sqlite3.IntegrityError as exc:raise ValidationError('笼号已存在，请使用不同笼号') from exc
            cid=cur.lastrowid
            for mouse in mice:
                if not isinstance(mouse,dict):raise ValidationError('小鼠资料无效')
                self.db._create_mouse(c,{**mouse,'cage_id':cid})
            c.execute('INSERT INTO cage_positions(cage_id,rack_id,row_index,column_index) VALUES(?,?,?,?)',(cid,rack_id,row,col))
            self.db.audit(c,'create','cage',cid,'从架位新建笼：'+code)
            self._record(c,rack_id,'create',[],[dict(c.execute('SELECT * FROM cage_positions WHERE cage_id=?',(cid,)).fetchone())])
            result=self._board(c,rack_id);result['created_cage_id']=cid;return result

    def card_state(self,rack_id,cage_id,payload):
        value=payload.get('card_state') or None
        if value is not None and value not in STATES:raise ValidationError('笼状态无效')
        with self.db.write() as c:
            self._sync(c);self._rack(c,rack_id,payload.get('expected_revision',-1))
            p=c.execute('SELECT * FROM cage_positions WHERE rack_id=? AND cage_id=?',(rack_id,cage_id)).fetchone()
            if not p:raise ValidationError('该笼不在当前架子')
            if value=='empty' and self.db.cage_genotyping(c,cage_id)['pending']:raise ValidationError('有待鉴定幼鼠的笼不能标记为空笼')
            if value=='empty' and c.execute("SELECT 1 FROM mice WHERE cage_id=? AND status IN ('Active','Experimental')",(cage_id,)).fetchone():
                raise ValidationError('笼内还有在养小鼠，不能标记为空笼')
            c.execute('UPDATE cage_positions SET card_state=? WHERE cage_id=?',(value,cage_id))
            self._record(c,rack_id,'state',[dict(p)],[dict(c.execute('SELECT * FROM cage_positions WHERE cage_id=?',(cage_id,)).fetchone())])
            return self._board(c,rack_id)

    def save_rack(self,payload,rack_id=None):
        """架子是真实房间的受控选项；新架子固定72笼位，不生成72只假cage。"""
        with self.db.write() as c:
            if rack_id is None:
                rack=self.db.create_rack({**payload,'capacity':72},c);rack_id=rack['id']
            else:
                self._rack(c,rack_id);name=str(payload.get('name') or '').strip()
                room_id=self._positive(payload.get('room_id'),'房间ID')
                if not name:raise ValidationError('架子名称不能为空')
                try:c.execute('UPDATE racks SET room_id=?,name=?,capacity=72,note=?,updated_at=? WHERE id=?',(room_id,name,str(payload.get('note') or ''),now_text(),rack_id))
                except sqlite3.IntegrityError as exc:raise ValidationError('房间不存在或架子名称重复') from exc
                self.db.audit(c,'update','rack',rack_id,'编辑真实架子：'+name)
            self._sync(c);return self._board(c,rack_id)
