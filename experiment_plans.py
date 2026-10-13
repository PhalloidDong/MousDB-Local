"""实验计划与站内提醒。

输入：明确的计划日期、类型、耳号，或现有备注里的带日期计划。
输出：可编辑的计划清单，以及服务器本地日期的今天/明天提醒。
计划和实际操作分开保存；模糊时间、预留用途不猜日期，不自动记为给药或取材。
"""
from __future__ import annotations
import re
from datetime import date, timedelta
from db import ValidationError, now_text, parse_date

PLAN_TYPES = ('取材','注射Tam','注射Bleo','实验','配鼠','检栓','成像','样本处理','其他')
STATUSES = ('pending','completed','cancelled')
ISO_DATE = r'\d{4}-\d{2}-\d{2}'

class PlanStore:
    def __init__(self, database):
        self.db = database

    def initialize(self):
        """幂等新增计划表；不改原小鼠、事件、笼或架位数据。"""
        with self.db.write() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS experiment_plans(
                id INTEGER PRIMARY KEY,title TEXT NOT NULL,plan_type TEXT NOT NULL,
                plan_date TEXT NOT NULL,plan_time TEXT NOT NULL DEFAULT '',
                details TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','completed','cancelled')),
                source TEXT NOT NULL DEFAULT 'user',source_key TEXT UNIQUE,
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL,completion_date TEXT)''')
            c.execute('''CREATE TABLE IF NOT EXISTS experiment_plan_mice(
                plan_id INTEGER NOT NULL REFERENCES experiment_plans(id) ON DELETE CASCADE,
                mouse_id INTEGER NOT NULL REFERENCES mice(id) ON DELETE RESTRICT,
                PRIMARY KEY(plan_id,mouse_id))''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_plans_due ON experiment_plans(status,plan_date)')
            self._sync(c)

    @staticmethod
    def classify(text):
        """只归类明确写出的动作；收Tam属于其他任务，不推定为注射Tam。"""
        if re.search(r'收\s*Tam',text,re.I): return '其他'
        for kind,pattern in [('检栓',r'检栓|查栓'),('配鼠',r'配鼠|合笼|交配'),('取材',r'取材|采样'),
                             ('成像',r'成像|imaging'),('样本处理',r'PFA|PBS|换液|固定')]:
            if re.search(pattern,text,re.I): return kind
        if re.search(r'Tam|他莫昔芬',text,re.I): return '注射Tam'
        if re.search(r'Bleo|博来霉素',text,re.I): return '注射Bleo'
        if re.search(r'实验|手术',text): return '实验'
        return '其他'

    def _candidates(self,c):
        """解析有明确年月日的计划。

        支持原实验计划区块中的Tam/Bleo/取材字段，以及单独的
        “计划：2026-10-14 检栓”这类行。不能识别的自由文字保留原文，
        不强行猜日期；用户可以使用网页的新增计划表单精确录入。
        """
        candidates = {}
        def add(key,title,kind,day,details,mouse_id,source='comment',time=''):
            try: day=parse_date(day,'计划日期',required=True)
            except ValidationError: return
            candidates[key]=dict(source_key=key,title=title,plan_type=kind,plan_date=day,
                                 plan_time=time,details=details,mouse_ids=[mouse_id],source=source)
        for m in c.execute('SELECT id,comment FROM mice'):
            for line in (m['comment'] or '').splitlines():
                # 原有计划只解析字段本身，后文的解释性日期不会再变成重复任务。
                block=re.search(r'【\d+实验计划】\s*([^；]+)',line)
                if block:
                    group=block.group(1).strip()
                    for field,kind in [('Tam','注射Tam'),('Bleo','注射Bleo'),('取材','取材')]:
                        match=re.search(r'(?:^|；)\s*'+field+r'=([^；]*)',line)
                        if not match: continue
                        for day in re.findall(ISO_DATE,match.group(1)):
                            title=kind+' · '+('Bleo组' if field=='Tam' and group.startswith('Bleo') else group)
                            add(f"comment:{m['id']}:{kind}:{day}",title,kind,day,line,m['id'])
                    continue
                explicit=re.search(r'(?:计划|安排|预计|预定)[：:]\s*(.+)',line)
                if not explicit or '已完成' in explicit.group(1): continue
                body=explicit.group(1);days=re.findall(ISO_DATE,body)
                if not days: continue
                kind=self.classify(body)
                title=re.sub(ISO_DATE,'',body).strip(' ；;，,：:') or kind
                clock=re.search(r'(?<!\d)(?:[01]\d|2[0-3]):[0-5]\d(?!\d)',body)
                for day in days:
                    add(f"comment:{m['id']}:{kind}:{day}",title,kind,day,body,m['id'],time=clock.group(0) if clock else '')
        # 已有“计划”事件也接入；已执行的事件不会被误当作计划。
        for e in c.execute('SELECT * FROM mouse_events'):
            if not re.search(r'计划|预计|预定',e['title']) or re.search(r'已完成|取消',e['title']): continue
            kind=self.classify(e['title'])
            if any(p['mouse_ids']==[e['mouse_id']] and p['plan_type']==kind and p['plan_date']==e['event_date'] for p in candidates.values()):
                continue
            add(f"event:{e['id']}",e['title'],kind,e['event_date'],e['details'],e['mouse_id'],'event')
        return candidates

    def _sync(self,c):
        """把备注计划幂等同步；用户修改过的计划优先，避免刷新撤销用户编辑。

        同日同鼠同类型且标题明确已完成的实际事件，作为完成依据。
        删除/改期的备注对应旧未完成任务自动取消；旧完成事实仍保留。
        """
        candidates=self._candidates(c);stamp=now_text()
        completed={(e['mouse_id'],e['event_date'],self.classify(e['title']))
                   for e in c.execute('SELECT * FROM mouse_events')
                   if re.search(r'已完成',e['title']) and not e['title'].startswith('计划已完成：')}
        for key,p in candidates.items():
            old=c.execute('SELECT * FROM experiment_plans WHERE source_key=?',(key,)).fetchone()
            done=all((mid,p['plan_date'],p['plan_type']) in completed for mid in p['mouse_ids'])
            if old:
                if old['source']=='user': continue
                # 用户没有手工覆盖时，备注中的标题/时间/详情修订同步到原任务。
                # source=user的记录已在上面跳过，人工改期或取消不会被此逻辑撤销。
                status='completed' if done else 'pending' if old['status']=='cancelled' else old['status']
                finished=p['plan_date'] if done else old['completion_date']
                if (old['title'],old['plan_time'],old['details'],old['status'])!=(p['title'],p['plan_time'],p['details'],status):
                    c.execute("UPDATE experiment_plans SET title=?,plan_time=?,details=?,status=?,completion_date=?,updated_at=? WHERE id=?",
                              (p['title'],p['plan_time'],p['details'],status,finished,stamp,old['id']))
                continue
            cur=c.execute('''INSERT INTO experiment_plans(title,plan_type,plan_date,plan_time,details,
                status,source,source_key,created_at,updated_at,completion_date) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                (p['title'],p['plan_type'],p['plan_date'],p['plan_time'],p['details'],
                 'completed' if done else 'pending',p['source'],key,stamp,stamp,p['plan_date'] if done else None))
            for mid in p['mouse_ids']:
                c.execute('INSERT INTO experiment_plan_mice(plan_id,mouse_id) VALUES(?,?)',(cur.lastrowid,mid))
        for old in c.execute("SELECT id,source_key FROM experiment_plans WHERE source IN ('comment','event') AND status='pending'").fetchall():
            if old['source_key'] not in candidates:
                c.execute("UPDATE experiment_plans SET status='cancelled',updated_at=? WHERE id=?",(stamp,old['id']))

    def _rows(self,c):
        """地点按当前笼位读取；转笼后提醒也随实际位置更新。"""
        rows=[]
        for p in c.execute('SELECT * FROM experiment_plans ORDER BY plan_date,plan_time,id'):
            item=dict(p)
            item['mice']=[dict(m) for m in c.execute('''SELECT m.id,m.code,m.ear_tag,m.sex,m.status,
                c.code cage_code,k.id rack_id,k.name rack_name,r.name room_name
                FROM experiment_plan_mice pm JOIN mice m ON m.id=pm.mouse_id
                LEFT JOIN cages c ON c.id=m.cage_id LEFT JOIN racks k ON k.id=c.rack_id
                LEFT JOIN rooms r ON r.id=k.room_id WHERE pm.plan_id=? ORDER BY m.id''',(p['id'],))]
            rows.append(item)
        return rows

    def list(self):
        with self.db.write() as c:
            self._sync(c)
            return {'plans':self._rows(c),'types':PLAN_TYPES,'today':date.today().isoformat()}

    def reminders(self,today=None):
        """只提醒当天/明天未完成任务；用本机日期，避免浏览器UTC跨日偏差。

        today仅用于隔离测试模拟跨日。批次内同日同任务合并显示耳号，
        多只小鼠的给药不会产生10张重复提醒卡。
        """
        today=today or date.today();tomorrow=today+timedelta(days=1)
        data=self.list();groups={}
        for p in data['plans']:
            if p['status']!='pending' or p['plan_date'] not in (str(today),str(tomorrow)): continue
            # 手工任务彼此独立；自动导入的同批次任务才合并。
            key=(p['plan_date'],p['plan_time'],p['plan_type'],p['title'],p['id'] if p['source']=='user' else 'auto')
            if key not in groups:
                groups[key]={**p,'ids':[],'mice':[],'when':'today' if p['plan_date']==str(today) else 'tomorrow'}
            groups[key]['ids'].append(p['id'])
            for m in p['mice']:
                if not any(x['id']==m['id'] for x in groups[key]['mice']):groups[key]['mice'].append(m)
        return {'today':str(today),'tomorrow':str(tomorrow),'items':list(groups.values())}

    def _mouse_ids(self,c,payload,old_ids):
        if 'mouse_ids' in payload:
            values=payload['mouse_ids']
            if not isinstance(values,list) or any(isinstance(x,bool) or not str(x).isdigit() for x in values):
                raise ValidationError('小鼠ID必须为整数列表')
            mids=list(dict.fromkeys(int(x) for x in values))
        elif 'ear_tags' in payload:
            mids=[]
            for tag in re.split(r'[\s,，;；、]+',str(payload['ear_tags']).strip()):
                if not tag: continue
                matches=c.execute('SELECT id FROM mice WHERE ear_tag=?',(tag,)).fetchall()
                if len(matches)!=1: raise ValidationError('耳号'+tag+'不存在或不唯一，请从小鼠档案添加计划')
                if matches[0]['id'] not in mids:mids.append(matches[0]['id'])
        else: mids=old_ids
        if any(not c.execute('SELECT 1 FROM mice WHERE id=?',(mid,)).fetchone() for mid in mids):
            raise ValidationError('所选小鼠不存在')
        return mids

    def save(self,payload,plan_id=None):
        """保存明确日期的任务；完成标记只写计划日志，不虚构体重或注射实测值。"""
        with self.db.write() as c:
            old=c.execute('SELECT * FROM experiment_plans WHERE id=?',(plan_id,)).fetchone() if plan_id else None
            if plan_id and not old:raise ValidationError('计划不存在')
            merged={**(dict(old) if old else {}),**payload}
            title=str(merged.get('title') or '').strip();kind=merged.get('plan_type','实验')
            if not title or len(title)>240:raise ValidationError('计划标题不能为空且不得超过240字')
            if kind not in PLAN_TYPES:raise ValidationError('计划类型无效')
            day=parse_date(merged.get('plan_date'),'计划日期',required=True)
            time=str(merged.get('plan_time') or '').strip()
            if time and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',time):raise ValidationError('时间请使用HH:MM')
            status=merged.get('status','pending')
            if status not in STATUSES:raise ValidationError('计划状态无效')
            mids=self._mouse_ids(c,payload,[r[0] for r in c.execute('SELECT mouse_id FROM experiment_plan_mice WHERE plan_id=?',(plan_id,))] if old else [])
            details=str(merged.get('details') or '').strip();stamp=now_text()
            finished=(old['completion_date'] if old and old['status']=='completed' else date.today().isoformat()) if status=='completed' else None
            if old:
                c.execute('''UPDATE experiment_plans SET title=?,plan_type=?,plan_date=?,plan_time=?,details=?,
                    status=?,source='user',updated_at=?,completion_date=? WHERE id=?''',
                    (title,kind,day,time,details,status,stamp,finished,plan_id))
                c.execute('DELETE FROM experiment_plan_mice WHERE plan_id=?',(plan_id,))
            else:
                plan_id=c.execute('''INSERT INTO experiment_plans(title,plan_type,plan_date,plan_time,details,status,
                    source,created_at,updated_at,completion_date) VALUES(?,?,?,?,?,?,'user',?,?,?)''',
                    (title,kind,day,time,details,status,stamp,stamp,finished)).lastrowid
            for mid in mids:c.execute('INSERT INTO experiment_plan_mice(plan_id,mouse_id) VALUES(?,?)',(plan_id,mid))
            if status=='completed' and (not old or old['status']!='completed'):
                for mid in mids:
                    self.db.audit(c,'plan_completed','mouse',mid,'已确认完成计划：'+title+'；计划ID='+str(plan_id))
            self.db.audit(c,'update' if old else 'create','experiment_plan',plan_id,title+'；'+day+'；'+status)
            return next(p for p in self._rows(c) if p['id']==plan_id)
