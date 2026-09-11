# 采集报错排查与验证清单
工具：scrapling
接入状态：已有受控适配；具体原生功能是否开放以正文和平台接口为准
主题：troubleshooting

版本：Scrapling 0.4.15；项目锁定版本
核对日期：2026-09-11
适用范围：原生排查思路 + SpiderFly 运行约定
来源：[docs/fetching/static.md](https://github.com/D4Vinci/Scrapling/blob/333fa22b7a5821194ce66b59b11f4b16a6484f02/docs/fetching/static.md)
来源：[docs/fetching/dynamic.md](https://github.com/D4Vinci/Scrapling/blob/333fa22b7a5821194ce66b59b11f4b16a6484f02/docs/fetching/dynamic.md)
来源：[docs/parsing/selection.md](https://github.com/D4Vinci/Scrapling/blob/333fa22b7a5821194ce66b59b11f4b16a6484f02/docs/parsing/selection.md)

关键词：报错 ModuleNotFoundError 证书 timeout 空数据 重复第一页 编码 安装 CSV 验收。

## 错误到检查点

| 现象 | 优先核对 |
| --- | --- |
| No module named scrapling | 实际解释器、依赖清单和安装环境；Windows 与 WSL 分开 |
| 浏览器 executable 不存在 | 对应环境的浏览器是否安装，不能只检查 Python 包 |
| CSS 返回空 | 真实 HTML 是否含目标、容器范围、页面是否仅加载占位 |
| HTTP 200 却没记录 | 响应是否登录/验证/推荐页，接口是否真的返回列表 |
| timeout | HTTP 秒与浏览器毫秒、等待选择器、整体运行预算 |
| 每页重复 | 分页字段是否真实变化、后端是否忽略参数、主键集合 |
| 字段错位 | 是否以每条记录容器为单位，而不是分别抓全页列 |
| CSV 乱码 | 输出编码与读取软件；中文 Excel 常用 utf-8-sig |
| 只留断点没文件 | 脚本是否把 crawl 返回记录写入产物目录 |

## 修改前的证据

保存错误类型、实际 URL、状态码、选取的字段、脱敏样本和实际条数。不要把历史网站观察当本次结果。已有草稿先读取当前版本；修复后重新保存并试跑，不能引用旧版本成功。

## 验收边界

语法正确只说明能解析；浏览器打开只说明能加载；文件存在不代表字段正确；行数足够不代表符合地区和筛选。需求中的字段、去重和数量必须独立检查。缺失业务条件应说明，不能替用户编造验收标准。

## 知识不足怎么办

先换更具体主题检索，再按章节读取官方来源。知识版本不明或参数未核对时明确不确定；生成示例中的占位 URL 与选择器要标成示例，不能声称是真实网页结构。知识检索不会访问网站，也不会执行知识里的代码。
