import json, tempfile, threading, unittest
from pathlib import Path
from urllib.request import Request, urlopen
import app
from db import Database

class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); app.DB=Database(Path(self.tmp.name)/"http.sqlite3"); app.DB.initialize()
        self.server=app.create_server(0); self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.base=f"http://127.0.0.1:{self.server.server_address[1]}"
    def tearDown(self): self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()
    def get(self,path): return urlopen(self.base+path,timeout=5)
    def send(self,path,method,body):
        req=Request(self.base+path,method=method,data=json.dumps(body).encode(),headers={"Content-Type":"application/json"})
        return json.loads(urlopen(req,timeout=5).read())["data"]
    def test_10_static_health_and_mouse_flow(self):
        self.assertIn(b"MousDB Local",self.get("/").read()); health=json.loads(self.get("/api/health").read());self.assertTrue(health["ok"])
        mouse=self.send("/api/mice","POST",{"code":"BAD","sex":"M"});self.assertEqual(mouse["code"],"M0001")
        changed=self.send(f"/api/mice/{mouse['id']}","PUT",{"code":"BAD2","status":"Dead","death_date":"2026-01-01"});self.assertEqual(changed["code"],"M0001")
        alive=json.loads(self.get("/api/mice?scope=alive").read())["data"];dead=json.loads(self.get("/api/mice?scope=dead").read())["data"]
        self.assertEqual(len(alive),0);self.assertEqual(len(dead),1)

if __name__=="__main__": unittest.main()

