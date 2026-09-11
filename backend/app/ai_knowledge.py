"""本地知识的章节检索、来源信息和采集问答上下文；不联网或执行知识代码。"""
from __future__ import annotations

import re
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "ai_knowledge"
SECTION_CHARS = 3600
QUERY_CHARS = 1000
ALIASES = (
    ("端口", "调试地址", "9222", "9333", "set_local_port", "set_address"),
    ("接管", "复用浏览器", "已有浏览器", "existing_only", "connect_browser"),
    ("监听", "数据包", "listen.start", "listen.wait", "listener"),
    ("用户目录", "用户文件夹", "配置目录", "set_user_data_path"),
    ("无头", "headless"),
    ("翻页", "分页", "pagination", "下一页", "next"),
    ("断点", "续采", "续跑", "恢复", "checkpoint", "resume"),
    ("并发", "异步", "concurrency", "async", "限速"),
    ("选择器", "selector", "css", "xpath", "字段提取"),
    ("自适应", "adaptive", "改版", "元素定位"),
    ("登录态", "会话", "cookie", "session"),
    ("动态", "渲染", "javascript", "dynamic", "无头"),
    ("反爬", "stealth", "风控", "403", "429", "限流"),
    ("代理", "proxy", "ip轮换"),
    ("知识库", "rag", "markdown", "文档采集"),
    ("去重", "重复", "unique", "dedup"),
    ("安装", "依赖", "版本", "install", "modulenotfounderror"),
    ("spider", "爬虫框架", "爬虫引擎"),
)


def is_collection_topic(text: str) -> bool:
    return bool(re.search(
        r"scrapling|drissionpage|(?<![a-z0-9_])dp(?![a-z0-9_])|chromium|sessionpage|浏览器|调试端口|采集|抓取|爬虫|爬取|翻页|分页|选择器|网页|网站|cookie|xpath|反爬|断点续|fetcher",
        text or "", re.I,
    ))


def is_reference_question(text: str) -> bool:
    """Only exempt explanations from the live-site refusal guard, never execution requests."""
    if re.search(r"https?://|(?:这个|该|目标|指定|某个)网站|(?:这个|该|目标)站点", text, re.I):
        return False
    if re.search(r"(?:帮我|请|直接|实际|现在).{0,8}(?:采集|抓取|爬取|访问|搜索)|采集.{0,12}(?:条|页|文件)", text):
        return False
    if re.search(r"(?:能不能|能否|是否可以|可以).{0,8}(?:采集|抓取|访问|爬取)", text):
        return False
    return bool(re.search(r"区别|原理|用法|怎么用|如何使用|解释|介绍|讲解|知识库|文档|是什么|是否支持|支持.{0,20}吗|能否|能不能", text, re.I)
                or (re.search(r"scrapling|drissionpage|(?<![a-z0-9_])dp(?![a-z0-9_])", text, re.I) and re.search(r"怎么|如何|吗|[？?]", text)))


def _libraries(query: str) -> set[str]:
    libraries = set()
    if re.search(r"scrapling", query, re.I):
        libraries.add("scrapling")
    if re.search(r"drissionpage|(?<![a-z0-9_])dp(?![a-z0-9_])|sessionpage|chromiumoptions|auto_port|existing_only", query, re.I):
        libraries.add("drissionpage")
    return libraries


def _tokens(text: str) -> set[str]:
    text = text.casefold()
    for filler in ("请问", "如何", "怎么", "什么", "可以", "帮我", "一下", "这个", "那个", "是否"):
        text = text.replace(filler, " ")
    result = set(re.findall(r"[a-z_][a-z0-9_.-]*|\d{3}", text))
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        result.update(run[i:i + 2] for i in range(len(run) - 1))
    return result


def _query_tokens(query: str) -> set[str]:
    lowered = query.casefold()
    expanded = [query]
    for group in ALIASES:
        if any((re.search(r"(?<![a-z0-9_])" + re.escape(word) + r"(?![a-z0-9_])", lowered)
                if re.search(r"[a-z]", word) else word in lowered) for word in group):
            expanded.extend(group)
    return _tokens(" ".join(expanded))


def _paths(root: Path):
    root = root.resolve()
    for path in sorted(root.rglob("*.md")):
        # Knowledge readers must not follow links into runtime files.
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            continue
        if any(parent.is_symlink() for parent in path.parents if parent != root and parent.is_relative_to(root)):
            continue
        yield path


def _metadata(path: Path, root: Path, content: str) -> dict:
    def field(label):
        match = re.search(rf"^{label}：(.+)$", content, re.M)
        return match.group(1).strip() if match else "未标注"
    title = re.search(r"^# (.+)$", content, re.M)
    source_lines = re.findall(r"^来源：(.+)$", content, re.M)
    return {
        "name": path.relative_to(root).as_posix(),
        "title": title.group(1) if title else path.stem,
        "version": field("版本"), "scope": field("适用范围"),
        "verified_at": field("核对日期"),
        "library": field("工具"), "integration": field("接入状态"),
        "topics": field("主题"),
        "source_path": field("资料路径"), "source_sha256": field("资料校验"),
        "sources": [{"title": title, "url": url} for line in source_lines
                    for title, url in re.findall(r"\[([^]]+)\]\((https://[^)]+)\)", line)],
    }


