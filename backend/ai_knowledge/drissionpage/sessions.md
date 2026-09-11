# DrissionPage：请求模式与登录态
工具：drissionpage
接入状态：原生 API 参考；当前平台接入范围以 runtime.md 为准
主题：SessionPage、HTTP、Cookie、会话、模式切换
版本：官方文档 4.1.1.4；不是本项目安装版本
核对日期：2026-09-11
适用范围：DrissionPage 原生用法与选型参考，不是平台可执行接口
来源：[SessionPage 概述](https://drissionpage.cn/SessionPage/intro/)
来源：[模式切换](https://drissionpage.cn/browser_control/mode_change/)

## SessionPage 与浏览器模式
SessionPage 封装 requests Session 的请求及结果解析，适合研究无需浏览器交互的页面获取。请求模式与浏览器渲染不同，不能把请求成功当作动态内容已加载。
原生混合模式的切换须查对应对象和版本文档，不能假定任意 Chromium、Tab、SessionPage 对象都有相同方法。

## Cookie 与定时任务
原生会话能力不等于 SpiderFly 已支持传递登录态。当前探索浏览器、普通 Windows 任务、collection-v1 属于不同运行环境；知识库不会在它们之间复制 Cookie。
回答“登录后能否定时采集”时，必须分别说明原生实现思路和平台现状，不承诺登录永久有效，也不能输出或保存用户凭据作为知识资料。
