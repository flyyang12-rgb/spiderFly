import unittest

from app.ai_titles import display_title, summarize_request


class TitleTests(unittest.TestCase):
    def test_link_followed_immediately_by_request(self):
        request = 'https://item.jd.com/123.html?pckd=abcXYZ帮我采集这个评论 最新的十条'
        self.assertEqual(summarize_request(request), '京东 · 采集最新十条评论')

    def test_plain_requests_and_limits(self):
        self.assertEqual(summarize_request('请帮我汇总每周销售额。保留原文件。'), '汇总每周销售额')
        self.assertLessEqual(len(summarize_request('整理每周销售额' * 20)), 32)
        self.assertEqual(summarize_request('123'), '123')

    def test_bare_link_does_not_invent_a_goal(self):
        self.assertEqual(summarize_request('https://item.jd.com/123.html?tracking=abc'), '京东链接')
        self.assertEqual(summarize_request('https://example.com/path?token=abc'), 'example.com链接')
        self.assertEqual(summarize_request(''), '新对话')

    def test_host_matching_is_exact(self):
        self.assertEqual(summarize_request('https://jd.com.example.org/ 查看页面'), 'jd.com.example.org · 查看页面')

    def test_historical_auto_titles_and_named_tasks(self):
        request = 'https://item.jd.com/123.html?tracking=abc帮我采集这个评论 最新的十条'
        self.assertEqual(display_title({'title': request[:50], 'task_id': None}, request)['title'], '京东 · 采集最新十条评论')
        self.assertEqual(display_title({'title': request[:50], 'task_id': 1}, request)['title'], request[:50])
        self.assertEqual(display_title({'title': '自定义名称'}, request)['title'], '自定义名称')
