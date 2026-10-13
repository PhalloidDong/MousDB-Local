"""公开版启动器：Windows/macOS/Linux使用同一Python标准库代码。
输入：本地数据库配置及--check/--no-browser选项；输出：后台服务和浏览器首页。
锁文件及启动日志位于私有数据目录，不进入仓库；已有服务直接复用。
"""
import argparse,json,os,signal,socket,subprocess,sys,time,urllib.request,webbrowser
from pathlib import Path
from runtime_paths import ROOT,storage_paths

def health(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health',timeout=2) as r:
            data=json.load(r)
            return data['data'] if data.get('ok') and 'MousDBLocal' in r.headers.get('Server','') else None
    except Exception:return None

def start(open_browser=True):
    """选定健康服务或空端口；只绑定loopback，服务文件由数据目录记录。"""
    data_dir,database=storage_paths();data_dir.mkdir(parents=True,exist_ok=True)
    state=data_dir/'service.json';port=None
    if state.exists():
        try:
            candidate=int(json.loads(state.read_text())['port'])
            info=health(candidate)
            # 身份匹配必须包含同一数据库标识，避免复用另一套本机实例。
            if info and info.get('database_id')==str(database.resolve()):port=candidate
        except (ValueError,KeyError,OSError):pass
    if port is None:
        # 独占创建启动锁，连续双击不会同时创建服务器。异常退出锁超过60秒后可恢复。
        lock=data_dir/'startup.lock';deadline=time.monotonic()+35
        while True:
            try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY);os.close(fd);break
            except FileExistsError:
                # 另一个启动器可能刚释放锁；文件消失时重新尝试，不报错。
                try:
                    if time.time()-lock.stat().st_mtime>60:lock.unlink(missing_ok=True);continue
                except FileNotFoundError:continue
                if time.monotonic()>deadline:raise RuntimeError('启动过程仍在运行，请稍后重试')
                time.sleep(.3)
        try:
            if state.exists():
                try:
                    candidate=int(json.loads(state.read_text())['port']);info=health(candidate)
                    if info and info.get('database_id')==str(database.resolve()):port=candidate
                except (ValueError,KeyError,OSError):pass
            if port is None:
                env={**os.environ,'MOUSDB_DB_PATH':str(database),'MOUSDB_DATA_DIR':str(data_dir),'MOUSDB_NO_BROWSER':'1'}
                python=Path(sys.executable)
                if sys.platform=='win32' and python.with_name('pythonw.exe').exists():python=python.with_name('pythonw.exe')
                options={'creationflags':subprocess.CREATE_NO_WINDOW} if sys.platform=='win32' else {'start_new_session':True}
                with (data_dir/'service-out.log').open('ab') as out,(data_dir/'service-error.log').open('ab') as err:
                    child=subprocess.Popen([str(python),str(ROOT/'app.py')],cwd=str(ROOT),env=env,stdout=out,stderr=err,**options)
                deadline=time.monotonic()+30
                while time.monotonic()<deadline:
                    if child.poll() is not None:
                        diagnostic=(data_dir/'service-error.log').read_text(encoding='utf-8',errors='replace')[-3000:]
                        raise RuntimeError('后台进程退出：'+diagnostic)
                    if state.exists():
                        try:
                            candidate=int(json.loads(state.read_text())['port']);info=health(candidate)
                            if info and info.get('database_id')==str(database.resolve()):port=candidate;break
                        except (ValueError,KeyError,OSError):pass
                    time.sleep(.3)
                if port is None:raise RuntimeError('启动超时，请查看本地数据目录中的service-error.log')
        finally:lock.unlink(missing_ok=True)
    url=f'http://127.0.0.1:{port}/'
    if open_browser:webbrowser.open(url,new=2)
    return url

def stop():
    """只停止配置指向且PID经健康接口核对的MousDB进程，不删除任何数据。"""
    data_dir,database=storage_paths();state=data_dir/'service.json'
    if not state.exists():return False
    value=json.loads(state.read_text(encoding='utf-8'));info=health(int(value['port']))
    if not info:
        state.unlink(missing_ok=True);return False
    if info.get('database_id')!=str(database.resolve()) or info.get('process_id')!=value['pid']:
        raise RuntimeError('服务标识不一致，拒绝停止其他进程')
    os.kill(int(value['pid']),signal.SIGTERM)
    deadline=time.monotonic()+15
    while health(int(value['port'])):
        if time.monotonic()>deadline:raise RuntimeError('服务未停止，请核对本机进程')
        time.sleep(.3)
    state.unlink(missing_ok=True);return True

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='后台启动本机MousDB')
    parser.add_argument('--check',action='store_true',help='启动/核对服务但不打开浏览器')
    parser.add_argument('--stop',action='store_true',help='停止自己的后台服务，数据保留')
    parser.add_argument('--restart',action='store_true',help='更新代码后重启自己的服务')
    args=parser.parse_args()
    try:
        if args.stop:result={'ok':True,'stopped':stop()}
        else:
            if args.restart:stop()
            result={'ok':True,'url':start(not args.check)}
        if sys.stdout is not None:print(json.dumps(result,ensure_ascii=False))
    except Exception as error:
        print(str(error),file=sys.stderr)
        if sys.platform=='win32' and 'pythonw' in Path(sys.executable).stem:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,str(error),'MousDB启动失败',16)
        raise SystemExit(1)