def _sections(content: str):
    """Split headings outside code fences, with explicit IDs for continuation chunks."""
    heading = "概览"
    lines = []
    sections = []
    fence = None
    parents = {}
    for line in content.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)[0]
            fence = None if fence == token else token if fence is None else fence
        if fence is None and re.match(r"^(?:# |(?:版本|核对日期|适用范围|来源|关键词|工具|接入状态|主题|资料路径|资料校验)：)", line):
            continue
        if fence is None and re.fullmatch(r"\s*[-*_]{3,}\s*", line):
            continue
        heading_match = re.match(r"^(#{2,4}) (.+)$", line) if fence is None else None
        if heading_match:
            if any(item.strip() for item in lines):
                sections.append((heading, "\n".join(lines).strip()))
            level = len(heading_match.group(1))
            parents = {key: value for key, value in parents.items() if key < level}
            parents[level] = heading_match.group(2).strip()
            heading, lines = " / ".join(parents.values()), []
        else:
            lines.append(line)
    if lines:
        sections.append((heading, "\n".join(lines).strip()))
    index = 0
    for heading, body in sections:
        for offset in range(0, len(body), SECTION_CHARS):
            index += 1
            yield {"section": str(index), "heading": heading,
                   "content": body[offset:offset + SECTION_CHARS],
                   "continued": offset > 0, "continues": offset + SECTION_CHARS < len(body)}


def read(name: str, section: str = "", *, root: Path = KNOWLEDGE_DIR) -> dict:
    root = root.resolve()
    paths = {path.relative_to(root).as_posix(): path for path in _paths(root)}
    if name not in paths:
        raise ValueError("知识文档不存在；请使用检索结果中的 name")
    path = paths[name]
    content = path.read_text(encoding="utf-8")
    metadata = _metadata(path, root, content)
    sections = list(_sections(content))
    if not section:
        return {**metadata, "sections": [{k: row[k] for k in ("section", "heading", "continued", "continues")}
                                          for row in sections]}
    match = next((row for row in sections if row["section"] == section), None)
    if match is None:
        raise ValueError("章节不存在；先用空 section 查看章节目录")
    return {**metadata, **match, "next_section": str(int(section) + 1) if int(section) < len(sections) else None}


def search(query: str, *, root: Path = KNOWLEDGE_DIR, limit: int = 4, max_chars: int = 9000) -> dict:
    query = (query or "").strip()[:QUERY_CHARS]
    terms = _query_tokens(query)
    if not terms:
        return {"query": query, "documents": [], "note": "请输入主题，如翻页、Cookie、自适应选择器。"}
    root = root.resolve()
    libraries = _libraries(query)
    topical_terms = terms - {"scrapling", "drissionpage", "dp"}
    terms = topical_terms or terms
    direct = _tokens(query) - {"scrapling", "drissionpage", "dp"}
    ranked = []
    for path in _paths(root):
        content = path.read_text(encoding="utf-8")
        metadata = _metadata(path, root, content)
        for section in _sections(content):
            body = section["content"]
            body_tokens = _tokens(body)
            title_tokens = _tokens(metadata["title"] + " " + section["heading"])
            context_tokens = _tokens(metadata["source_path"])
            matches = terms & body_tokens
            title_matches = terms & title_tokens
            # Actual words/API names outrank synonym expansion and document size.
            score = (len(matches) + 2 * len(title_matches)
                     + 3 * len(direct & body_tokens) + 8 * len(direct & title_tokens)
                     + 4 * len(direct & context_tokens))
            if score:
                if metadata["library"].casefold() in libraries:
                    score += 40
                elif not libraries and is_collection_topic(query) and metadata["library"] == "scrapling":
                    score += 6
                ranked.append((score, {**metadata, **section}))
    ranked.sort(key=lambda row: (-row[0], row[1]["name"], int(row[1]["section"])))
    documents = []
    remaining = max(0, max_chars)
    per_document = {}
    for _, row in ranked:
        if len(documents) >= limit or remaining <= 0:
            break
        if per_document.get(row["name"], 0) >= 2:
            continue
        size = min(len(row["content"]), remaining)
        documents.append({**row, "content": row["content"][:size], "truncated": size < len(row["content"])})
        per_document[row["name"]] = per_document.get(row["name"], 0) + 1
        remaining -= size
    return {"query": query, "documents": documents,
            "note": "知识是版本化参考，不是网页实测证据。用 read_knowledge 的 name 和 section 读取完整章节。"
                    if documents else "没有匹配知识；请换用具体主题，不据此断言功能不存在。"}


def bootstrap(query: str, *, root: Path = KNOWLEDGE_DIR) -> dict:
    """Small automatic grounding bundle; detailed retrieval remains an explicit tool."""
    result = search(query, root=root, limit=2, max_chars=4200)
    libraries = _libraries(query) or {"scrapling"}
    # The catalog is the shared boundary; each named library keeps its own boundary.
    names = ["collection_index.md"] + [f"{library}/overview.md" for library in sorted(libraries)]
    for name in reversed(names):
        try:
            overview = read(name, "1", root=root)
        except ValueError:
            continue
        if not any(row["name"] == name and row["section"] == "1" for row in result["documents"]):
            result["documents"].insert(0, overview)
    return result
