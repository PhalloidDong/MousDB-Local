"""用Node标准库执行独立主题行为测试；不依赖前端框架或真实鼠数据。"""
import shutil,subprocess,unittest
from pathlib import Path

class ThemeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'未安装Node，跳过主题JavaScript行为测试')
    def test_theme_preference_system_storage_and_first_paint(self):
        """真实执行主题脚本，核验首屏、持久化、系统变化、跨标签和存储异常。"""
        run=subprocess.run([shutil.which('node'),str(Path(__file__).with_name('theme_behavior.js'))],capture_output=True,text=True,encoding='utf-8',timeout=15)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)

if __name__=='__main__':unittest.main()
