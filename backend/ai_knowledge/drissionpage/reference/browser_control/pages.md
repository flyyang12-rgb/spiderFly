# DrissionPage：🛰️ Page 对象
工具：drissionpage
接入状态：仅知识参考；未接入 SpiderFly AI 执行工具和 collection-v1
主题：🛰️ Page 对象；browser_control/pages.md
版本：用户提供的 Markdown 文档 4.1.1.2
核对日期：2026-09-11
适用范围：用户文档原生 API 参考；未进行库运行验证
资料路径：browser_control/pages.md
资料校验：0a7d76ff6081a502dd55bfef731bd322bb41941123e21cb2cc193fa0a0a6b461
来源：[官方对应章节（可变网页，不保证与本地版本一致）](https://www.drissionpage.cn/browser_control/pages/)

`ChromiumPage`和`WebPage`是 4.1 之前用于连接和控制浏览器的对象。

4.1 这些功能由`Chromium`实现，但`ChromiumPage`和`WebPage`仍能正常使用。

对比`Chromium`，`ChromiumPage`和`WebPage`在连接浏览器时可以少写一行代码，但在多标签页操作的时候容易造成混乱。

更详细的用法可以看旧版文档。

## ✅️️ `ChromiumPage`

`ChromiumPage`把浏览器管理功能和一个标签页（默认接管时激活那个）控制功能整合在一起。

可看作浏览器对象，但同时控制了一个标签页。

如果项目只需要使用单标签页，用`ChromiumPage`会比较方便。

`ChromiumPage`创建的标签页对象为`ChromiumTab`，没有切换模式功能。

|     初始化参数      |                     类型                     | 默认值    | 说明                                                                                                  |
|:--------------:|:------------------------------------------:|:------:|-----------------------------------------------------------------------------------------------------|
| `addr_or_opts` | `str`<br/>`int`<br/>`ChromiumOptions`<br/> | `None` | 浏览器启动配置或接管信息。<br/>传入 'ip: port' 字符串、端口数字或`ChromiumOptions`对象时按配置启动或接管浏览器；<br/>为`None`时使用配置文件配置启动浏览器 |
|    `tab_id`    |                   `str`                    | `None` | 要控制的标签页 id，不指定默认为激活的                                                                                |

```python
from DrissionPage import ChromiumPage

page = ChromiumPage()
page.get('http://DrissionPage.cn')
print(page.title)
```

---

## ✅️️ `WebPage`

`WebPage`覆盖了`ChromiumPage`所有功能，并且增加了切换模式功能，创建的标签页对象为`MixTab`。

|       初始化参数       |                   类型                    |  默认值   | 说明                                                                         |
|:-----------------:|:---------------------------------------:|:------:|----------------------------------------------------------------------------|
|      `mode`       |                  `str`                  |  `'d'`  | 运行模式，可选`'d'`或`'s'`                                                         |
|  `chromium_options`   |    `bool`<br/>`ChromiumOptions`<br/>    | `None` | `ChromiumOptions`对象，传入`None`时从默认 ini 文件读取，传入`False`时不读取 ini 文件，使用默认配置      |
| `session_or_options` | `SessionOptions`<br/>`None`<br/>`False` | `None` | `Session`对象或`SessionOptions`对象，传入`None`时从默认 ini 文件读取，传入`False`时不读取 ini 文件，使用默认配置 |

```python
from DrissionPage import WebPage

page = WebPage()
page.get('http://DrissionPage.cn')
print(page.title)
page.change_mode()
print(page.title)
```
