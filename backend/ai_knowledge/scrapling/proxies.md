# 代理、限流和访问失败
工具：scrapling
接入状态：已有受控适配；具体原生功能是否开放以正文和平台接口为准
主题：proxies

版本：Scrapling 0.4.15；项目锁定版本
核对日期：2026-09-11
适用范围：原生代理参考；平台未提供用户代理池配置
来源：[docs/spiders/proxy-blocking.md](https://github.com/D4Vinci/Scrapling/blob/333fa22b7a5821194ce66b59b11f4b16a6484f02/docs/spiders/proxy-blocking.md)
来源：[docs/fetching/stealthy.md](https://github.com/D4Vinci/Scrapling/blob/333fa22b7a5821194ce66b59b11f4b16a6484f02/docs/fetching/stealthy.md)

关键词：proxy 代理池 IP轮换 限流 429 403 Retry-After 重试 风控。

## 先诊断再调整

403 可能与访问规则、认证、地区或请求特征有关；429 通常表示限流；网络超时可能是连接、页面等待或预算耗尽。先看实际状态码、响应内容和请求频率。不能仅凭状态码断言登录或换代理必定解决，也不能把请求失败静默当空结果。

## 原生代理能力

Scrapling 原生文档描述请求代理和 ProxyRotator 等机制，用于管理请求出口；具体同步、异步、浏览器会话参数应核对固定版本。代理地址与凭据属于配置，不应写入日志或示例结果。原生能力不等于平台已提供代理池的界面或运行配置。

## 平台现状

SpiderFly 的探索与 collection-v1 没有暴露任意代理 URL、原生 ProxyRotator 或可自由修改的网络策略。回答用户代理方案时说明尚未接入，不能编造设置项。collection-v1 继续由 broker 检查公网目标和允许的方法。

## 有限重试与结果说明

临时网络故障可讨论有上限的退避；明确限流应参考响应等待指示并减少请求。现有平台任务有时长与数量预算，不能无限重试。实际条数不足就给出已完成量与证据，不用重复记录补足数量，不通过改变筛选条件伪造完成。
