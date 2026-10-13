"""克隆后启用本地提交/推送检查，记录当前Python路径于私有Git配置。"""
import subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
subprocess.run(['git','config','core.hooksPath','.githooks'],cwd=root,check=True)
subprocess.run(['git','config','mousdb.python',sys.executable],cwd=root,check=True)
print('本地Git隐私检查已启用。Python路径仅保存于.git/config，不会提交。')
