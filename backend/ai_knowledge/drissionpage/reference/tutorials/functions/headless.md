# DrissionPage：🥦 无头模式
工具：drissionpage
接入状态：仅知识参考；未接入 SpiderFly AI 执行工具和 collection-v1
主题：🥦 无头模式；tutorials/functions/headless.md
版本：用户提供的 Markdown 文档 4.1.1.2
核对日期：2026-09-11
适用范围：用户文档原生 API 参考；未进行库运行验证
资料路径：tutorials/functions/headless.md
资料校验：f013b6c255205a31664740bbe4e10c9d3179fbffb9248502fd1c6ff04b6468d4
来源：[官方对应章节（可变网页，不保证与本地版本一致）](https://www.drissionpage.cn/tutorials/functions/headless/)

要使用无头模式很简单，在`ChromiumOptions`设置`headless()`即可。

```python
from DrissionPage import Chromium, ChromiumOptions

co = ChromiumOptions().headless()
browser = Chromium(co)
```

需要注意的是，程序结束时浏览器不会自动关闭，下次运行会继续接管该浏览器。

无头浏览器因为看不见很容易被忽视。可在程序结尾用`browser.quit()`将其关闭。
