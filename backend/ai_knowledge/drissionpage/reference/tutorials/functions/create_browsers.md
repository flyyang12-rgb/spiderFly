# DrissionPage：🥦 浏览器多开
工具：drissionpage
接入状态：仅知识参考；未接入 SpiderFly AI 执行工具和 collection-v1
主题：🥦 浏览器多开；tutorials/functions/create_browsers.md
版本：用户提供的 Markdown 文档 4.1.1.2
核对日期：2026-09-11
适用范围：用户文档原生 API 参考；未进行库运行验证
资料路径：tutorials/functions/create_browsers.md
资料校验：19b5915b209eb695731bd628c89e839ce89dec72cffe52c9c5a68807a887cd88
来源：[官方对应章节（可变网页，不保证与本地版本一致）](https://www.drissionpage.cn/tutorials/functions/create_browsers/)

本节介绍如何启动多个浏览器同时使用。

## ✅️️ 直接指定端口

使用`Chromium()`启动浏览器时可以直接指定端口来启动多个浏览器。

如果指定端口已有浏览器在运行，会接管这个浏览器。

使用端口号创建的浏览器用户数据文件夹会保留，只要临时文件夹未被清理，下次使用该端口时还会使用这些数据，比如登录信息和插件。

```python
from DrissionPage import Chromium

browser1 = Chromium(9222)
browser2 = Chromium(9333)
```

## ✅️️ 自动设置端口

使用`ChromiumOptions`对象的`auto_port()`方法，可自动获取空闲端口，并创建全新浏览器（无用户数据和插件）。

这时多个页面对象可共用一个`ChromiumOptions`对象，不会产生冲突。

浏览器关闭后会自动删除用户文件夹，不会过多占用硬盘空间。

```python
from DrissionPage import Chromium, ChromiumOptions

co = ChromiumOptions().auto_port()
browser1 = Chromium(co)
browser2 = Chromium(co)
```

## ✅️️ 手动指定端口和路径

也可以用指定独立端口和用户文件夹路径的方式启动多个浏览器。

需要注意的是，端口和用户文件夹每个浏览器都要独立使用，不能共用。

因此，这时每个页面对象需要自己的配置对象。

```python
from DrissionPage import Chromium, ChromiumOptions

co1 = ChromiumOptions().set_local_port(9222).set_user_data_path('data1')
co2 = ChromiumOptions().set_local_port(9333).set_user_data_path('data2')
browser1 = Chromium(co1)
browser2 = Chromium(co2)
```
