# -*- coding: utf-8 -*-
"""下载 7-Zip 官方安装包并提取独立的 7z.exe / 7z.dll 作为内嵌引擎。

7-Zip 是 LGPL 授权，允许随程序一起分发（见 License.txt）。
"""

import os
import sys
import shutil
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(HERE, "vendor")
OUTDIR = os.path.join(VENDOR, "7zip")
URL = "https://github.com/ip7z/7zip/releases/download/26.03/7z2603-x64.exe"
SIG = b"7z\xbc\xaf\x27\x1c"


def download(url, dest):
    print("downloading", url)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        total = 0
        while True:
            buf = r.read(1 << 16)
            if not buf:
                break
            f.write(buf)
            total += len(buf)
    print("downloaded %s bytes -> %s" % (total, dest))
    return dest


def main():
    os.makedirs(VENDOR, exist_ok=True)
    setup = os.path.join(VENDOR, "7z2603-x64.exe")
    if not os.path.isfile(setup):
        download(URL, setup)

    with open(setup, "rb") as f:
        blob = f.read()
    idx = blob.find(SIG)
    if idx < 0:
        print("ERROR: 7z signature not found in installer")
        return 1
    print("7z signature at offset", idx)
    inner = os.path.join(VENDOR, "7z2603-x64.inner.7z")
    with open(inner, "wb") as f:
        f.write(blob[idx:])

    import py7zr
    if os.path.isdir(OUTDIR):
        shutil.rmtree(OUTDIR)
    os.makedirs(OUTDIR, exist_ok=True)
    with py7zr.SevenZipFile(inner, mode="r") as z:
        names = z.getnames()
        print("archive contains %d entries" % len(names))
        want = [n for n in names if os.path.basename(n).lower() in
                ("7z.exe", "7z.dll", "license.txt", "7z.1", "history.txt")]
        print("extracting:", want)
        z.extract(path=OUTDIR, targets=want)

    for root, _dirs, files in os.walk(OUTDIR):
        for n in files:
            p = os.path.join(root, n)
            print("  %-60s %d bytes" % (os.path.relpath(p, OUTDIR),
                                        os.path.getsize(p)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
