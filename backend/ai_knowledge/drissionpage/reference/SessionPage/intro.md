# DrissionPage：🛩️ 概述
工具：drissionpage
接入状态：仅知识参考；未接入 SpiderFly AI 执行工具和 collection-v1
主题：🛩️ 概述；SessionPage/intro.md
版本：用户提供的 Markdown 文档 4.1.1.2
核对日期：2026-09-11
适用范围：用户文档原生 API 参考；未进行库运行验证
资料路径：SessionPage/intro.md
资料校验：83b26d772b3d85a7f0cd0c26a1951e799dd735f0db967c4b3103533bb72471ff
来源：[官方对应章节（可变网页，不保证与本地版本一致）](https://www.drissionpage.cn/SessionPage/intro/)

`SessionPage`对象和`WebPage`对象的 s 模式，可用收发数据包的形式访问网页。

`SessionPage`是一个使用使用`Session`（requests 库）对象的页面，封装了网络连接和结果解析功能，使收发数据包也可以像操作页面一样便利。

**示例：**

获取 gitee 推荐项目第一页所有项目。

```python
# 导入
from DrissionPage import SessionPage
# 创建页面对象
page = SessionPage()
# 访问网页
page.get('https://gitee.com/explore/all')
# 在页面中查找元素
items = page.eles('t:h3')
# 遍历元素
for item in items[:-1]:
    # 获取当前<h3>元素下的<a>元素
    lnk = item('tag:a')
    # 打印<a>元素文本和href属性
    print(lnk.text, lnk.link)
```

**输出：**

```shell
七年觐汐/wx-calendar https://gitee.com/qq_connect-EC6BCC0B556624342/wx-calendar
ThingsPanel/thingspanel-go https://gitee.com/ThingsPanel/thingspanel-go
APITable/APITable https://gitee.com/apitable/APITable
Indexea/ideaseg https://gitee.com/indexea/ideaseg
CcSimple/vue-plugin-hiprint https://gitee.com/CcSimple/vue-plugin-hiprint
william_lzw/ExDUIR.NET https://gitee.com/william_lzw/ExDUIR.NET
anolis/ancert https://gitee.com/anolis/ancert
cozodb/cozo https://gitee.com/cozodb/cozo
后面省略...
```
