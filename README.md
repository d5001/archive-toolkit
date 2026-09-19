# 压缩包工具箱 · Archive Toolkit

> 一个可以在 Windows 上**独立运行**的压缩包处理工具：打包、分卷、合并、解压。
> 单文件 exe，双击即用，**不依赖 Python、7-Zip 或任何运行库**——压缩引擎已内嵌在程序里。

![platform](https://img.shields.io/badge/platform-Windows%207%2F10%2F11%20x64-1a5fb4)
![engine](https://img.shields.io/badge/engine-7--Zip%2026.03-2f6f4f)
![python](https://img.shields.io/badge/python-stdlib%20%2B%20tkinter-3572A5)
![license](https://img.shields.io/badge/license-see%20notice-lightgrey)

---

## 功能

| 页签 | 能力 |
| --- | --- |
| **打包压缩** | 压缩成 `7z` / `zip` / `tar` / `tar.gz` / `tar.bz2` / `tar.xz`；级别 0–9；**可边压缩边分卷**；支持密码（7z 可加密文件名）、固实压缩、多线程 |
| **分卷切割** | 把已有的任意文件（zip / rar / 7z / iso / 视频 / 备份…）按大小或份数切成 `.001 / .002 …`；**纯字节切割不重新压缩**，速度等于磁盘拷贝速度；支持 SHA-256 清单 |
| **合并还原** | 选 `.001` 自动找齐后续分卷并还原，自动与清单中的 SHA-256 比对，可合并后删卷 |
| **解压解包** | 解压 `7z / zip / rar / iso / cab / tar / gz / bz2 / xz / arj / lzh …`；**可直接选 `.001` 多卷**；密码；覆盖/跳过/改名；完整性测试 |
| **查看内容** | 列出压缩包内文件清单、原始大小、压缩后大小、压缩率、时间 |

其他：拖入式路径输入、实时进度（速度 / 剩余时间）、任务可取消、运行日志、中文路径完整支持、高分屏 DPI 适配、命令行模式。

---

## 快速开始

1. 下载本仓库根目录的 **`压缩包工具箱.exe`**（约 12 MB）
2. 双击运行。首次启动可能会略慢（自解压引擎到临时目录）
3. 不需要安装任何东西

> 杀毒软件可能会误报单文件 exe（PyInstaller 打包的常见现象）。
> 源码就在 `build/` 里，全部使用 Python 标准库，可自行审阅或重新编译。

---

## 关于「最大 4GB」分卷

本项目约定 **1 GB = 1024 MB = 1,073,741,824 字节**，所以：

```
「最大 4GB」= 4 × 1073741824 = 4,294,967,296 字节
```

「分卷切割」页签点一下 **`4GB`** 即可。

### 如果要传网盘，请用 `3.7GB` 预设

百度网盘**非会员**用 PC 客户端上传时单文件上限 **4GB**，**网页端只有 1~2GB**
（会员 10GB，超级会员 20GB）。

而 `4GiB = 4,294,967,296 > 4,000,000,000`——如果网盘按十进制口径卡 4GB，4GB 分卷一样会超。
所以工具里内置了 **`3.7GB`＝3,972,929,536 字节** 的「网盘安全」预设，两种口径下都不会超限。

---

## 分卷与合并

分卷命名遵循通用约定，`7-Zip` / `WinRAR` / `HJSplit` 都能识别：

```
IdeaProjects.zip.001
IdeaProjects.zip.002
IdeaProjects.zip.003
```

输出目录会额外生成：

- `*.splitinfo.json` —— 记录原始文件名、大小、每卷字节数、SHA-256
- `合并还原_*.bat` —— 给没有本工具的人用，双击即可用 `copy /b` 合并

### 没有本工具时怎么合并

```cmd
:: Windows
copy /b IdeaProjects.zip.001+IdeaProjects.zip.002+IdeaProjects.zip.003 IdeaProjects.zip
```

```bash
# Linux / macOS
cat IdeaProjects.zip.00* > IdeaProjects.zip
```

也可以直接用 **7-Zip / WinRAR 右键点 `.001` → 提取**，会自动读取后续分卷，不必先合并。

---

## 命令行模式

```bat
:: 查看全部命令
压缩包工具箱.exe --cli --help

:: 打包并分卷
压缩包工具箱.exe --cli pack "D:\data" -o "D:\out\data.7z" -f 7z --volume 4GB

:: 切割已有文件（最大 4GB）
压缩包工具箱.exe --cli split "D:\IdeaProjects.zip" --outdir "D:\out" --size 4GB --hash

:: 合并还原（自动校验 SHA-256）
压缩包工具箱.exe --cli merge "D:\out\IdeaProjects.zip.001"

:: 解压（可直接吃 .001 多卷）
压缩包工具箱.exe --cli extract "D:\out\data.7z.001" -o "D:\restore"

:: 查看内容 / 测试完整性 / 自检
压缩包工具箱.exe --cli list "D:\out\data.7z"
压缩包工具箱.exe --cli test "D:\out\data.7z"
压缩包工具箱.exe --cli selftest
```

单位规则：纯数字小于 10000 按 GB 解释（`4` = 4GB），大于等于 10000 按字节解释
（`4000000000` = 40 亿字节），带 `B` 后缀恒为字节。

---

## 常见问题

### 合并回来的 zip，双击提示「Windows 无法打开文件夹。压缩(zipped)文件夹无效。」

**文件没坏，这是 Windows 自带解压的先天限制。**

Windows 资源管理器内置的解压（`zipfldr.dll`）只实现了 ZIP 2.0 规范，**不支持 ZIP64**。
只要满足任意一条，zip 就必须使用 ZIP64：

- 总大小超过 4 GB
- 内部文件数超过 65,535 个

例如一个 11.7 GB、含 212,696 个条目的 zip，**原始文件直接双击也是同样的报错**，
这和分卷、合并没有任何关系。

正确的打开方式：

1. 本工具的「解压解包」页签（直接选 `.001` 或合并后的 `.zip`）
2. 7-Zip / WinRAR / Bandizip 右键解压
3. PowerShell（.NET 支持 ZIP64）：

   ```powershell
   Expand-Archive -Path "IdeaProjects.zip" -DestinationPath "D:\out"
   ```

本工具在合并出 ZIP64 大包后会自动弹出该提示。

### `.001` 用 Windows 自带解压打不开？

`.001` 是分卷，需要先合并，见上文。若 `.001` 本身是 7z/zip 的**原生分卷**，
用「解压解包」直接选它就能解压，不需要先合并。

### 能创建 rar 吗？

受 RAR 授权限制，任何第三方工具都不能创建 rar。但本工具可以正常**读取和解压** rar。

### 分卷总大小会比原文件大吗？

不会。分卷是纯字节切割，总大小与原文件完全相同。

---

## 目录结构

```
压缩包工具箱.exe          编译好的单文件程序
使用说明.txt              详细使用说明（功能 / 命令行 / FAQ）
build/
  archive_toolkit.py      全部源码：GUI（tkinter）+ 命令行 + 7z 引擎封装
  build_exe.py            PyInstaller 打包脚本
  get_engine.py           下载并提取官方 7-Zip 引擎
  make_icon.py            纯标准库生成 icon.ico
  smoke_gui.py            GUI 冒烟测试（自动构建界面并关闭）
  version_info.txt        exe 版本资源
  vendor/7zip/            内嵌的 7-Zip 引擎（7z.exe / 7z.dll / License.txt）
```

---

## 从源码构建

需要 Python 3.8+（**必须带 tkinter**）和网络。

```bat
:: 1) 准备环境
python -m venv venv
venv\Scripts\python -m pip install --no-cache-dir pyinstaller

:: 2) 下载 7-Zip 引擎（会自动到 vendor/7zip/）
venv\Scripts\python build\get_engine.py

:: 3) 打包
venv\Scripts\python build\build_exe.py
```

产物在 `build/_dist/`，同时会复制一份到仓库根目录。

也可以不打包，直接运行源码：

```bat
python build\archive_toolkit.py            :: 图形界面
python build\archive_toolkit.py --cli ...  :: 命令行
```

---

## 第三方组件

`压缩包工具箱.exe` 内嵌了官方 **7-Zip 26.03** 的控制台引擎（`7z.exe` + `7z.dll`）。
7-Zip 采用 GNU LGPL 授权，允许随程序分发，完整许可文本见
`build/vendor/7zip/License.txt`。详见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。

本仓库中的程序源码为作者原创。
