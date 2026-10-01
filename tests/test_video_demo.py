import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class VideoDemoContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = (ROOT / "static" / "video-demo.js").read_text(encoding="utf-8")
        cls.page = (ROOT / "static" / "video-demo.html").read_text(encoding="utf-8")
        cls.styles = (ROOT / "static" / "video-demo.css").read_text(encoding="utf-8")

    def test_recording_page_has_all_eight_scenes(self):
        expected = [
            "入口", "连续陪伴", "个性化回答", "家长卡片",
            "记忆组织", "策略进化", "安全与审计", "三阶段路线",
        ]
        for label in expected:
            self.assertIn(label, self.script)
        self.assertIn("?scene=", self.script)

    def test_recording_case_matches_scripted_evidence(self):
        expected = [
            "影子为什么下午会变长？",
            "手电筒放低",
            "Constructor",
            "Retriever",
            "Judge",
            "Refresher",
            "37 次复用",
            "31% → 11%",
            "这不是心理诊断或性格标签",
            "不是基础大模型自行完成参数训练",
        ]
        for text in expected:
            self.assertIn(text, self.script)

    def test_recording_page_is_static_and_does_not_touch_product_data(self):
        forbidden = [
            "fetch(",
            "localStorage",
            "sessionStorage",
            "/api/chat",
            "/api/data/delete",
            "demo-db.json",
        ]
        for marker in forbidden:
            self.assertNotIn(marker, self.script)
        self.assertIn("不写入正式数据库", self.script)
        self.assertIn("本录制页不会触碰任何真实数据", self.script)

    def test_recording_assets_are_wired(self):
        self.assertIn('/static/video-demo.css?v=video-v1', self.page)
        self.assertIn('/static/video-demo.js?v=video-v1', self.page)
        self.assertIn(".recording-dock", self.styles)
        self.assertIn(".record-scene", self.styles)

    def test_product_does_not_promote_prerecorded_showcase(self):
        product_script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        server = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertNotIn('href="/video-demo"', product_script)
        self.assertIn("a.hasAttribute('data-native')", product_script)
        self.assertIn('parsed.path == "/video-demo"', server)
        self.assertIn('STATIC_DIR / "video-demo.html"', server)


if __name__ == "__main__":
    unittest.main()
