"""若本机有 Edge，则用真实浏览器执行 JavaScript；其他系统自动跳过。"""
import subprocess,tempfile,threading,unittest
from pathlib import Path
import app
from db import Database

EDGE=Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")

@unittest.skipUnless(EDGE.is_file(),"本机没有 Microsoft Edge，跳过真实浏览器检查")
class BrowserSmokeTest(unittest.TestCase):
    def test_14_dashboard_javascript_executes(self):
        with tempfile.TemporaryDirectory() as tmp:
            app.DB=Database(Path(tmp)/"browser.sqlite3");app.DB.initialize();server=app.create_server(0)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                url=f"http://127.0.0.1:{server.server_address[1]}"
                run=subprocess.run([str(EDGE),"--headless=new","--disable-gpu","--no-first-run",
                    f"--user-data-dir={Path(tmp)/'edge-profile'}","--virtual-time-budget=3000","--dump-dom",url],
                    capture_output=True,timeout=30,check=True)
                html=run.stdout.decode("utf-8",errors="replace")
                self.assertIn("当前在笼小鼠",html);self.assertNotIn("正在载入摘要",html)
            finally:
                server.shutdown();server.server_close();thread.join()

if __name__=="__main__":unittest.main()
