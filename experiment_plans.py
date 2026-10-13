"""实验计划与站内提醒。

输入：明确的计划日期、类型、耳号，或现有备注里的带日期计划。
输出：可编辑的计划清单，以及服务器本地日期的今天/明天提醒。
计划和实际操作分开保存；模糊时间、预留用途不猜日期，不自动记为给药或取材。
"""
from __future__ import annotations
import json,re
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
            # 一个批次可以对应多条旧备注来源；独立保存来源，刷新不能再造单鼠任务。
            c.execute("""CREATE TABLE IF NOT EXISTS experiment_plan_sources(
                source_key TEXT PRIMARY KEY,plan_id INTEGER NOT NULL
                REFERENCES experiment_plans(id) ON DELETE CASCADE)""")
            # 合并前的完整计划和鼠关联归档；旧ID仍能定位到当前批次，科研历史可回查。
            c.execute("""CREATE TABLE IF NOT EXISTS experiment_plan_merges(
                old_plan_id INTEGER PRIMARY KEY,plan_id INTEGER NOT NULL
                REFERENCES experiment_plans(id) ON DELETE RESTRICT,
                snapshot_json TEXT NOT NULL,merged_at TEXT NOT NULL)""")
            for old in c.execute("SELECT id,source_key FROM experiment_plans WHERE source IN ('comment','event') AND source_key IS NOT NULL").fetchall():
                c.execute('INSERT OR IGNORE INTO experiment_plan_sources VALUES(?,?)',(old['source_key'],old['id']))
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
            if time:key+=':'+time  # 同一只鼠同一天不同给药时刻需要保留为两次操作。
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

    @staticmethod
    def _batch_key(p,has_mice=True):
        """同一日期、时刻和动作是一场操作，完成状态另由调用方匹配。

        取材/给药/配鼠/检栓/成像有明确动作类型，多只鼠共用一个批次。
        “实验”“样本处理”“其他”仍按具体标题区分动作；无关联鼠的实验室任务也保留标题。
        空时刻表示当天未指定时刻，与10:00等明确时刻分开，不推定它们同时进行。
        """
        common={'取材','注射Tam','注射Bleo','配鼠','检栓','成像'}
        action=p['plan_type'] if has_mice and p['plan_type'] in common else p['title'].strip()
        return p['plan_date'],p['plan_time'],p['plan_type'],action

    def _details(self,c,items):
        """按原标题/备注汇总，并标注对应耳号；不同剂量、组别等细节不丢失。

        输入为待合并任务及mouse_ids，输出单个批次的备注。相同文字仅保留一份，
        不把多个小鼠重复的同一段说明堆叠十次。
        """
        if len(items)==1:return items[0]['details']
        blocks={}
        for p in items:
            key=p['title'],p['details']
            blocks.setdefault(key,[])
            for mid in p['mouse_ids']:
                if mid not in blocks[key]:blocks[key].append(mid)
        parts=[]
        for (title,details),mids in blocks.items():
            labels=[]
            for mid in mids:
                m=c.execute('SELECT ear_tag,code FROM mice WHERE id=?',(mid,)).fetchone()
                labels.append(m['ear_tag'] or m['code'])
            header=title+('｜耳号：'+'、'.join(labels) if labels else '')
            parts.append(header+('\n'+details if details else ''))
        return '\n\n'.join(parts)

    @staticmethod
    def _next_id(c):
        """新计划避开已归档的旧ID，旧网页链接不会被误解析为另一场实验。"""
        return c.execute('SELECT COALESCE(MAX(id),0)+1 FROM (SELECT id FROM experiment_plans UNION ALL SELECT old_plan_id AS id FROM experiment_plan_merges)').fetchone()[0]

    def _resolve_id(self,c,plan_id):
        """旧单鼠计划ID解析到合并后的批次ID，避免已打开的网页编辑失效。"""
        merged=c.execute('SELECT plan_id FROM experiment_plan_merges WHERE old_plan_id=?',(plan_id,)).fetchone()
        return merged['plan_id'] if merged else plan_id

    def _archive_merge(self,c,old,target):
        """先归档再合并；只删除冗余计划行，不删除小鼠、实际事件和原始备注。"""
        snapshot=dict(old)
        snapshot['mouse_ids']=[r[0] for r in c.execute('SELECT mouse_id FROM experiment_plan_mice WHERE plan_id=?',(old['id'],))]
        # 当前批次再次被合并时，所有旧ID直接指向最终目标，不产生失效的跳转链。
        c.execute('UPDATE experiment_plan_merges SET plan_id=? WHERE plan_id=?',(target,old['id']))
        c.execute('UPDATE experiment_plan_sources SET plan_id=? WHERE plan_id=?',(target,old['id']))
        c.execute('INSERT INTO experiment_plan_merges VALUES(?,?,?,?)',
                  (old['id'],target,json.dumps(snapshot,ensure_ascii=False),now_text()))
        c.execute('DELETE FROM experiment_plans WHERE id=?',(old['id'],))
        self.db.audit(c,'merge','experiment_plan',target,'合并原计划ID='+str(old['id']))

    def _merge_matching(self,c):
        """手工录入和自动导入共用批次规则；同时间的鼠在一个计划中统一编辑。

        已完成/待执行/已取消分开，避免部分小鼠的完成事实被应用到其他小鼠。
        用户编辑过的批次继续优先于原备注自动同步。
        """
        groups={}
        for row in c.execute('SELECT * FROM experiment_plans ORDER BY id'):
            p=dict(row);p['mouse_ids']=[r[0] for r in c.execute('SELECT mouse_id FROM experiment_plan_mice WHERE plan_id=?',(p['id'],))]
            key=self._batch_key(p,bool(p['mouse_ids'])),p['status']
            groups.setdefault(key,[]).append(p)
        for items in groups.values():
            if len(items)<2:continue
            root=items[0];mids=sorted({mid for p in items for mid in p['mouse_ids']})
            title=root['title'] if len({p['title'] for p in items})==1 else root['plan_type']
            source='user' if any(p['source']=='user' for p in items) else root['source']
            c.execute('UPDATE experiment_plans SET title=?,details=?,source=?,updated_at=? WHERE id=?',
                      (title,self._details(c,items),source,now_text(),root['id']))
            for mid in mids:c.execute('INSERT OR IGNORE INTO experiment_plan_mice VALUES(?,?)',(root['id'],mid))
            for old in items[1:]:self._archive_merge(c,old,root['id'])

    def _sync(self,c):
        """把逐鼠备注先归并为批次，再同步单条计划及全体鼠关联。

        来源映射保留每条原备注的身份；用户手工改期/取消/完成后整批以用户记录为准。
        自动来源增加或删除鼠时按当前备注重算；实际完成仍逐鼠判断，再分配完成状态。
        """
        candidates=self._candidates(c);stamp=now_text()
        completed={(e['mouse_id'],e['event_date'],self.classify(e['title']))
                   for e in c.execute('SELECT * FROM mouse_events')
                   if re.search(r'已完成',e['title']) and not e['title'].startswith('计划已完成：')}
        old_rows={p['id']:dict(p) for p in c.execute('SELECT * FROM experiment_plans')}
        aliases={r['source_key']:r['plan_id'] for r in c.execute('SELECT * FROM experiment_plan_sources')}
        groups={}
        for key,p in candidates.items():
            # 兼容旧版来源键未包含时刻的记录；只有原计划时刻一致才复用旧映射。
            if p['plan_time'] and key not in aliases:
                legacy=key[:-6];old_pid=aliases.get(legacy)
                if old_pid in old_rows and old_rows[old_pid]['plan_time']==p['plan_time']:
                    aliases[key]=old_pid
                    c.execute('INSERT OR IGNORE INTO experiment_plan_sources VALUES(?,?)',(key,old_pid))
            old=old_rows.get(aliases.get(key))
            if old and old['source']=='user':continue
            done=all((mid,p['plan_date'],p['plan_type']) in completed for mid in p['mouse_ids'])
            status='completed' if done or old and old['status']=='completed' else 'pending'
            group_key=self._batch_key(p),status
            groups.setdefault(group_key,[]).append(p)
        used=set();assigned={}
        # 先处理待执行批次，保留其ID；部分鼠已实际完成时单独保存完成事实。
        for (key,status),items in sorted(groups.items(),key=lambda x:(x[0][0],x[0][1]!='pending')):
            mids=sorted({mid for p in items for mid in p['mouse_ids']})
            existing=sorted({aliases[p['source_key']] for p in items if p['source_key'] in aliases})
            eligible=[pid for pid in existing if pid not in used and old_rows[pid]['source']!='user'
                      and old_rows[pid]['status']==status and self._batch_key(old_rows[pid])==key]
            pid=eligible[0] if eligible else None
            first=items[0];title=first['title'] if len({p['title'] for p in items})==1 else first['plan_type']
            details=self._details(c,items);finished=first['plan_date'] if status=='completed' else None
            if pid:
                old=old_rows[pid]
                previous=[r[0] for r in c.execute('SELECT mouse_id FROM experiment_plan_mice WHERE plan_id=? ORDER BY mouse_id',(pid,))]
                if (old['title'],old['details'],old['status'],previous)!=(title,details,status,mids):
                    c.execute('UPDATE experiment_plans SET title=?,details=?,status=?,completion_date=?,updated_at=? WHERE id=?',
                              (title,details,status,finished,stamp,pid))
                    c.execute('DELETE FROM experiment_plan_mice WHERE plan_id=?',(pid,))
            else:
                pid=c.execute("""INSERT INTO experiment_plans(id,title,plan_type,plan_date,plan_time,details,status,
                    source,created_at,updated_at,completion_date) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (self._next_id(c),title,first['plan_type'],first['plan_date'],first['plan_time'],details,status,first['source'],stamp,stamp,finished)).lastrowid
            used.add(pid)
            for mid in mids:c.execute('INSERT OR IGNORE INTO experiment_plan_mice VALUES(?,?)',(pid,mid))
            for p in items:
                assigned[p['source_key']]=pid
                c.execute('INSERT OR REPLACE INTO experiment_plan_sources VALUES(?,?)',(p['source_key'],pid))
        for pid,old in old_rows.items():
            if old['source']=='user' or pid in used:continue
            original_keys=[k for k,v in aliases.items() if v==pid]
            remaining=c.execute('SELECT source_key FROM experiment_plan_sources WHERE plan_id=?',(pid,)).fetchall()
            targets=[assigned[k] for k in original_keys if k in assigned]
            if targets and not remaining:self._archive_merge(c,old,targets[0])
            elif old['status']=='pending' and not any(r['source_key'] in candidates for r in remaining):
                c.execute("UPDATE experiment_plans SET status='cancelled',updated_at=? WHERE id=?",(stamp,pid))
        self._merge_matching(c)

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
        data=self.list();items=[]
        for p in data['plans']:
            if p['status']=='pending' and p['plan_date'] in (str(today),str(tomorrow)):
                items.append({**p,'ids':[p['id']],'when':'today' if p['plan_date']==str(today) else 'tomorrow'})
        return {'today':str(today),'tomorrow':str(tomorrow),'items':items}

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
            self._sync(c)
            if plan_id:plan_id=self._resolve_id(c,plan_id)
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
                plan_id=c.execute('''INSERT INTO experiment_plans(id,title,plan_type,plan_date,plan_time,details,status,
                    source,created_at,updated_at,completion_date) VALUES(?,?,?,?,?,?,?,'user',?,?,?)''',
                    (self._next_id(c),title,kind,day,time,details,status,stamp,stamp,finished)).lastrowid
            for mid in mids:c.execute('INSERT INTO experiment_plan_mice(plan_id,mouse_id) VALUES(?,?)',(plan_id,mid))
            if status=='completed' and (not old or old['status']!='completed'):
                for mid in mids:
                    self.db.audit(c,'plan_completed','mouse',mid,'已确认完成计划：'+title+'；计划ID='+str(plan_id))
            self.db.audit(c,'update' if old else 'create','experiment_plan',plan_id,title+'；'+day+'；'+status)
            self._merge_matching(c)
            plan_id=self._resolve_id(c,plan_id)
            return next(p for p in self._rows(c) if p['id']==plan_id)
