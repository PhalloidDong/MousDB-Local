"""跨平台本地路径。
输入：可选环境变量或被Git忽略的.mousdb-local.json；输出：数据库/数据目录。
默认数据存于用户目录且位于Git仓库之外，更新代码不会替换小鼠数据库。
显式配置优先，旧版repo/data数据库存在时沿用，避免升级生成另一个空库。
"""
import json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def configuration():
    path=ROOT/'.mousdb-local.json'
    if not path.exists():return {}
    config=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(config,dict):raise ValueError('本地配置必须是JSON对象')
    return config

def default_data_dir():
    if sys.platform=='win32':return Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData/Local')))/'MousDB'
    if sys.platform=='darwin':return Path.home()/'Library/Application Support/MousDB'
    return Path(os.environ.get('XDG_DATA_HOME',str(Path.home()/'.local/share')))/'MousDB'

def storage_paths():
    """返回(data_dir,db_path)；只计算路径，不创建或复制数据库。"""
    config=configuration()
    explicit=os.environ.get('MOUSDB_DB_PATH') or config.get('database')
    directory=os.environ.get('MOUSDB_DATA_DIR') or config.get('data_dir')
    if explicit:
        database=Path(explicit).expanduser().resolve()
        return Path(directory).expanduser().resolve() if directory else database.parent,database
    if directory:
        base=Path(directory).expanduser().resolve();return base,base/'mousdb.sqlite3'
    legacy=ROOT/'data/mousdb.sqlite3'
    if legacy.exists():return legacy.parent,legacy
    base=default_data_dir();return base,base/'mousdb.sqlite3'
