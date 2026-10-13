"""MousDB Local 本地 HTTP 服务。

它只监听 127.0.0.1，不接受局域网或互联网连接。浏览器请求经过本文件路由后，
交给 db.py 执行业务规则，再统一返回 {ok, data} 或 {ok, error} JSON。
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import socket
import sys
import threading
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from db import Database, ValidationError
from rack_layout import RackLayoutStore, LayoutConflict
from experiment_plans import PlanStore
from runtime_paths import storage_paths


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR, DB_PATH = storage_paths()
DB = Database(DB_PATH)


class AppHandler(BaseHTTPRequestHandler):
    server_version = "MousDBLocal/1.5.1"

    def log_message(self, fmt: str, *args: object) -> None:
        # 控制台日志不写文件，避免不断积累含实验编号的日志副本。
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def _json(self, status: int, data=None, error: str | None = None) -> None:
        body = json.dumps({"ok": error is None, "data": data, "error": error}, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(body)

    def _binary(self, body: bytes, content_type: str, filename: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f"attachment; filename={filename}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def _payload(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 10 * 1024 * 1024:
            raise ValidationError("请求内容超过 10 MB 限制")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("请求必须是有效的 UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise ValidationError("请求 JSON 顶层必须是对象")
        return value

    def _handle_error(self, exc: Exception) -> None:
        if isinstance(exc, LayoutConflict):
            self._json(HTTPStatus.CONFLICT, error=str(exc))
        elif isinstance(exc, ValidationError):
            self._json(HTTPStatus.BAD_REQUEST, error=str(exc))
        else:
            print(f"内部错误：{type(exc).__name__}: {exc}")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, error="服务器处理失败，请查看启动窗口中的错误信息")

    def do_GET(self) -> None:  # noqa: N802 - http.server 规定的方法名
        try:
            parsed = urlparse(self.path); path = parsed.path; query = parse_qs(parsed.query)
            if path == "/api/health":
                return self._json(200, {"status": "ok", "version": "1.5.1", "schema": DB.integrity_check(), "database_id": str(DB_PATH.resolve()), "process_id": os.getpid()})
            if path == "/api/bootstrap":
                return self._json(200, {"references": DB.list_references(), "next_code": DB.next_code()})
            # 明确排程独立保存；站内提醒只显示今天/明天的未完成计划。
            if path == "/api/plans/reminders": return self._json(200,PlanStore(DB).reminders())
            if path == "/api/plans": return self._json(200,PlanStore(DB).list())
            if path == "/api/dashboard": return self._json(200, DB.dashboard())
            if path == "/api/mice/next-code": return self._json(200, {"code": DB.next_code(), "readonly": True})
            if path == "/api/mice":
                one=lambda key: query.get(key,[""])[0]
                return self._json(200, DB.list_mice(one("scope") or "alive", one("search"),
                    int(one("strain_id")) if one("strain_id").isdigit() else None,
                    int(one("line_id")) if one("line_id").isdigit() else None, one("status")))
            match=re.fullmatch(r"/api/mice/(\d+)",path)
            if match: return self._json(200, DB.get_mouse(int(match.group(1))))
            # 真实架子首页：位置与动物所属cage分开保存。
            if path == "/api/rack-layouts": return self._json(200,{"racks":RackLayoutStore(DB).list_racks()})
            rack_match=re.fullmatch(r"/api/rack-layouts/(\d+)",path)
            if rack_match: return self._json(200,RackLayoutStore(DB).board(int(rack_match.group(1))))
            if path == "/api/cages": return self._json(200, DB.list_cages())
            match=re.fullmatch(r"/api/cages/(\d+)",path)
            if match: return self._json(200, DB.get_cage(int(match.group(1))))
            if path == "/api/matings": return self._json(200, DB.list_matings())
            if path == "/api/audit": return self._json(200, DB.audit_rows(int(query.get("limit",["100"])[0])))
            if path == "/api/import/template":
                text="\ufeff耳标,性别,出生日期,房间,架位,笼号,strain,line,genotype,来源,负责人,项目,备注\r\n"
                return self._binary(text.encode("utf-8"),"text/csv; charset=utf-8","mousdb_import_template.csv")
            if path == "/api/export/mice.csv":
                scope=query.get("scope",["all"])[0]
                if scope not in ("all","alive","dead"): raise ValidationError("导出 scope 无效")
                return self._binary(DB.export_csv(scope),"text/csv; charset=utf-8",f"mousdb_{scope}.csv")
            if path == "/api/backup/download":
                stamp=datetime.now().strftime("%Y%m%d_%H%M%S")
                dest=DATA_DIR/"backups"/f"mousdb_backup_{stamp}.sqlite3"
                DB.backup_to(dest)
                return self._binary(dest.read_bytes(),"application/vnd.sqlite3",dest.name)
            if path.startswith("/api/"):
                return self._json(404,error="API 路径不存在")
            return self._static(path)
        except Exception as exc:
            self._handle_error(exc)

    def _static(self, path: str) -> None:
        relative="index.html" if path in ("/","") else path.lstrip("/")
        # resolve 后再校验父目录，可阻止 /../ 读取项目外文件。
        target=(STATIC_DIR/relative).resolve()
        if STATIC_DIR.resolve() not in target.parents and target != STATIC_DIR.resolve():
            return self._json(403,error="禁止访问该路径")
        if not target.is_file():
            target=STATIC_DIR/"index.html"
        body=target.read_bytes(); mime=mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200); self.send_header("Content-Type",mime+("; charset=utf-8" if mime.startswith("text/") else ""))
        self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        try:
            path=urlparse(self.path).path; payload=self._payload()
            # 建笼和定位一并提交；跨格移动/交换/撤销都由服务器验证。
            if path == "/api/rack-layouts": return self._json(201,RackLayoutStore(DB).save_rack(payload))
            rack_match=re.fullmatch(r"/api/rack-layouts/(\d+)/(move|undo|cages)",path)
            if rack_match:
                rack_id=int(rack_match.group(1));action=rack_match.group(2);store=RackLayoutStore(DB)
                result=store.move(rack_id,payload) if action=="move" else store.undo(rack_id,payload) if action=="undo" else store.create_slot_cage(rack_id,payload)
                return self._json(201 if action=="cages" else 200,result)
            if path == "/api/plans": return self._json(201,PlanStore(DB).save(payload))
            if path == "/api/mice": return self._json(201,DB.create_mouse(payload))
            match=re.fullmatch(r"/api/mice/(\d+)/events",path)
            if match: return self._json(201,DB.add_event(int(match.group(1)),payload))
            if path == "/api/cages": return self._json(201,DB.create_cage(payload))
            if path == "/api/racks": return self._json(201,DB.create_rack(payload))
            match=re.fullmatch(r"/api/references/(rooms|strains|lines|genotypes)",path)
            if match: return self._json(201,DB.create_reference(match.group(1),payload))
            if path == "/api/matings": return self._json(201,DB.create_mating(payload))
            match=re.fullmatch(r"/api/matings/(\d+)/litters",path)
            if match: return self._json(201,DB.create_litter(int(match.group(1)),payload))
            match=re.fullmatch(r"/api/litters/(\d+)/wean",path)
            if match: return self._json(201,DB.wean_litter(int(match.group(1)),payload))
            if path in ("/api/import/preview","/api/import/commit"):
                method="preview_import" if path.endswith("preview") else "commit_import"
                if not hasattr(DB,method): raise ValidationError("导入模块尚未初始化")
                return self._json(200,getattr(DB,method)(payload))
            return self._json(404,error="API 路径不存在")
        except Exception as exc: self._handle_error(exc)

    def do_PUT(self) -> None:  # noqa: N802
        try:
            path=urlparse(self.path).path; payload=self._payload()
            rack_match=re.fullmatch(r"/api/rack-layouts/(\d+)",path)
            if rack_match: return self._json(200,RackLayoutStore(DB).save_rack(payload,int(rack_match.group(1))))
            card_match=re.fullmatch(r"/api/rack-layouts/(\d+)/cards/(\d+)",path)
            if card_match: return self._json(200,RackLayoutStore(DB).card_state(int(card_match.group(1)),int(card_match.group(2)),payload))
            plan_match=re.fullmatch(r"/api/plans/(\d+)",path)
            if plan_match: return self._json(200,PlanStore(DB).save(payload,int(plan_match.group(1))))
            match=re.fullmatch(r"/api/mice/(\d+)",path)
            if match: return self._json(200,DB.update_mouse(int(match.group(1)),payload))
            match=re.fullmatch(r"/api/cages/(\d+)",path)
            if match: return self._json(200,DB.update_cage(int(match.group(1)),payload))
            match=re.fullmatch(r"/api/references/(rooms|strains|lines|genotypes)/(\d+)",path)
            if match: return self._json(200,DB.update_reference(match.group(1),int(match.group(2)),payload))
            return self._json(404,error="API 路径不存在")
        except Exception as exc: self._handle_error(exc)


def find_port(preferred: int = 8765) -> int:
    for port in range(preferred, preferred + 50):
        with socket.socket() as sock:
            try: sock.bind(("127.0.0.1",port))
            except OSError: continue
            return port
    raise RuntimeError("8765-8814 端口均被占用")


def create_server(port: int | None = None) -> ThreadingHTTPServer:
    DB.initialize()
    RackLayoutStore(DB).initialize()
    PlanStore(DB).initialize()
    return ThreadingHTTPServer(("127.0.0.1",port or find_port()),AppHandler)


def main() -> None:
    # 后台日志固定UTF-8，英语Windows/macOS地区设置不能影响中文启动信息。
    for stream in (sys.stdout,sys.stderr):
        if stream is not None:stream.reconfigure(encoding="utf-8",errors="backslashreplace")
    if sys.version_info < (3,10):
        print("MousDB Local 需要 Python 3.10 或更高版本。")
        input("按 Enter 退出……"); raise SystemExit(2)
    server=create_server(); url=f"http://127.0.0.1:{server.server_address[1]}"
    print(f"MousDB Local v1.5.1 已启动：{url}")
    print(f"数据库：{DB_PATH}")
    # 服务地址仅写本机数据目录，桌面启动器及Codex CLI据此定位同一实例。
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    (DATA_DIR/"service.json").write_text(json.dumps({"port":server.server_address[1],"pid":os.getpid()},ensure_ascii=False),encoding="utf-8")
    print("关闭此窗口或按 Ctrl+C 即可停止；已保存的数据不会丢失。")
    if os.environ.get("MOUSDB_NO_BROWSER") != "1":
        threading.Timer(0.8,lambda:webbrowser.open(url)).start()
    try: server.serve_forever()
    except KeyboardInterrupt: print("\n正在安全停止……")
    finally: server.server_close()


if __name__ == "__main__": main()
