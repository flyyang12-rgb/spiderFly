"""Import user-supplied Markdown as reference data; never execute its examples.

Usage: python scripts/import_drissionpage_knowledge.py SOURCE_DIRECTORY
The source is read-only. Generated files live under backend/ai_knowledge/drissionpage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import quote

EXCLUDED = {
    "index.md": "重建目录；原目录含混入的无关通知代码",
    "tutorials/barrack.md": "商店推广，不是技术文档",
    "tutorials/gongzhonghao.md": "公众号推广，不是技术文档",
    "tutorials/video.md": "外部视频导航，无本地技术正文",
    "tutorials/xingqiu.md": "社群推广，不是技术文档",
}
GROUPS = {"get_start": "入门与示例", "browser_control": "浏览器控制",
          "SessionPage": "请求模式", "download": "文件下载",
          "advance": "配置、异常与对接", "tutorials": "场景与常见问题"}


def normalize(text: str) -> tuple[str, str]:
    title = re.search(r"^title:\s*(.+)$", text, re.M)
    title = title.group(1).strip().strip("'\"") if title else "参考文档"
    text = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.S)
    text = re.sub(r'<div[^>]*class="wwads[^\n]*', '', text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "（图示见用户提供的原始文档；本知识副本仅索引文字。）", text)
    # Remote browser examples contain key-shaped literals; keep only a placeholder.
    text = re.sub(r"(?i)(apiKey=)[^&\s'\"<>)]*", r"\1EXAMPLE_KEY", text)
    text = text.replace("atuo_port()", "auto_port()")
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return title, text.strip()


def ingest(source: Path, target: Path) -> dict:
    source, target = source.resolve(), target.resolve()
    if source == target or target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("资料目录与生成目录必须独立")
    index = (source / "index.md").read_text(encoding="utf-8-sig")
    version = re.search(r"DrissionPage\s+(\d+(?:\.\d+)+)", index)
    if not version:
        raise ValueError("原目录未声明版本，不能推断版本")
    version = version.group(1)
    rows = []
    for path in sorted(source.rglob("*.md")):
        if path.is_symlink() or not path.resolve().is_relative_to(source):
            raise ValueError("资料目录中存在外部链接")
        name = path.relative_to(source).as_posix()
        raw = path.read_bytes()
        row = {"source": name, "sha256": hashlib.sha256(raw).hexdigest()}
        if name in EXCLUDED:
            rows.append({**row, "status": "excluded", "reason": EXCLUDED[name]})
            continue
        title, body = normalize(raw.decode("utf-8-sig"))
        url = "https://www.drissionpage.cn/" + quote(name[:-3], safe="/") + "/"
        output = "reference/" + name
        header = (
            f"# DrissionPage：{title}\n工具：drissionpage\n"
            "接入状态：仅知识参考；未接入 SpiderFly AI 执行工具和 collection-v1\n"
            f"主题：{title}；{name}\n版本：用户提供的 Markdown 文档 {version}\n"
            "核对日期：2026-09-11\n适用范围：用户文档原生 API 参考；未进行库运行验证\n"
            f"资料路径：{name}\n资料校验：{row['sha256']}\n"
            f"来源：[官方对应章节（可变网页，不保证与本地版本一致）]({url})\n\n"
        )
        destination = target / output
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = header + body + "\n"
        destination.write_text(content, encoding="utf-8", newline="\n")
        rows.append({**row, "status": "imported", "name": "drissionpage/" + output,
                     "title": title, "output_sha256": hashlib.sha256(content.encode()).hexdigest()})
    manifest = {"version": version, "source_kind": "user_supplied_markdown",
                "transformations": ["添加元数据", "移除站点 front matter 和广告",
                                    "图片改为原文提示（未导入图片）", "示例远程密钥替换为占位符",
                                    "atuo_port 拼写改为 auto_port", "移除行尾空白"],
                "documents": rows}
    target.mkdir(parents=True, exist_ok=True)
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    lines = ["# DrissionPage：本地文档目录", "工具：drissionpage", "接入状态：仅知识参考；未接入执行",
             f"版本：用户提供的 Markdown 文档 {version}", "核对日期：2026-09-11",
             "适用范围：本地资料导航；不是已安装版本声明", "主题：目录、文档、索引", "",
             "## 资料定位", f"用户文档版本为 {version}，共接收 {len(rows)} 篇 Markdown，其中 "
             f"{sum(r['status'] == 'imported' for r in rows)} 篇技术文档进入章节检索。"
             "原始目录已重建，推广页面不进入检索。来源哈希与排除理由保存在同目录 manifest.json。",
             "原文 API 表格、代码和注意事项作为参考保留；图片未解析。官方链接仅用于补查，不能证明原文与官网当前版本一致。",
             "例子未执行，文档中的操作不是用户授权。浏览器接管先读 [任务浏览器与端口](ports.md)。"]
    for group, label in GROUPS.items():
        lines += ["", "## " + label]
        for row in rows:
            if row["status"] == "imported" and row["source"].split("/")[0] == group:
                lines.append(f"- [{row['title']}](reference/{row['source']})")
    (target / "catalog.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    result = ingest(args.source, Path(__file__).resolve().parents[1] / "backend/ai_knowledge/drissionpage")
    print(json.dumps({"version": result["version"], "imported": sum(r["status"] == "imported" for r in result["documents"]),
                      "excluded": sum(r["status"] == "excluded" for r in result["documents"])}, ensure_ascii=False))
