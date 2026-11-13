AutoKaraoke 中文增强版（Windows 10/11，64 位）

项目来源：https://github.com/GiacoBot/AutoKaraoke
原项目作者：GiacoBot 及其贡献者。
中文增强仓库：https://github.com/Dhz8678/AutoKaraoke
本版本在原项目基础上增加汉化、进度显示、自定义歌词修正 API、简体转换和中文排版修复。
保留原项目历史，沿用 GPL-3.0-or-later；许可证及第三方说明见 licenses 目录。

1. 解压整个文件夹，双击 AutoKaraoke.bat 启动。
2. 首次启动下载 Python 和编辑器依赖，约 300 MB。
3. AI 组件按需安装：NVIDIA 显卡版约 3～4 GB，CPU 版小于 1 GB。
   下载期间会显示组件完成数量、大文件下载量、百分比和观测平均速度。
4. 人声分离、识别及对齐模型将在首次使用相应功能时另行下载。
5. “文件 → 设置”可以配置歌词修正 API 地址、密钥及模型名称。
   支持兼容 Chat Completions 且能返回 JSON 的本地或远程模型服务。

已有安装的用户：请等待当前下载完成后关闭软件，重新启动以加载更新。
运行环境继续保存在 %LOCALAPPDATA%\AutoKaraoke。
项目默认保存在 %USERPROFILE%\Documents\AutoKaraoke。
更详细的下载源、模型说明和配置方法见“中文增强版说明.md”。

视频生成工具 FFmpeg 和包管理工具 uv 已随发行包提供。
请使用你拥有的本地音频文件；发行版不提供流媒体音频下载。
运行日志：%APPDATA%\autokaraoke\autokaraoke.log
