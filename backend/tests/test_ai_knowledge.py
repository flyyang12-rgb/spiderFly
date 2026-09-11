from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

from app import ai_knowledge as knowledge


class KnowledgeTests(unittest.TestCase):
    def test_natural_chinese_and_api_terms_find_relevant_topics(self):
        cases = {
            "采集分页怎么避免重复数据": "pagination",
            "并发和断点续采怎么做": "concurrency",
            "Cookie登录态能给定时任务吗": "sessions",
            "Scrapling支持代理池吗": "proxies",
            "网页改版自适应选择器": "adaptive",
            "CSS怎么提取链接": "selectors",
            "怎么用Scrapling建立RAG知识库": "rag",
            "ModuleNotFoundError scrapling 安装": "http",
        }
        for query, topic in cases.items():
            with self.subTest(query=query):
                rows = knowledge.search(query)["documents"]
                self.assertIn(f"scrapling/{topic}.md", [row["name"] for row in rows[:3]])
                self.assertTrue(all(row["content"].strip() for row in rows))

    def test_empty_and_unknown_queries_do_not_return_arbitrary_documents(self):
        for query in ("", "请问怎么", "zzxnonexistent443xyz"):
            self.assertEqual(knowledge.search(query)["documents"], [])

    def test_legacy_excel_knowledge_remains_searchable(self):
        self.assertEqual(knowledge.search("Excel单元格格式")["documents"][0]["name"], "tables.md")

    def test_section_read_preserves_version_scope_and_pinned_source(self):
        result = knowledge.search("Cookie登录态能给定时任务吗")["documents"]
        match = next(row for row in result if row["name"] == "scrapling/sessions.md")
        full = knowledge.read(match["name"], match["section"])
        self.assertEqual(full["content"], match["content"])
        self.assertIn("0.4.15", full["version"])
        self.assertEqual(full["verified_at"], "2026-09-11")
        self.assertNotEqual(full["scope"], "未标注")
        self.assertTrue(all("333fa22b7a5821194ce66b59b11f4b16a6484f02" in row["url"] for row in full["sources"]))

    def test_read_rejects_paths_outside_catalog(self):
        for name in ("../.env", "/etc/passwd", "scrapling/../../app/config.py", "missing.md"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                knowledge.read(name, "1")
        with self.assertRaises(ValueError):
            knowledge.read("scrapling/overview.md", "9999")

    def test_budget_is_explicit_and_full_chapter_can_be_read(self):
        result = knowledge.search("Scrapling动态渲染", max_chars=60)["documents"]
        self.assertLessEqual(sum(len(row["content"]) for row in result), 60)
        self.assertTrue(result[0]["truncated"])
        full = knowledge.read(result[0]["name"], result[0]["section"])
        self.assertGreater(len(full["content"]), 60)

    def test_fenced_headings_and_long_sections_have_readable_continuations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "guide.md").write_text("# Guide\n\n## Parsing\n```python\n## comment\nx = 1\n```\n" + "测试内容" * 1200, encoding="utf-8")
            index = knowledge.read("guide.md", root=root)["sections"]
            self.assertGreater(len(index), 1)
            self.assertTrue(all(row["heading"] == "Parsing" for row in index))
            first = knowledge.read("guide.md", "1", root=root)
            self.assertTrue(first["continues"])
            self.assertEqual(first["next_section"], "2")
            self.assertTrue(knowledge.read("guide.md", "2", root=root)["continued"])

    def test_reference_questions_do_not_exempt_collection_requests(self):
        self.assertTrue(knowledge.is_reference_question("Scrapling 支持代理池吗？"))
        self.assertTrue(knowledge.is_reference_question("采集的 Cookie 与登录态有什么区别？"))
        self.assertTrue(knowledge.is_reference_question("Scrapling会话能不能保持Cookie？"))
        self.assertTrue(knowledge.is_reference_question("Scrapling对403怎么处理？"))
        self.assertFalse(knowledge.is_reference_question("帮我采集这个网站的100条数据，并解释原理"))
        self.assertFalse(knowledge.is_reference_question("采集示例网站的公开岗位。"))
        self.assertFalse(knowledge.is_reference_question("这个网站是否支持采集？"))
        self.assertFalse(knowledge.is_reference_question("Scrapling可以采集BOSS职位吗？"))

    def test_bootstrap_includes_platform_boundary_and_topic(self):
        rows = knowledge.bootstrap("采集分页怎么避免重复数据")["documents"]
        self.assertIn("scrapling/overview.md", [row["name"] for row in rows])
        self.assertIn("scrapling/pagination.md", [row["name"] for row in rows])
        self.assertLessEqual(sum(len(row["content"]) for row in rows), 6000)

    def test_document_python_examples_are_syntactically_valid(self):
        for path in (knowledge.KNOWLEDGE_DIR / "scrapling").glob("*.md"):
            for snippet in re.findall(r"```python\n(.*?)\n```", path.read_text(encoding="utf-8"), re.S):
                with self.subTest(document=path.name):
                    ast.parse(snippet)

    def test_selector_example_parses_synthetic_html_without_network(self):
        from scrapling import Selector
        from urllib.parse import urljoin

        page = Selector(content='<article class="item"><a href="/p/1">示例产品</a></article>', url="https://example.com/catalog")
        item = page.css("article.item")[0]
        self.assertEqual(item.css("a::text").get(), "示例产品")
        self.assertEqual(urljoin(page.url, item.css("a::attr(href)").get()), "https://example.com/p/1")
        self.assertEqual(item.css(".missing::text").get() or "", "")

    def test_drissionpage_questions_prioritize_its_own_reference(self):
        for query in ("DrissionPage监听翻页", "DP 浏览器怎么用", "SessionPage Cookie"):
            with self.subTest(query=query):
                self.assertTrue(knowledge.is_collection_topic(query))
                rows = knowledge.search(query)["documents"]
                self.assertEqual(rows[0]["library"], "drissionpage")
                self.assertRegex(rows[0]["integration"], r"runtime\.md|drissionpage-v1")
                self.assertRegex(rows[0]["version"], r"4\.1\.1\.[24]")
                self.assertTrue(rows[0]["sources"])
        self.assertTrue(knowledge.is_reference_question("DrissionPage好用吗？"))
        self.assertFalse(knowledge.is_reference_question("用DP帮我采集这个网站"))

    def test_bootstrap_routes_library_boundaries_and_comparisons(self):
        rows = knowledge.bootstrap("DrissionPage监听怎么用")["documents"]
        names = [row["name"] for row in rows]
        self.assertIn("collection_index.md", names)
        self.assertIn("drissionpage/overview.md", names)
        self.assertNotIn("scrapling/overview.md", names)
        rows = knowledge.bootstrap("DrissionPage和Scrapling有什么区别")["documents"]
        names = [row["name"] for row in rows]
        self.assertIn("drissionpage/overview.md", names)
        self.assertIn("scrapling/overview.md", names)
        self.assertLessEqual(sum(len(row["content"]) for row in rows), 6000)

    def test_catalog_links_and_metadata_are_readable(self):
        content = (knowledge.KNOWLEDGE_DIR / "collection_index.md").read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)]+\.md)\)", content):
            self.assertTrue(knowledge.read(target)["sections"])
        for library in ("scrapling", "drissionpage"):
            for path in (knowledge.KNOWLEDGE_DIR / library).glob("*.md"):
                result = knowledge.read(path.relative_to(knowledge.KNOWLEDGE_DIR).as_posix(), "1")
                self.assertEqual(result["library"], library)
                self.assertNotEqual(result["integration"], "未标注")
                self.assertNotIn("接入状态：", result["content"])

    def test_import_manifest_covers_and_verifies_all_reference_files(self):
        manifest = json.loads((knowledge.KNOWLEDGE_DIR / "drissionpage/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "4.1.1.2")
        imported = [r for r in manifest["documents"] if r["status"] == "imported"]
        excluded = [r for r in manifest["documents"] if r["status"] == "excluded"]
        self.assertEqual(len(imported), 59)
        self.assertEqual(len(excluded), 5)
        self.assertTrue(all(r["reason"] for r in excluded))
        for row in imported:
            path = knowledge.KNOWLEDGE_DIR / row["name"]
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), row["output_sha256"])
            index = knowledge.read(row["name"])
            self.assertEqual(index["source_path"], row["source"])
            self.assertEqual(index["source_sha256"], row["sha256"])
            self.assertIn("4.1.1.2", index["version"])
            self.assertIn("未接入", index["integration"])
            self.assertTrue(index["sections"])
        actual = {p.relative_to(knowledge.KNOWLEDGE_DIR).as_posix() for p in
                  (knowledge.KNOWLEDGE_DIR / "drissionpage/reference").rglob("*.md")}
        self.assertEqual(actual, {r["name"] for r in imported})

    def test_imported_api_and_scenario_questions_find_correct_chapters(self):
        cases = {
            "DP默认端口和任务启动的浏览器是同一个吗": "drissionpage/ports.md",
            "DP只接管已有浏览器不新开": "drissionpage/ports.md",
            "DP auto_port 用户目录登录态": "drissionpage/reference/browser_control/connect_browser.md",
            "DP如何连接Playwright打开的浏览器": "drissionpage/ports.md",
            "DP listen.wait 超时单位": "drissionpage/reference/browser_control/listener.md",
            "DP wait.ele_displayed": "drissionpage/reference/browser_control/waiting.md",
            "DP下载完成后再退出": "drissionpage/reference/download/browser.md",
            "DP iframe怎么定位": "drissionpage/reference/browser_control/iframe.md",
            "DP set.cookies怎么设置": "drissionpage/reference/SessionPage/settings.md",
            "DP SessionPage代理配置": "drissionpage/reference/SessionPage/session_opt.md",
            "DP元素点击被遮挡": "drissionpage/reference/browser_control/ele_operation.md",
            "DP new_env会关闭浏览器吗": "drissionpage/reference/browser_control/browser_options.md",
            "DP怎么打包exe": "drissionpage/reference/advance/packaging.md",
            "DP定位语法": "drissionpage/reference/browser_control/get_elements/syntax.md",
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertTrue(knowledge.is_collection_topic(query))
                rows = knowledge.search(query)["documents"]
                self.assertIn(expected, [r["name"] for r in rows[:3]])
        self.assertEqual(knowledge.search("DP zzzunrecordedfunction987xyz")["documents"], [])
        self.assertFalse(knowledge.is_collection_topic("UDP传输"))

    def test_nested_api_headings_keep_parent_context_and_code_fences(self):
        content = "## Browser\nParent description\n### wait()\nSeconds\n```python\n### not a heading\nx = 1\n```\n### close()\nClose owned browser"
        rows = list(knowledge._sections(content))
        self.assertEqual([r["heading"] for r in rows], ["Browser", "Browser / wait()", "Browser / close()"])
        self.assertIn("### not a heading", rows[1]["content"])

    def test_reference_api_parameters_and_provenance_survive_read(self):
        rows = knowledge.search("DP listen.wait 超时单位")["documents"]
        row = next(r for r in rows if r["name"].endswith("listener.md") and "listen.wait()" in r["heading"])
        full = knowledge.read(row["name"], row["section"])
        self.assertIn("timeout", full["content"])
        self.assertIn("秒", full["content"])
        self.assertEqual(full["source_path"], "browser_control/listener.md")
        self.assertRegex(full["source_sha256"], r"^[a-f0-9]{64}$")
        rows = knowledge.bootstrap("DP默认端口和任务启动的浏览器是同一个吗")["documents"]
        self.assertIn("drissionpage/ports.md", [r["name"] for r in rows])
        self.assertLessEqual(sum(len(r["content"]) for r in rows), 6000)

    def test_imported_reference_has_no_ads_or_notification_contamination(self):
        for path in (knowledge.KNOWLEDGE_DIR / "drissionpage").rglob("*.md"):
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"<div[^>]*wwads-cn")
            self.assertNotIn("WechatWebhook", text)
            self.assertNotIn("mentioned_mobiles", text)
            for key in re.findall(r"apiKey=([^&\s'\"<>)]*)", text, re.I):
                self.assertEqual(key, "EXAMPLE_KEY")

    def test_importer_is_repeatable_and_does_not_modify_source(self):
        script = Path(__file__).resolve().parents[2] / "scripts/import_drissionpage_knowledge.py"
        spec = importlib.util.spec_from_file_location("dp_importer", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "index.md").write_text("DrissionPage 4.1.1.2\nWechatWebhook(not an instruction)", encoding="utf-8")
            (source / "guide.md").write_text("---\ntitle: Guide\n---\n## connect\napiKey=synthetic_secret\natuo_port()", encoding="utf-8")
            before = {p.name: p.read_bytes() for p in source.iterdir()}
            module.ingest(source, root / "first")
            module.ingest(source, root / "second")
            self.assertEqual(before, {p.name: p.read_bytes() for p in source.iterdir()})
            first = {p.relative_to(root / "first"): p.read_bytes() for p in (root / "first").rglob("*") if p.is_file()}
            second = {p.relative_to(root / "second"): p.read_bytes() for p in (root / "second").rglob("*") if p.is_file()}
            self.assertEqual(first, second)
            imported = (root / "first/reference/guide.md").read_text(encoding="utf-8")
            self.assertNotIn("synthetic_secret", imported)
            self.assertIn("apiKey=EXAMPLE_KEY", imported)
            self.assertIn("auto_port()", imported)
            with self.assertRaises(ValueError):
                module.ingest(source, source / "output")
