# -*- coding: utf-8 -*-
"""把 archive_toolkit.py + 7-Zip 引擎打包成单文件 exe。"""

import os
import sys
import time
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = "压缩包工具箱"
ENTRY = os.path.join(HERE, "archive_toolkit.py")
ICON = os.path.join(HERE, "icon.ico")
ENGINE_DIR = os.path.join(HERE, "vendor", "7zip")
WORK = os.path.join(HERE, "_pyi")
DIST = os.path.join(HERE, "_dist")
VERSION_FILE = os.path.join(HERE, "version_info.txt")

VERSION_TXT = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(2, 0, 0, 0),
    prodvers=(2, 0, 0, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '080404B0',
          [StringStruct('CompanyName', 'WorkBuddy'),
           StringStruct('FileDescription', '压缩包工具箱 - 打包/分卷/合并/解压'),
           StringStruct('FileVersion', '2.0.0.0'),
           StringStruct('InternalName', 'archive_toolkit'),
           StringStruct('OriginalFilename', '%s.exe'),
           StringStruct('ProductName', '压缩包工具箱'),
           StringStruct('ProductVersion', '2.0.0.0')])
      ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
""" % APP


def main():
    # 引擎就位检查
    for f in ("7z.exe", "7z.dll"):
        p = os.path.join(ENGINE_DIR, f)
        if not os.path.isfile(p):
            print("缺少引擎文件：%s（先运行 get_engine/手动准备）" % p)
            return 1

    # 打包时把 License 也带上（LGPL 合规）
    lic = os.path.join(ENGINE_DIR, "License.txt")
    if not os.path.isfile(lic):
        print("提示：未找到 7-Zip License.txt（不影响运行）")

    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(VERSION_TXT)

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        # 不用 --clean：它会批量删除 _pyi 缓存，可能被安全策略拦截
        "--onefile",
        "--noconsole",
        "--noupx",
        "--name", APP,
        "--icon", ICON,
        "--version-file", VERSION_FILE,
        "--distpath", DIST,
        "--workpath", WORK,
        "--specpath", WORK,
        "--add-binary", os.path.join(ENGINE_DIR, "7z.exe") + ";engine",
        "--add-binary", os.path.join(ENGINE_DIR, "7z.dll") + ";engine",
    ]
    if os.path.isfile(os.path.join(ENGINE_DIR, "License.txt")):
        args += ["--add-data", os.path.join(ENGINE_DIR, "License.txt") + ";engine"]
    args += [
        "--exclude-module", "unittest",
        "--exclude-module", "doctest",
        "--exclude-module", "pydoc_data",
        "--exclude-module", "lib2to3",
        "--exclude-module", "sqlite3",
        "--exclude-module", "setuptools",
        "--exclude-module", "pip",
        "--exclude-module", "test",
        "--exclude-module", "email",
        "--exclude-module", "xml",
        "--exclude-module", "http",
        "--exclude-module", "asyncio",
        "--exclude-module", "concurrent",
        ENTRY,
    ]
    print(" ".join(args))
    rc = subprocess.call(args, cwd=HERE)
    if rc != 0:
        print("BUILD FAILED rc=%d" % rc)
        return rc

    src = os.path.join(DIST, APP + ".exe")
    dst = os.path.join(ROOT, APP + ".exe")

    # 旧 exe 常被杀软/沙箱锁住导致无法覆盖：先改名腾位置（改名一般不被拦）
    olds = []
    if os.path.exists(dst):
        try:
            os.remove(dst)
        except OSError:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            old = "%s.old-%s" % (dst, stamp)
            try:
                os.rename(dst, old)
                olds.append(old)
                print("旧 exe 被占用，已改名占位：%s" % os.path.basename(old))
            except OSError as e:
                print("无法替换旧 exe：%s" % e)
                print("新 exe 已生成在：%s" % src)
                return 1

    try:
        shutil.copy2(src, dst)
    except OSError as e:
        print("复制失败：%s" % e)
        print("新 exe 已生成在：%s" % src)
        return 1

    for old in olds:                     # 尽力清理占位文件
        try:
            os.remove(old)
        except OSError:
            print("提示：%s 暂被占用，可稍后手动删除" % os.path.basename(old))

    print("EXE -> %s  (%.1f MB)" % (dst, os.path.getsize(dst) / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
