"""公开仓库隐私检查：提交/推送/CI共用。
输入：Git暂存文件、已跟踪文件或历史blob；输出：通过或只含路径的阻止原因。
检查不打印文件内容，避免把检测到的小鼠数据/令牌再次写入日志。
默认数据目录已在仓库外；此脚本进一步阻止误放入仓库的SQLite、导出及私人配置。
"""
import argparse,json,re,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BLOCK_EXT={'.sqlite','.sqlite3','.db','.csv','.xlsx','.xls','.pdf','.docx','.pptx','.png','.jpg','.jpeg','.lnk','.log','.zip','.bak','.env'}
SAFE_TEXT={'.py','.js','.css','.html','.sql','.md','.txt','.yml','.yaml','.bat','.command','.sh','.svg'}
SAFE_NAMES={'LICENSE','.gitignore','.gitattributes','public_manifest.json'}
SECRET=re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{40,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')
PERSONAL_PATH=re.compile(rb'(?:[A-Za-z]:[\\/]Users[\\/]|/Users/)[A-Za-z0-9_.-]+')

def inspect(path,data):
    """仅输出违规类型；演示CSV/JSON只能包含DEMO耳号。"""
    normalized=path.replace('\\','/');parts=normalized.split('/');p=Path(normalized)
    if any(part in {'data','backups','exports','screenshots'} for part in parts):return '私有数据目录'
    if p.name in {'.mousdb-local.json','service.json','startup.lock'}:return '私有运行配置'
    if data.startswith(b'SQLite format 3\x00'):return 'SQLite数据库（包括改名文件）'
    if SECRET.search(data):return '疑似访问令牌或私钥'
    if PERSONAL_PATH.search(data):return '硬编码个人用户目录'
    if normalized=='examples/sample_import.csv':
        import csv,io
        rows=list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))
        return None if all(str(r.get('耳标','')).startswith('DEMO-') for r in rows) else '演示CSV含非DEMO耳号'
    if p.suffix=='.json':
        if normalized=='public_manifest.json':return None
        if normalized.startswith('examples/request_'):
            try:
                value=json.loads(data)
                def safe(v):
                    if isinstance(v,dict):return all((str(x).startswith('DEMO-') if k=='ear_tag' else safe(x)) for k,x in v.items())
                    if isinstance(v,list):return all(safe(x) for x in v)
                    return True
                return None if safe(value) else '请求示例含非DEMO耳号'
            except Exception:return 'JSON示例不可解析'
        return '未允许的JSON数据/回执文件'
    if p.suffix in BLOCK_EXT or '.sqlite' in p.name:return '数据库、导出、截图或备份文件'
    if normalized=='assets/app.ico':return None
    if p.suffix not in SAFE_TEXT and p.name not in SAFE_NAMES and not normalized.startswith('.githooks/'):
        return '未允许的文件类型'
    return None

def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--staged',action='store_true');parser.add_argument('--history',action='store_true');args=parser.parse_args()
    failed=[];count=0
    if args.history:
        # 检查全部提交历史，防止先提交数据、再删除数据后误以为可以安全推送。
        for line in git('rev-list','--objects','--all').decode('utf-8').splitlines():
            fields=line.split(' ',1)
            if len(fields)!=2:continue
            sha,path=fields
            if git('cat-file','-t',sha).strip()!=b'blob':continue
            reason=inspect(path,git('cat-file','blob',sha));count+=1
            if reason:failed.append((path,reason))
    else:
        command=('diff','--cached','--name-only','--diff-filter=ACMR','-z') if args.staged else ('ls-files','-z')
        for path in git(*command).decode('utf-8').split('\x00'):
            if not path:continue
            data=git('show',':'+path) if args.staged else (ROOT/path).read_bytes()
            reason=inspect(path,data);count+=1
            if reason:failed.append((path,reason))
    if failed:
        for path,reason in failed:print('禁止发布：'+path+'；'+reason,file=sys.stderr)
        return 1
    print(f'Privacy check PASS: {count} public source files checked; no private data detected.')
    return 0

if __name__=='__main__':raise SystemExit(main())
