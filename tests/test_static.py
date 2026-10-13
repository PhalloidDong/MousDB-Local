"""检查 HTML/JavaScript 约定，提前发现拼错 id 或取消按钮误提交。"""
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class IdParser(HTMLParser):
    def __init__(self): super().__init__();self.ids=[];self.cancel_types=[]
    def handle_starttag(self,tag,attrs):
        data=dict(attrs)
        if "id" in data:self.ids.append(data["id"])
        if tag=="button" and ("cancel" in data.get("class","") or "dialog-close" in data.get("class","")):
            self.cancel_types.append(data.get("type",""))

class StaticTests(unittest.TestCase):
    def test_13_unique_ids_queries_exist_and_cancel_never_submits(self):
        html=(ROOT/"static"/"index.html").read_text(encoding="utf-8");js=(ROOT/"static"/"app.js").read_text(encoding="utf-8")
        parser=IdParser();parser.feed(html);self.assertEqual(len(parser.ids),len(set(parser.ids)),"HTML id 必须唯一")
        # 一些弹窗按钮由 app.js 动态生成，所以同时收集 JS 模板中的 id="..."。
        dynamic_ids=set(re.findall(r'id=["\']([A-Za-z0-9_-]+)["\']',js))
        literal_queries=set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)',js));missing=literal_queries-(set(parser.ids)|dynamic_ids)
        self.assertEqual(missing,set(),f"JavaScript 查询了不存在的元素：{missing}")
        self.assertTrue(parser.cancel_types);self.assertTrue(all(x=="button" for x in parser.cancel_types))

    def test_14_rich_picker_controls_and_shortcuts_are_present(self):
        js=(ROOT/"static"/"app.js").read_text(encoding="utf-8")
        css=(ROOT/"static"/"styles.css").read_text(encoding="utf-8")
        for marker in ("data-rich-picker", "data-rich-command=\"superscript\"", "data-rich-command=\"subscript\"", "data-rich-command=\"italic\"", "data-rich-symbol-toggle", "Ctrl/Cmd+Shift+=", "Ctrl/Cmd+="):
            self.assertIn(marker, js)
        for marker in (".rich-editor", ".rich-suggestions", ".rich-symbol-menu"):
            self.assertIn(marker, css)

    def test_15_windows_ui_labels_auto_hide_and_relation_matchers_are_present(self):
        html=(ROOT/"static"/"index.html").read_text(encoding="utf-8")
        js=(ROOT/"static"/"app.js").read_text(encoding="utf-8")
        css=(ROOT/"static"/"styles.css").read_text(encoding="utf-8")
        self.assertIn('data-page="settings">品系和备份', html)
        for marker in ("closeRichSymbolMenus", "data-rich-symbol-menu", "pointerdown", "matchPicker(\"cages\",\"cage\"", "matchPicker(\"mice\",\"father\"", "matchPicker(\"mice\",\"mother\""):
            self.assertIn(marker, js)
        self.assertIn(".rich-symbol-menu[hidden]{display:none!important}", css)
        self.assertIn(".match-suggestions[hidden]{display:none!important}", css)

if __name__=="__main__":unittest.main()
