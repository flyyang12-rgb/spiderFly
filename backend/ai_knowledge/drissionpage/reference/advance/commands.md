# DrissionPage：⚙️ 命令行的使用
工具：drissionpage
接入状态：仅知识参考；未接入 SpiderFly AI 执行工具和 collection-v1
主题：⚙️ 命令行的使用；advance/commands.md
版本：用户提供的 Markdown 文档 4.1.1.2
核对日期：2026-09-11
适用范围：用户文档原生 API 参考；未进行库运行验证
资料路径：advance/commands.md
资料校验：7f0f090b8a473db13d34659236ea6cce2b629b55b371e98ccd10e4e8cbbbc3ba
来源：[官方对应章节（可变网页，不保证与本地版本一致）](https://www.drissionpage.cn/advance/commands/)

DrissionPage 提供一些便捷的命令行命令，用于基本设置，以取代有时需要的临时配置文件。

命令行主命令为`dp`，形式为：

```shell
dp 命令全称或缩写 <参数>
```

## ✅️️ 设置浏览器路径

| 全称                 | 缩写  | 参数    | 说明            |
|:------------------:|:---:|:-----:|:-------------:|
| --set-browser-path | -p  | 浏览器路径 | 设置配置文件中的浏览器路径 |

**示例：**

```shell
# 完整写法
dp --set-browser-path "D:\chrome\Chrome.exe"

# 简略写法
dp -p "D:\chrome\Chrome.exe"
```

## ✅️️ 设置用户数据路径

| 全称              | 缩写  | 参数        | 说明             |
|:---------------:|:---:|:---------:|:--------------:|
| --set-user-path | -u  | 用户数据文件夹路径 | 设置配置文件中的用户数据路径 |

**示例：**

```shell
# 完整写法
dp --set-user-path D:\chrome\user_data

# 简略写法
dp -u D:\chrome\user_data
```

## ✅️️ 复制默认 ini 文件到当前路径

| 全称                | 缩写  | 参数  | 说明            |
|:-----------------:|:---:|:---:|:-------------:|
| --configs-to-here | -c  | 无   | 复制默认配置文件到当前路径 |

**示例：**

```shell
# 完整写法
dp --configs-to-here

# 简略写法
dp -c
```

## ✅️️ 启动浏览器

此命令用于启动浏览器，等待程序接管。

| 全称               | 缩写  | 参数  | 说明                      |
|:----------------:|:---:|:---:|:-----------------------:|
| --launch-browser | -l  | 端口号 | 启动浏览器，传入端口号，0表示用配置文件中的值 |

**示例：**

```shell
# 完整写法
dp --launch-browser 9333

# 简略写法
dp -l 0
```
