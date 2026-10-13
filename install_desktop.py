"""为当前用户安装桌面入口；Windows写lnk，Mac写可执行.command。
输入：当前Python和项目路径；输出：用户桌面上的启动入口。不需要管理员权限。
没有硬编码作者的Windows目录，别人克隆后可在自己的路径下运行。
"""
import argparse,json,os,shlex,subprocess,sys
from pathlib import Path
from runtime_paths import ROOT

def mac_shortcut(desktop,python,launcher):
    target=desktop/'MousDB.command'
    target.write_text('#!/bin/sh\n# 本地启动MousDB；路径由安装器填写。\nexec '+shlex.quote(str(python))+' '+shlex.quote(str(launcher))+'\n',encoding='utf-8')
    target.chmod(0o755);return target

def windows_shortcut(desktop,python,launcher):
    target=desktop/'MousDB.lnk'
    python=python.with_name('pythonw.exe') if python.with_name('pythonw.exe').exists() else python
    # PowerShell仅用于Windows官方COM快捷方式接口，启动网站本身不依赖PowerShell脚本。
    quote=lambda value:"'"+str(value).replace("'","''")+"'"
    script=f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({quote(target)});$s.TargetPath={quote(python)};$s.Arguments={quote(chr(34)+str(launcher)+chr(34))};$s.WorkingDirectory={quote(ROOT)};$s.IconLocation={quote(str(ROOT/'assets/app.ico')+',0')};$s.Description='MousDB Local';$s.Save()"
    subprocess.run(['powershell.exe','-NoProfile','-Command',script],check=True)
    return target

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--desktop',help='可选桌面目录，默认使用系统用户桌面')
    args=parser.parse_args()
    if args.desktop:desktop=Path(args.desktop).expanduser().resolve()
    elif sys.platform=='win32':
        location=subprocess.check_output(['powershell.exe','-NoProfile','-Command',"[Console]::OutputEncoding=[Text.UTF8Encoding]::new();[Environment]::GetFolderPath('Desktop')"],encoding='utf-8').strip()
        desktop=Path(location)
    else:desktop=Path.home()/'Desktop'
    desktop.mkdir(parents=True,exist_ok=True)
    if sys.platform=='win32':target=windows_shortcut(desktop,Path(sys.executable),ROOT/'launcher.py')
    elif sys.platform=='darwin':target=mac_shortcut(desktop,Path(sys.executable),ROOT/'launcher.py')
    else:raise SystemExit('桌面入口安装目前支持Windows和macOS；Linux可直接运行launcher.py')
    print(json.dumps({'shortcut':str(target)},ensure_ascii=False))
