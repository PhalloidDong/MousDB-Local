"""鼠房库存的受控整体替换。

输入：已核对的房间名、旧笼/鼠ID、架位revision，以及新笼、鼠和待鉴定记录。
输出：原库存本机归档ID及新笼/鼠ID。所有写入共用一个事务，失败自动恢复原库存。
此操作只经Codex显式调用；不把数据发送到任何网络服务。
"""
import json
from db import ValidationError,now_text
from rack_layout import RackLayoutStore,STATES

class RoomInventoryStore:
    def __init__(self,db):self.db=db

    @staticmethod
    def _where(values):
        """把数据库读出的ID列表转成占位符；空集合用不存在的0避免特殊SQL。"""
        ids=list(values) or [0]
        return ','.join('?' for _ in ids),ids

    def _guard_references(self,c,mouse_ids,cage_ids):
        """不改其他动物的谱系/移动，也不静默破坏已有繁育和实验计划。

        若旧库存已有这些关系，需先按用户真实意图处理，不能推定死亡或转出。
        """
        m,mi=self._where(mouse_ids);g,gi=self._where(cage_ids)
        external=c.execute(f'SELECT 1 FROM mice WHERE id NOT IN ({m}) AND (father_id IN ({m}) OR mother_id IN ({m})) LIMIT 1',mi+mi+mi).fetchone()
        if external:raise ValidationError('旧鼠是其他鼠的亲本，不能整体删除；请先核对谱系')
        mating=c.execute(f'SELECT 1 FROM matings WHERE male_id IN ({m}) OR cage_id IN ({g}) LIMIT 1',mi+gi).fetchone()
        female=c.execute(f'SELECT 1 FROM mating_females WHERE female_id IN ({m}) LIMIT 1',mi).fetchone()
        litter=c.execute(f'SELECT 1 FROM litters WHERE mother_id IN ({m}) LIMIT 1',mi).fetchone()
        if mating or female or litter:raise ValidationError('旧库存关联已有繁育记录，需先处理原繁育关系')
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='experiment_plan_mice'").fetchone():
            if c.execute(f'SELECT 1 FROM experiment_plan_mice WHERE mouse_id IN ({m}) LIMIT 1',mi).fetchone():
                raise ValidationError('旧库存关联实验计划，需先处理原计划')
        movement=c.execute(f'SELECT 1 FROM mouse_movements WHERE mouse_id NOT IN ({m}) AND (from_cage_id IN ({g}) OR to_cage_id IN ({g})) LIMIT 1',mi+gi+gi).fetchone()
        if movement:raise ValidationError('原笼在其他小鼠的移笼历史中，不能整体删除')

    def replace(self,room_id,payload):
        """只替换指定房间：先归档，创建新记录，再删除旧记录，确保内部ID不被复用。

        未知幼鼠数量/性别/母鼠保持未知，只保存笼级标记。父母同笼本身不足以推定
        配鼠日期或具体窝的母鼠，因此此入口不创建虚构的matings/litters记录。
        """
        if not isinstance(payload,dict):raise ValidationError('库存资料必须为对象')
        reason=str(payload.get('reason') or '').strip();cages=payload.get('cages')
        if not reason:raise ValidationError('整体替换必须填写原因')
        if not isinstance(cages,list) or not cages:raise ValidationError('新库存必须包含明确的笼清单')
        for name in ('expected_cage_ids','expected_mouse_ids'):
            if not isinstance(payload.get(name),list):raise ValidationError('请先读取并核对原库存ID')
        layout=RackLayoutStore(self.db)
        with self.db.write() as c:
            room=c.execute('SELECT * FROM rooms WHERE id=?',(room_id,)).fetchone()
            if not room or payload.get('expected_room_name')!=room['name']:raise ValidationError('房间名称与目标不符')
            racks=[dict(r) for r in c.execute('SELECT * FROM racks WHERE room_id=?',(room_id,))]
            rack_ids={r['id'] for r in racks};q,ri=self._where(rack_ids)
            old_cages=[dict(r) for r in c.execute(f'SELECT * FROM cages WHERE rack_id IN ({q}) ORDER BY id',ri)]
            cage_ids=[r['id'] for r in old_cages];qg,gi=self._where(cage_ids)
            old_mice=[dict(r) for r in c.execute(f'SELECT * FROM mice WHERE cage_id IN ({qg}) ORDER BY id',gi)]
            mouse_ids=[r['id'] for r in old_mice];qm,mi=self._where(mouse_ids)
            revisions={str(r['rack_id']):r['revision'] for r in c.execute(f'SELECT * FROM rack_layouts WHERE rack_id IN ({q})',ri)}
            if sorted(payload['expected_cage_ids'])!=cage_ids or sorted(payload['expected_mouse_ids'])!=mouse_ids or payload.get('expected_rack_revisions')!=revisions:
                raise ValidationError('房间库存或位置已变化，请重新核对后再替换')
            slots=set();names=set();ears=set()
            for entry in cages:
                if entry.get('rack_id') not in rack_ids:raise ValidationError('新笼不属于指定房间')
                row,col=layout._slot(entry)
                if row is None:raise ValidationError('整体重建必须明确每笼物理坐标')
                slot=(entry['rack_id'],row,col);name=(entry['rack_id'],str(entry.get('code') or '').strip().casefold())
                if slot in slots or name in names:raise ValidationError('新库存有重复笼名或位置')
                slots.add(slot);names.add(name)
                state=entry.get('card_state')
                if state is not None and state not in STATES:raise ValidationError('笼状态无效')
                for mouse in entry.get('mice',[]):
                    ear=str(mouse.get('ear_tag') or '').strip().casefold()
                    if not ear or ear in ears:raise ValidationError('此次逐鼠清单需要唯一且非空的耳号')
                    ears.add(ear)
                if state=='empty' and entry.get('mice'):raise ValidationError('有鼠的笼不能标记空笼')
            self._guard_references(c,mouse_ids,cage_ids)
            before_positions=[dict(r) for r in c.execute(f'SELECT * FROM cage_positions WHERE cage_id IN ({qg})',gi)]
            snapshot={'room':dict(room),'racks':racks,'cages':old_cages,'mice':old_mice,'positions':before_positions,
                      'movements':[dict(r) for r in c.execute(f'SELECT * FROM mouse_movements WHERE mouse_id IN ({qm})',mi)],
                      'events':[dict(r) for r in c.execute(f'SELECT * FROM mouse_events WHERE mouse_id IN ({qm})',mi)],
                      'genotyping':[dict(r) for r in c.execute(f'SELECT * FROM cage_genotyping WHERE cage_id IN ({qg})',gi)]}
            archive=c.execute('INSERT INTO room_inventory_replacements(room_id,reason,snapshot_json,created_at) VALUES(?,?,?,?)',
                              (room_id,reason,json.dumps(snapshot,ensure_ascii=False),now_text())).lastrowid
            c.execute(f'DELETE FROM cage_positions WHERE cage_id IN ({qg})',gi)
            # 原名临时让给新清单，事务未提交前其他客户端仍只看到完整原库存。
            for old in old_cages:c.execute('UPDATE cages SET code=? WHERE id=?',(f'__replaced_{archive}_{old["id"]}',old['id']))
            made=[]
            for entry in cages:
                fresh=self.db.create_cage(entry,c,numbering_prefix=payload.get('numbering_prefix','M'))
                c.execute('INSERT INTO cage_positions VALUES(?,?,?,?,?)',
                          (fresh['id'],entry['rack_id'],entry['row'],entry['column'],entry.get('card_state')))
                made.append({'cage_id':fresh['id'],'code':fresh['code'],'rack_id':entry['rack_id'],
                             'row':entry['row'],'column':entry['column'],'mouse_ids':[m['id'] for m in fresh['created_mice']]})
            # 新鼠和笼已经获得更大的ID，此时删除旧库存不会让新个体继承旧历史标识。
            remaining=set(mouse_ids)
            while remaining:
                deleted=[]
                for mid in sorted(remaining):
                    if not c.execute('SELECT 1 FROM mice WHERE father_id=? OR mother_id=?',(mid,mid)).fetchone():
                        c.execute('DELETE FROM mice WHERE id=?',(mid,));deleted.append(mid)
                if not deleted:raise ValidationError('旧库存谱系有循环，整体替换已回滚')
                remaining.difference_update(deleted)
            c.execute(f'DELETE FROM cages WHERE id IN ({qg})',gi)
            for rack_id in rack_ids:
                after=[dict(r) for r in c.execute('SELECT * FROM cage_positions WHERE rack_id=?',(rack_id,))]
                layout._record(c,rack_id,'replace_inventory',[p for p in before_positions if p['rack_id']==rack_id],after)
            self.db.audit(c,'replace_inventory','room',room_id,reason+'；本机归档ID='+str(archive))
            return {'room_id':room_id,'room_name':room['name'],'archive_id':archive,'removed_mice':len(old_mice),
                    'removed_cages':len(old_cages),'created_mice':sum(len(x['mouse_ids']) for x in made),'created_cages':made}
