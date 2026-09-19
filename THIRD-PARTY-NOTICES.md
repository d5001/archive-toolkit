# 第三方组件声明 / Third-Party Notices

本项目（压缩包工具箱 / Archive Toolkit）在分发时包含以下第三方组件。

---

## 1. 7-Zip

`压缩包工具箱.exe` 内嵌了官方 **7-Zip** 的命令行引擎，具体为：

| 文件 | 说明 |
| --- | --- |
| `build/vendor/7zip/7z.exe` | 7-Zip 控制台程序 |
| `build/vendor/7zip/7z.dll` | 7-Zip 编解码动态库 |

- 项目主页：<https://www.7-zip.org/>
- 版权：Copyright (C) 1999-2026 Igor Pavlov
- 版本：26.03
- 获取来源：<https://github.com/ip7z/7zip/releases/tag/26.03>（官方发布）
- 完整许可文本：[`build/vendor/7zip/License.txt`](build/vendor/7zip/License.txt)

### 授权说明

7-Zip 的大部分代码以 **GNU LGPL** 授权发布，部分代码以 **BSD 3-Clause** 授权，
另有 **unRAR 限制**条款。要点如下：

- LGPL 允许在满足条件的前提下随其它程序一起分发（本项目按原样分发未修改的官方二进制，
  并随附完整许可文本 `License.txt`，该文件同时被打包进 exe 内部）。
- **unRAR 限制**：7-Zip 中的 RAR 解压代码不得用于开发 RAR 压缩程序，
  也不得以任何形式重新分发该 RAR 代码。本项目仅把 `7z.exe` 作为**独立进程**调用，
  未修改、未链接、未重新分发其 RAR 代码，仅使用其读取/解压能力。

本项目源码本身不包含、也不修改 7-Zip 的任何代码，两者是聚合（aggregation）关系。

---

## 2. Python 与 Tkinter

程序由 Python 编写，使用 Python 标准库（`tkinter`、`subprocess`、`hashlib`、`json` 等），
打包时使用 [PyInstaller](https://pyinstaller.org/)。

- Python 采用 PSF License
- Tcl/Tk 采用 BSD 风格许可
- PyInstaller 采用 GPL 2.0 及例外的 bootloader 条款（仅用于构建，不改变本程序的授权）

---

## 3. Tcl/Tk 运行时

由 PyInstaller 一并打包的 Tcl/Tk 运行时（`tcl86t.dll`、`tk86t.dll` 等）
归 Tcl/Tk 项目所有，采用 BSD 风格许可。
