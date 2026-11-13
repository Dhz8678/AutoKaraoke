# Windows 两种发行方式

原项目及作者：[GiacoBot/AutoKaraoke](https://github.com/GiacoBot/AutoKaraoke)，GiacoBot 及其贡献者。
中文增强与打包源码：[Dhz8678/AutoKaraoke](https://github.com/Dhz8678/AutoKaraoke)，沿用 GPL-3.0-or-later。

| 版本 | 下载内容 | 使用方式 |
|---|---|---|
| 离线完整版 | `*-offline-Setup.exe` 和全部同名 `*.bin` 分卷 | 全部放在同一文件夹，双击 EXE 安装，再运行桌面或开始菜单中的 AutoKaraoke |
| BAT 联网版 | `*-online.zip` | 解压后双击 AutoKaraoke.bat，按需下载 Python、依赖及模型 |

GitHub [每个 Release 附件必须小于 2 GiB](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases#storage-and-bandwidth-quotas)。
离线完整版使用 EXE 加数据分卷；不需要合并分卷或另外安装解压软件。安装后的 `AutoKaraoke.exe` 是启动入口，旁边的运行环境及模型目录须保留。

离线版内置 Python、PySide6、PyTorch（CUDA/CPU）、ONNX Runtime、音频依赖、FFmpeg，
以及 MelBand RoFormer、Qwen3-ForcedAligner-0.6B、MMS-300M、Qwen3-ASR-1.7B。
本地导入音频、TXT/LRC 歌词后，可离线分离、识别、对齐与生成视频。
在线歌词检索和远程歌词修正 API 仍需联网。打包不包含用户密钥、内网地址、歌曲或个人设置。

Windows 10/11 x64，建议至少 16 GB 内存、25 GB 可用磁盘空间。
人声分离和单独的歌词对齐可使用受支持的 NVIDIA 显卡；识别与时间戳模型会同时加载，
显存不足 8 GiB 时语音识别自动使用 CPU。CPU 运行较慢。
MMS 权重遵循 CC-BY-NC-4.0，仅限非商业用途；模型卡、来源及其他许可证随包保留。

## 重现构建

使用已安装 AI 组件的 Python 3.12，在 Windows 上执行。
安装 Inno Setup 7（官方签名安装器），不修改原有 BAT 或用户配置。

```powershell
python packaging/prepare_offline_models.py
python packaging/build_windows.py --label 1.0.0-zh.YYYYMMDD `
  --existing-release C:/path/to/AutoKaraoke-windows-x64 `
  --runtime C:/path/to/AutoKaraoke-runtime `
  --iscc C:/path/to/InnoSetup/ISCC.exe `
  --output D:/builds/AutoKaraoke-unique-build --stage-only
```

`--runtime` 指包含 `python/` 和 `venv/` 的目录。模型下载固定修订版本并核对 SHA256。
构建从已提交的 Git 快照开始，拒绝覆盖已有构建目录或安装包。
`--stage-only` 先生成完整目录及 BAT ZIP，用于验收；通过后以相同参数追加 `--reuse-stage` 并移除 `--stage-only` 生成安装器。
`package-manifest.json` 记录对应源码提交和逐文件哈希。

## 离线验收

```powershell
AutoKaraoke.exe --self-test --report D:/checks/offline.json
AutoKaraoke.exe --self-test --report D:/checks/asr.json --inference asr
```

自检隔离应用配置，阻断测试进程的 socket 连接，检查自带依赖和所有模型文件，启动离屏中文界面。
`--inference` 可选 `asr`、`align`、`mms`、`separation`，使用合成静音做真实推理，不上传歌曲或调用付费 API。
实际歌曲效果、不同电脑的驱动兼容性需要在对应设备上验证，不能由合成音频结果代替。
