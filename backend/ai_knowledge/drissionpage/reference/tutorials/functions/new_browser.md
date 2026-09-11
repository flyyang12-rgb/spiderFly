# DrissionPage：🥦 创建全新的浏览器
工具：drissionpage
接入状态：仅知识参考；未接入 SpiderFly AI 执行工具和 collection-v1
主题：🥦 创建全新的浏览器；tutorials/functions/new_browser.md
版本：用户提供的 Markdown 文档 4.1.1.2
核对日期：2026-09-11
适用范围：用户文档原生 API 参考；未进行库运行验证
资料路径：tutorials/functions/new_browser.md
资料校验：3b674e2bad71cf065f3f062ecc7a9613217aac0215ac7fb81ac85bb6bac1f6c8
来源：[官方对应章节（可变网页，不保证与本地版本一致）](https://www.drissionpage.cn/tutorials/functions/new_browser/)

默认设置下，DrissionPage 会在 9222 端口创建浏览器，如果该端口下浏览器已经启动，则会接管使用。

并且会重复使用用户文件夹，即该端口登录过的账号，下次启动时可能仍有效，使用的插件也一样。

这为调试和日常使用带来大量便利，无须总是要处理登录和加载插件。

但项目中往往需要创建全新的浏览器环境，不希望复用之前的用户数据，可用以下方法实现：

## ✅️️ `auto_port()`方法

使用`ChromiumOptions`对象的`auto_port()`方法，可指定程序自动创建全新的浏览器，多个浏览器互不干扰。

```python
from DrissionPage import ChromiumOptions, Chromium

co = ChromiumOptions().auto_port()
browser1 = Chromium(co)
browser2 = Chromium(co)
```

如此即可创建两个全新且独立的浏览器。

可以注意到，示例中两个`Chromium`对象共用了一个`ChromiumOptions`对象，这在设置`auto_port()`时才会生效。

如果没有设置`auto_port()`，两个页面对象其实是同一个。

:::warning 注意
    如果使用`auto_port()`后再使用`set_local_port()`、`set_address()`或`set_user_data_path()`，会覆盖`auto_port()`设置。
:::

---

## ✅️️ 手动配置

如果有更细致的需求，不使用`auto_port()`，可自行使用`set_local_port()`、`set_address()`和`set_user_data_path()`为每个浏览器指定端口和用户文件夹。

:::warning 注意
    - 务必注意的是，每个浏览器的端口和用户文件夹都必须是独立的，不能复用
    - 每个浏览器都要一个`ChromiumOptions`对象，不能复用
:::

```python
from DrissionPage import Chromium, ChromiumOptions

co1 = ChromiumOptions().set_local_port(9111).set_user_data_path('data1')
co2 = ChromiumOptions().set_local_port(9222).set_user_data_path('data2')

browser1 = Chromium(co1)
browser2 = Chromium(co2)
```
