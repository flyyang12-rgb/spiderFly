"""Compact request labels without an extra model call or visiting user URLs."""
import re
from urllib.parse import urlsplit

URL = re.compile(r'https?://[^\s<>\u3400-\u9fff，。；！？]+', re.I)


def summarize_request(content: str) -> str:
    links = URL.findall(content)
    site = ''
    if links:
        try:
            host = (urlsplit(links[0]).hostname or '').lower()
        except ValueError:
            host = ''
        for domain, label in (('jd.com', '京东'), ('taobao.com', '淘宝'), ('tmall.com', '天猫'), ('zhipin.com', 'BOSS直聘')):
            if host == domain or host.endswith('.' + domain):
                site = label
                break
        else:
            site = host.removeprefix('www.')
    text = URL.sub(' ', content)
    text = re.sub(r'\[([^\]]+)\]\(\s*\)', r'\1', text)
    text = re.sub(r'\s+', ' ', text).strip(' ：:，,。;；')
    text = re.sub(r'^(?:(?:请你|请|麻烦你|麻烦|帮我|帮助我)\s*)+', '', text)
    text = re.sub(r'(采集|抓取|获取|提取)(?:这个|该|这些)?(评论|评价)[，,。\s]*(最新|最近)的?([0-9一二两三四五六七八九十百]+)条', r'\1\3\4条\2', text)
    text = re.split(r'[。！？\n]', text, maxsplit=1)[0].strip()
    if not text:
        return (site[:24] + '链接') if site else '新对话'
    title = f'{site} · {text}' if site and site.casefold() not in text.casefold() else text
    return title if len(title) <= 32 else title[:31].rstrip() + '…'


def display_title(thread: dict, first_request: str | None) -> dict:
    # Task names and independently named conversations keep their existing names.
    if first_request and not thread.get('task_id') and thread['title'] in ('新建 AI 任务', first_request[:50], summarize_request(first_request)):
        thread['title'] = summarize_request(first_request)
    return thread
