# -*- coding: utf-8 -*-
"""
压缩包工具箱  Archive Toolkit  v2.0
=====================================
一个可独立运行的压缩包处理软件（Windows 单文件 exe，双击即用）。

功能
----
  1. 打包压缩 : 把文件/文件夹压缩成 7z / zip / tar / tar.gz / tar.bz2 / tar.xz，
                可选“边压缩边分卷”（如每卷 4GB），支持密码。
  2. 分卷切割 : 把已有的任意压缩包（zip/rar/7z/iso…）按指定大小切成 N 个分卷
                （file.zip.001 / .002 …），不重新压缩，速度极快。
  3. 合并还原 : 把 .001 分卷合并成原文件，可与清单中的 SHA-256 比对校验。
  4. 解压解包 : 解压 7z/zip/rar/iso/tar/gz/bz2/xz… 压缩包，可直接吃 .001 多卷。
  5. 查看内容 : 读取压缩包内的文件清单、大小、压缩率。

引擎
----
  内置官方 7-Zip 控制台引擎（LGPL，随程序分发），无需用户安装任何东西。

命令行模式
----------
  压缩包工具箱.exe --cli <命令> ...
  见 `--cli --help`
"""

import os
import re
import sys
import json
import math
import time
import shutil
import hashlib
import tempfile
import threading
import subprocess
import traceback
import datetime
import argparse

APP_NAME = "压缩包工具箱"
APP_VERSION = "2.0"
APP_TITLE = "%s v%s" % (APP_NAME, APP_VERSION)

IS_WIN = (os.name == "nt")
CREATE_NO_WINDOW = 0x08000000 if IS_WIN else 0

KB, MB, GB, TB = 1024, 1024 ** 2, 1024 ** 3, 1024 ** 4
BUFSIZE = 8 * 1024 * 1024
MANIFEST_SUFFIX = ".splitinfo.json"
PART_RE = re.compile(r"^(?P<base>.+)\.(?P<num>\d{2,6})$")
PCT_RE = re.compile(r"^\s*(\d{1,3})%\s*(?:\d*)\s*([-+]?)\s*(.*)$")

# 分卷大小预设。百度网盘非会员用 PC 客户端上传时单文件上限 4GB（网页端只有
# 1~2GB）。「4GB」是临界值容易被拒；且 4,294,967,296 > 4,000,000,000，
# 若网盘按十进制口径卡 4GB 仍会超限。故安全档取 3.7GiB = 3,972,929,536 字节，
# 在「4GiB」和「40亿字节」两种口径下都安全。
VOLUME_PRESETS = {
    "不分卷": None,
    "512MB": 512 * MB,
    "1GB": GB,
    "2GB": 2 * GB,
    "3.7GB（网盘安全）": int(3.7 * GB),
    "4GB": 4 * GB,
    "自定义...": "custom",
}
# 分卷切割页签的一键预设：(按钮文字, 填入输入框的值)
SPLIT_PRESETS = [("4GB", "4GB"), ("3.7GB 网盘", "3.7GB"), ("2GB", "2GB"),
                 ("1GB", "1GB"), ("512MB", "512MB")]


# ================================================================ 异常

class Cancelled(Exception):
    """用户取消"""


class ToolError(Exception):
    """可预期的业务错误，直接展示给用户"""


# ================================================================ 引擎定位

def _engine_candidates():
    cands = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cands.append(os.path.join(meipass, "engine"))
    here = os.path.dirname(os.path.abspath(__file__))
    cands.append(os.path.join(here, "vendor", "7zip"))
    cands.append(os.path.join(os.path.dirname(here), "vendor", "7zip"))
    if IS_WIN:
        for pf in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                   os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            cands.append(os.path.join(pf, "7-Zip"))
    return cands


def find_engine():
    exe = "7z.exe" if IS_WIN else "7z"
    for d in _engine_candidates():
        p = os.path.join(d, exe)
        if os.path.isfile(p):
            return p
    which = shutil.which("7z") or shutil.which("7za") or shutil.which("7zr")
    return which


ENGINE_PATH = None


def engine():
    global ENGINE_PATH
    if ENGINE_PATH is None:
        ENGINE_PATH = find_engine()
    return ENGINE_PATH


def require_engine():
    p = engine()
    if not p:
        raise ToolError("未找到 7-Zip 引擎（7z.exe）。\n"
                        "请把 7z.exe 与 7z.dll 放在程序目录的 engine\\ 下，"
                        "或安装 7-Zip。")
    return p


# ================================================================ 通用工具

def fmt_bytes(n, digits=2):
    if n is None:
        return "-"
    if isinstance(n, str):
        return n
    neg = n < 0
    v = float(abs(n))
    idx = 0
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    while v >= 1024.0 and idx < len(units) - 1:
        v /= 1024.0
        idx += 1
    s = ("%d %s" % (v, units[idx])) if idx == 0 else ("%.*f %s" % (digits, v, units[idx]))
    return ("-" + s) if neg else s


def fmt_seconds(sec):
    if sec is None or sec < 0 or sec != sec or math.isinf(sec):
        return "--:--"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return ("%d:%02d:%02d" % (h, m, s)) if h else ("%02d:%02d" % (m, s))


def parse_size(text, default_unit=GB):
    """'4GB' / '4G' / '4096MB' / '3.9GB' / '4000000000B' / '4' -> 字节数。

    单位按 1024 进制：1 GB = 1024 MB = 1073741824 B。
    纯数字：小于 10000 时按 default_unit 解释（'4' = 4GB），
    大于等于 10000 时按字节解释（'4000000000' = 40 亿字节），
    避免把超大字节数误当成 GB。
    """
    if text is None:
        raise ToolError("分卷大小不能为空")
    if isinstance(text, (int, float)):
        v = int(text)
        if v <= 0:
            raise ToolError("分卷大小必须大于 0")
        return v
    s = str(text).strip().upper().replace(" ", "")
    if not s:
        raise ToolError("分卷大小不能为空")
    m = re.match(r"^([0-9]*\.?[0-9]+)\s*([A-Z]*)$", s)
    if not m:
        raise ToolError("无法识别的分卷大小：%s（示例：4GB / 3.7GB / 4096MB / "
                        "4000000000B）" % text)
    num = float(m.group(1))
    unit = m.group(2)
    if unit == "":
        mult = 1 if num >= 10000 else default_unit
    else:
        mult = {"B": 1,
                "K": KB, "KB": KB, "KIB": KB,
                "M": MB, "MB": MB, "MIB": MB,
                "G": GB, "GB": GB, "GIB": GB,
                "T": TB, "TB": TB, "TIB": TB}.get(unit)
    if mult is None:
        raise ToolError("不支持的单位：%s" % unit)
    val = int(round(num * mult))
    if val <= 0:
        raise ToolError("分卷大小必须大于 0")
    return val


def plan_parts(total, part_size=None, part_count=None):
    if total <= 0:
        raise ToolError("源文件是空文件，无法分割")
    if part_count:
        if part_count <= 0:
            raise ToolError("分卷数量必须大于 0")
        part_size = int(math.ceil(total / float(min(part_count, total))))
    if not part_size or part_size <= 0:
        raise ToolError("分卷大小必须大于 0")
    count = int(math.ceil(total / float(part_size)))
    sizes, remain = [], total
    for i in range(1, count + 1):
        cur = min(part_size, remain)
        sizes.append((i, cur))
        remain -= cur
    return part_size, sizes


def part_path(outdir, base, index, width=3):
    return os.path.join(outdir, "%s.%0*d" % (base, width, index))


def find_parts(any_part):
    d, name = os.path.split(os.path.abspath(any_part))
    m = PART_RE.match(name)
    if not m:
        raise ToolError("文件名不符合分卷规则：%s\n应为 xxx.001 这种三位以上数字后缀" % name)
    base, width = m.group("base"), len(m.group("num"))
    parts, i = [], 1
    while i <= 999999:
        p = part_path(d, base, i, width)
        if not os.path.isfile(p):
            break
        parts.append(p)
        i += 1
    if not parts:
        raise ToolError("找不到任何分卷文件")
    return base, parts


def sha256_file(path, ctx=None, base_done=0, total=None):
    h = hashlib.sha256()
    done = 0
    with open(path, "rb") as f:
        while True:
            if ctx:
                ctx.check()
            buf = f.read(4 * BUFSIZE)
            if not buf:
                break
            h.update(buf)
            done += len(buf)
            if ctx and total:
                ctx.progress((base_done + done) * 100.0 / total)
    return h.hexdigest()


def zip_is_zip64(path):
    """判断 zip 是否用了 ZIP64 扩展（体积 >4GB 或条目数 >65535 时必然使用）。

    Windows 资源管理器的内置解压（zipfldr.dll）只实现 ZIP 2.0，
    打不开 ZIP64 包，双击会提示“压缩(zipped)文件夹无效”。
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - 65536))
            tail = f.read()
        return tail.find(b"PK\x06\x06") >= 0
    except OSError:
        return False


ZIP64_HINT = (
    "这是 ZIP64 大压缩包（超过 4GB，或内部文件数超过 65535 个）。\n"
    "Windows 自带的「压缩(zipped)文件夹」只支持 ZIP 2.0，打不开它，\n"
    "双击会提示“压缩(zipped)文件夹无效”——这是系统限制，不是文件损坏。\n"
    "请改用：本工具「解压解包」页签 / 7-Zip / WinRAR / Bandizip。"
)


def open_dir(path):
    try:
        if path and os.path.isdir(path):
            if IS_WIN:
                os.startfile(path)          # noqa: S606
            else:
                subprocess.Popen(["xdg-open", path])
    except Exception:                        # noqa: BLE001
        pass


# ================================================================ 7-Zip 封装

class Ctx(object):
    """任务上下文：日志 + 进度 + 取消。"""

    def __init__(self, log=None, progress=None, cancel=None):
        self._log = log or (lambda m: None)
        self._progress = progress or (lambda p, t=None: None)
        self.cancel = cancel or threading.Event()

    def log(self, msg):
        self._log(msg)

    def progress(self, pct, text=None):
        self._progress(max(0.0, min(100.0, pct)), text)

    def check(self):
        if self.cancel.is_set():
            raise Cancelled()

    def cancelled(self):
        return self.cancel.is_set()


def _decode_console(v):
    if not v:
        return ""
    return v.decode("utf-8", "replace")


def run_engine(args, ctx=None, cwd=None, engine_arg0=True):
    """运行 7z 命令，解析进度，返回 (rc, err_text)。"""
    exe = require_engine()
    cmd = ([exe] + list(args)) if engine_arg0 else list(args)
    creationflags = CREATE_NO_WINDOW if IS_WIN else 0

    proc = subprocess.Popen(
        cmd, cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=creationflags)

    err_chunks = []

    def drain_err():
        try:
            while True:
                b = proc.stderr.read(4096)
                if not b:
                    break
                err_chunks.append(_decode_console(b))
        except Exception:                    # noqa: BLE001
            pass

    t_err = threading.Thread(target=drain_err, daemon=True)
    t_err.start()

    last_item = None
    buf = b""
    fd = proc.stdout.fileno()
    try:
        while True:
            if ctx and ctx.cancelled():
                try:
                    proc.kill()
                except Exception:            # noqa: BLE001
                    pass
                break
            try:
                data = os.read(fd, 8192)
            except OSError:
                break
            if not data:
                break
            buf += data
            while True:
                i_r, i_n = buf.find(b"\r"), buf.find(b"\n")
                cands = [x for x in (i_r, i_n) if x >= 0]
                if not cands:
                    break
                i = min(cands)
                raw, buf = buf[:i], buf[i + 1:]
                line = _decode_console(raw).strip()
                if not line or ctx is None:
                    continue
                pct = PCT_RE.match(line)
                if pct:
                    ctx.progress(float(pct.group(1)))
                    item = (pct.group(3) or "").strip()
                    if item and item != last_item and not item.lower().startswith("scan"):
                        last_item = item
                        ctx.log("   " + item)
                else:
                    if not re.match(r"^\s*\d+\s*M\s+Scan", line):
                        ctx.log("   " + line)
    finally:
        try:
            proc.stdout.close()
        except Exception:                    # noqa: BLE001
            pass
        t_err.join(timeout=3)
        rc = proc.wait()

    err_text = "".join(err_chunks).strip()
    if ctx and ctx.cancelled():
        raise Cancelled()
    return rc, err_text


def _clean7z(args):
    """公共参数：UTF-8 输出、禁止交互、不覆盖询问。"""
    return ["-y", "-sccUTF-8"] + list(args)


def ar_version():
    exe = engine()
    if not exe:
        return None
    try:
        r = subprocess.run([exe], capture_output=True, creationflags=CREATE_NO_WINDOW)
        txt = _decode_console(r.stdout)
        m = re.search(r"7-Zip[^\n]*", txt)
        return m.group(0).strip() if m else "7-Zip"
    except Exception:                        # noqa: BLE001
        return None


def ar_list(archive, password=None):
    """解析 7z l -slt 输出，返回 (归档信息dict, 条目列表)。

    -slt 的格式是「空行分块 + Key = Value」。
    只有「----------」分隔线之后的块才是真实条目；之前的都是头部信息。
    对 .001 分卷，头部会嵌套三层（Split 容器 → 内层容器 → 真正的归档头），
    取最后带 Type 的那块，并保留 Volumes 等分卷信息。
    """
    if not os.path.isfile(archive):
        raise ToolError("找不到压缩包：%s" % archive)
    args = ["l", "-slt", archive]
    if password:
        args.append("-p" + password)
    exe = require_engine()
    r = subprocess.run([exe] + _clean7z(args), capture_output=True,
                       creationflags=CREATE_NO_WINDOW)
    if r.returncode != 0:
        msg = (_decode_console(r.stderr).strip()
               or _decode_console(r.stdout).strip())
        raise ToolError("读取压缩包失败：\n%s" % (msg or "未知错误"))
    text = _decode_console(r.stdout)

    kv_re = re.compile(r"^([A-Za-z][A-Za-z0-9 ]*?) = (.*)$")

    info, entries = {}, []
    block, in_entries = {}, False

    def flush():
        nonlocal block
        if not block:
            return
        if "Path" in block:
            if in_entries:
                entries.append(block)
            elif "Type" in block:
                info.update(block)
        block = {}

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        if line.startswith("----------"):
            flush()
            in_entries = True
            continue
        m = kv_re.match(line)
        if m:
            block[m.group(1)] = m.group(2)
        else:
            flush()
    flush()

    for e in entries:
        e["_size"] = int(e.get("Size") or 0)
        e["_packed"] = int(e.get("Packed Size") or 0)
        e["_is_dir"] = (e.get("Folder") == "+"
                        or e.get("Attributes", "").upper().startswith("D"))
    return info, entries


def ar_extract(archive, outdir, password=None, overwrite="overwrite",
               ctx=None, flat=False, test_only=False):
    if not os.path.isfile(archive):
        raise ToolError("找不到压缩包：%s" % archive)
    if not test_only:
        os.makedirs(outdir, exist_ok=True)
    cmd = "t" if test_only else "e" if flat else "x"
    args = [cmd, archive, "-bsp1", "-bso0", "-bb0"]
    if not test_only:
        args += ["-o" + outdir]
    args.append({"-aoa": "-aoa", "-aos": "-aos", "-aou": "-aou",
                 "overwrite": "-aoa", "skip": "-aos", "rename": "-aou"}
                .get(overwrite, "-aoa"))
    if password:
        args.append("-p" + password)
    rc, err = run_engine(_clean7z(args), ctx=ctx)
    if rc != 0:
        raise ToolError(err or "解压失败（返回码 %d）" % rc)
    return True


FORMATS = {
    "7z":     {"ext": ".7z",     "type": "7z",    "multi": True},
    "zip":    {"ext": ".zip",    "type": "zip",   "multi": True},
    "tar":    {"ext": ".tar",    "type": "tar",   "multi": True},
    "tar.gz": {"ext": ".tar.gz", "type": "gzip",  "multi": False},
    "tar.bz2": {"ext": ".tar.bz2", "type": "bzip2", "multi": False},
    "tar.xz": {"ext": ".tar.xz", "type": "xz",    "multi": False},
}


def ar_create(target, sources, fmt="7z", level=5, password=None,
              volume=None, threads=0, ctx=None, solid=True):
    """创建压缩包（可选分卷）。返回生成的文件列表。"""
    if not sources:
        raise ToolError("请先添加要压缩的文件或文件夹")
    sources = [os.path.abspath(os.path.normpath(s)) for s in sources]
    for s in sources:
        if not os.path.exists(s):
            raise ToolError("路径不存在：%s" % s)

    cfg = FORMATS.get(fmt)
    if not cfg:
        raise ToolError("不支持的格式：%s" % fmt)

    target = os.path.abspath(target)
    outdir = os.path.dirname(target)
    if outdir:
        os.makedirs(outdir, exist_ok=True)

    common = ["-mx%d" % int(level), "-bsp1", "-bso0", "-bb0"]
    if threads:
        common.append("-mmt=%d" % int(threads))
    if password:
        common.append("-p" + password)
        if fmt == "7z" and solid:
            common.append("-mhe=on")
    vol = ["-v%d" % int(volume)] if volume else []

    if fmt in ("tar.gz", "tar.bz2", "tar.xz"):
        tmp_tar = target + ".tmp-%d.tar" % int(time.time() * 1000 % 100000)
        if ctx:
            ctx.log("步骤 1/2：打包为 tar ...")
        rc, err = run_engine(_clean7z(["a", "-ttar", tmp_tar] + list(sources) + common),
                             ctx=ctx, cwd=outdir)
        if rc != 0:
            _safe_remove(tmp_tar)
            raise ToolError(err or "tar 打包失败")
        if ctx:
            ctx.log("步骤 2/2：压缩为 %s ..." % cfg["type"])
        args = ["a", "-t" + cfg["type"], target, tmp_tar, "-mx%d" % int(level),
                "-bsp1", "-bso0", "-bb0"] + vol
        if password:
            args.append("-p" + password)
        rc, err = run_engine(_clean7z(args), ctx=ctx, cwd=outdir)
        _safe_remove(tmp_tar)
        if rc != 0:
            raise ToolError(err or "压缩失败")
    else:
        args = ["a", "-t" + cfg["type"], target] + list(sources) + common + vol
        if fmt == "7z" and not solid:
            args.append("-ms=off")
        rc, err = run_engine(_clean7z(args), ctx=ctx, cwd=outdir)
        if rc != 0:
            raise ToolError(err or "压缩失败")

    made = []
    if volume:
        i = 1
        while True:
            p = "%s.%03d" % (target, i)
            if not os.path.isfile(p):
                break
            made.append(p)
            i += 1
    if os.path.isfile(target):
        made.insert(0, target)
    return made


def _safe_remove(p):
    try:
        if os.path.isfile(p):
            os.remove(p)
    except OSError:
        pass


# ================================================================ 字节分卷

def split_bytes(src, outdir, part_size=None, part_count=None, ctx=None,
                do_hash=False, make_bat=True, overwrite=False):
    src = os.path.abspath(src)
    outdir = os.path.abspath(outdir) if outdir else os.path.dirname(src)
    if not os.path.isfile(src):
        raise ToolError("源文件不存在：%s" % src)

    total = os.path.getsize(src)
    base = os.path.basename(src)
    os.makedirs(outdir, exist_ok=True)

    part_size, sizes = plan_parts(total, part_size, part_count)
    n = len(sizes)
    targets = [part_path(outdir, base, i) for i, _ in sizes]

    if not overwrite:
        exist = [p for p in targets if os.path.exists(p)]
        if exist:
            raise ToolError("目标分卷已存在（%d 个）：\n%s\n\n请勾选“覆盖已有分卷”"
                            % (len(exist), exist[0]))

    if ctx:
        ctx.log("源文件   : %s" % src)
        ctx.log("文件大小 : %s (%d 字节)" % (fmt_bytes(total), total))
        ctx.log("分卷大小 : %s (%d 字节)" % (fmt_bytes(part_size), part_size))
        ctx.log("分卷数量 : %d" % n)
        ctx.log("-" * 62)

    h = hashlib.sha256() if do_hash else None
    done_total = 0
    t0 = time.time()
    started = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for i, size in sizes:
        target = part_path(outdir, base, i)
        written = 0
        with open(src, "rb") as fin:
            fin.seek(sum(s for _, s in sizes[:i - 1]))
            with open(target, "wb") as fout:
                while written < size:
                    if ctx:
                        ctx.check()
                    want = min(BUFSIZE, size - written)
                    buf = fin.read(want)
                    if not buf:
                        raise ToolError("读取源文件时提前结束（文件被改动？）")
                    fout.write(buf)
                    written += len(buf)
                    done_total += len(buf)
                    if h is not None:
                        h.update(buf)
                    if ctx:
                        el = max(time.time() - t0, 1e-6)
                        sp = done_total / el
                        eta = (total - done_total) / sp if sp > 0 else None
                        ctx.progress(done_total * 100.0 / total,
                                     "写入 %s · %s/s · 剩余 %s"
                                     % (os.path.basename(target), fmt_bytes(sp),
                                        fmt_seconds(eta)))
        if ctx:
            ctx.log("  [%2d/%2d] %-40s %s"
                    % (i, n, os.path.basename(target), fmt_bytes(written)))

    elapsed = time.time() - t0
    digest = h.hexdigest() if h is not None else None

    manifest = {
        "tool": APP_TITLE, "created": started,
        "original_name": base, "original_path": src, "original_size": total,
        "part_size": part_size, "part_count": n,
        "part_pattern": "%s.%%03d" % base,
        "part_sizes": [s for _, s in sizes],
        "sha256": digest, "hash_algorithm": "sha256" if digest else None,
    }
    mpath = os.path.join(outdir, base + MANIFEST_SUFFIX)
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    if ctx:
        ctx.log("清单文件 : %s" % os.path.basename(mpath))

    bat = None
    if make_bat:
        bat = os.path.join(outdir, "合并还原_%s.bat" % base)
        try:
            seq = '"+"'.join(["%s.%03d" % (base, i) for i in range(1, n + 1)])
            with open(bat, "w", encoding="utf-8", newline="\r\n") as f:
                f.write("\r\n".join([
                    "@echo off", "chcp 65001 >nul", 'cd /d "%~dp0"',
                    "echo 正在合并分卷 ...",
                    'copy /b "%s" "%s"' % (seq, base),
                    "echo.", "echo 合并完成：%s" % base, "pause"]) + "\r\n")
            if ctx:
                ctx.log("还原脚本 : %s" % os.path.basename(bat))
        except Exception:                    # noqa: BLE001
            bat = None

    if ctx:
        ctx.log("-" * 62)
        ctx.log("完成！%d 个分卷，用时 %s，平均 %s/s"
                % (n, fmt_seconds(elapsed), fmt_bytes(done_total / max(elapsed, 1e-6))))
        if digest:
            ctx.log("SHA-256  : %s" % digest)

    return {"part_count": n, "part_size": part_size, "parts": targets,
            "outdir": outdir, "manifest": mpath, "bat": bat, "sha256": digest,
            "elapsed": elapsed}


def merge_bytes(first_part, out_path=None, ctx=None, verify=True,
                overwrite=False, delete_after=False):
    base, parts = find_parts(first_part)
    outdir = os.path.dirname(parts[0])
    if not out_path:
        out_path = os.path.join(outdir, base)
    out_path = os.path.abspath(out_path)

    sizes = [os.path.getsize(p) for p in parts]
    total = sum(sizes)

    if ctx:
        ctx.log("分卷数量 : %d" % len(parts))
        for p, s in zip(parts, sizes):
            ctx.log("   %-45s %s" % (os.path.basename(p), fmt_bytes(s)))
        ctx.log("还原大小 : %s (%d 字节)" % (fmt_bytes(total), total))
        ctx.log("-" * 62)
        if len(sizes) > 2 and any(s != sizes[0] for s in sizes[:-1]):
            ctx.log("警告：中间分卷大小不一致，可能分卷不完整！")

    if os.path.exists(out_path) and not overwrite:
        raise ToolError("目标文件已存在：\n%s\n\n请勾选“覆盖已有文件”" % out_path)
    d = os.path.dirname(out_path)
    if d:
        os.makedirs(d, exist_ok=True)

    manifest = None
    mpath = os.path.join(outdir, base + MANIFEST_SUFFIX)
    if os.path.isfile(mpath):
        try:
            with open(mpath, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            if ctx:
                ctx.log("清单文件 : %s" % os.path.basename(mpath))
        except Exception:                    # noqa: BLE001
            manifest = None

    h = hashlib.sha256() if verify else None
    done = 0
    t0 = time.time()
    with open(out_path, "wb") as fout:
        for p in parts:
            with open(p, "rb") as fin:
                while True:
                    if ctx:
                        ctx.check()
                    buf = fin.read(BUFSIZE)
                    if not buf:
                        break
                    fout.write(buf)
                    done += len(buf)
                    if h is not None:
                        h.update(buf)
                    if ctx:
                        el = max(time.time() - t0, 1e-6)
                        sp = done / el
                        eta = (total - done) / sp if sp > 0 else None
                        ctx.progress(done * 100.0 / total,
                                     "%s · %s/s · 剩余 %s"
                                     % (os.path.basename(p), fmt_bytes(sp),
                                        fmt_seconds(eta)))

    out_size = os.path.getsize(out_path)
    digest = h.hexdigest() if h is not None else None
    expected = (manifest or {}).get("sha256")
    hash_match = None

    notice = None
    if out_path.lower().endswith(".zip") and zip_is_zip64(out_path):
        notice = ZIP64_HINT

    if ctx:
        ctx.log("-" * 62)
        ctx.log("已还原 : %s" % out_path)
        ctx.log("大小   : %s (%d 字节) %s"
                % (fmt_bytes(out_size), out_size,
                   "OK" if out_size == total else "不匹配！"))
        if digest:
            ctx.log("SHA-256: %s" % digest)
            if expected:
                hash_match = digest.lower() == expected.lower()
                ctx.log("校验   : %s" % ("与原文件哈希一致 √" if hash_match
                                         else "哈希不一致 ×"))
        ctx.log("用时   : %s" % fmt_seconds(time.time() - t0))
        if notice:
            ctx.log("")
            ctx.log("*" * 62)
            for ln in notice.splitlines():
                ctx.log("* " + ln)
            ctx.log("*" * 62)

    if delete_after:
        for p in parts:
            _safe_remove(p)
        if ctx:
            ctx.log("已删除 %d 个分卷" % len(parts))

    return {"output": out_path, "output_size": out_size, "expected_size": total,
            "sha256": digest, "expected_sha256": expected,
            "hash_match": hash_match, "size_match": out_size == total,
            "part_count": len(parts), "notice": notice}


# ================================================================ CLI

def run_cli(argv):
    argv = list(argv)
    if argv and argv[0] == "--cli":
        argv = argv[1:]
    p = argparse.ArgumentParser(prog="archive-toolkit",
                                description="%s —— 命令行模式" % APP_TITLE)
    sub = p.add_subparsers(dest="cmd")

    def add_report(sp):
        sp.add_argument("--report", default=None, help="结果写入 JSON 文件")

    sp = sub.add_parser("pack", help="打包压缩（可同时分卷）")
    sp.add_argument("inputs", nargs="+", help="要压缩的文件/文件夹")
    sp.add_argument("-o", "--output", required=True, help="输出压缩包路径")
    sp.add_argument("-f", "--format", default="7z", choices=list(FORMATS),
                    help="格式，默认 7z")
    sp.add_argument("-l", "--level", type=int, default=5, help="压缩级别 0-9")
    sp.add_argument("--volume", default=None, help="分卷大小，如 4GB")
    sp.add_argument("--password", default=None)
    sp.add_argument("--threads", type=int, default=0)
    add_report(sp)

    sp = sub.add_parser("split", help="把已有文件切成分卷")
    sp.add_argument("input")
    sp.add_argument("--outdir", default=None)
    sp.add_argument("--size", default=None, help="每卷大小，如 4GB")
    sp.add_argument("--count", type=int, default=None, help="按份数等分")
    sp.add_argument("--hash", action="store_true")
    sp.add_argument("--no-bat", action="store_true")
    sp.add_argument("--overwrite", action="store_true")
    add_report(sp)

    sp = sub.add_parser("merge", help="合并分卷")
    sp.add_argument("input", help="任意分卷，一般 .001")
    sp.add_argument("-o", "--output", default=None)
    sp.add_argument("--no-verify", action="store_true")
    sp.add_argument("--overwrite", action="store_true")
    sp.add_argument("--delete-parts", action="store_true")
    add_report(sp)

    sp = sub.add_parser("extract", help="解压（支持 .001 多卷）")
    sp.add_argument("input")
    sp.add_argument("-o", "--outdir", required=True)
    sp.add_argument("--password", default=None)
    sp.add_argument("--flat", action="store_true")
    add_report(sp)

    sp = sub.add_parser("list", help="列出压缩包内容")
    sp.add_argument("input")
    sp.add_argument("--password", default=None)
    add_report(sp)

    sp = sub.add_parser("test", help="测试压缩包完整性")
    sp.add_argument("input")
    sp.add_argument("--password", default=None)
    add_report(sp)

    sp = sub.add_parser("engine", help="显示引擎信息")
    add_report(sp)

    sp = sub.add_parser("selftest", help="界面自检（构建 GUI 并自动关闭）")
    add_report(sp)

    args = p.parse_args(argv)
    if not args.cmd:
        p.print_help()
        return 2

    def say(m):
        print(m, flush=True)

    ctx = Ctx(log=say, progress=lambda pct, text=None: None)
    try:
        if args.cmd == "engine":
            v = ar_version()
            say("引擎路径 : %s" % (engine() or "未找到"))
            say("引擎版本 : %s" % (v or "不可用"))
            say("支持格式 : %s" % ", ".join(FORMATS))
            res = {"ok": True, "engine": engine(), "version": v}

        elif args.cmd == "selftest":
            info = gui_selftest()
            say("界面自检 : %s" % ("通过" if info.get("ok") else "失败"))
            for k in ("title", "widgets", "geometry", "menu", "tk_version", "error"):
                if k in info:
                    say("  %-12s = %s" % (k, info[k]))
            res = dict(info)
            res["ok"] = bool(info.get("ok"))

        elif args.cmd == "pack":
            vol = parse_size(args.volume) if args.volume else None
            made = ar_create(args.output, args.inputs, fmt=args.format,
                             level=args.level, password=args.password,
                             volume=vol, threads=args.threads, ctx=ctx)
            for m in made:
                say("生成 : %s (%s)" % (m, fmt_bytes(os.path.getsize(m))))
            res = {"ok": True, "files": made}

        elif args.cmd == "split":
            res = split_bytes(args.input, args.outdir,
                              part_size=parse_size(args.size) if args.size else None,
                              part_count=args.count, ctx=ctx,
                              do_hash=args.hash, make_bat=not args.no_bat,
                              overwrite=args.overwrite)
            res["ok"] = True

        elif args.cmd == "merge":
            res = merge_bytes(args.input, args.output, ctx=ctx,
                              verify=not args.no_verify,
                              overwrite=args.overwrite,
                              delete_after=args.delete_parts)
            res["ok"] = bool(res["size_match"] and res["hash_match"] is not False)

        elif args.cmd == "extract":
            ar_extract(args.input, args.outdir, password=args.password,
                       ctx=ctx, flat=args.flat)
            res = {"ok": True, "outdir": args.outdir}

        elif args.cmd == "list":
            info, entries = ar_list(args.input, args.password)
            nfiles = len([e for e in entries if not e["_is_dir"]])
            ndirs = len(entries) - nfiles
            total = sum(e["_size"] for e in entries if not e["_is_dir"])
            packed = sum(e["_packed"] for e in entries if not e["_is_dir"])
            say("类型     : %s" % info.get("Type", "?"))
            say("条目总数 : %d（文件 %d，目录 %d）" % (len(entries), nfiles, ndirs))
            say("原始大小 : %s" % fmt_bytes(total))
            say("压缩后   : %s" % fmt_bytes(packed))
            for e in entries[:200]:
                if not e["_is_dir"]:
                    say("  %-12s %s" % (fmt_bytes(e["_size"]), e.get("Path", "")))
            if nfiles > 200:
                say("  ...（仅显示前 200 个文件）")
            res = {"ok": True, "type": info.get("Type"),
                   "count": len(entries), "file_count": nfiles,
                   "dir_count": ndirs, "total_size": total, "packed_size": packed,
                   "entries": entries[:5000],
                   "entries_truncated": len(entries) > 5000}

        elif args.cmd == "test":
            ar_extract(args.input, None, password=args.password, ctx=ctx, test_only=True)
            say("完整性测试通过")
            res = {"ok": True}

        else:
            return 2

        if getattr(args, "report", None):
            with open(args.report, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2, default=str)
        return 0

    except Cancelled:
        say("已取消。")
        return 130
    except ToolError as e:
        say("错误：%s" % e)
        if getattr(args, "report", None):
            with open(args.report, "w", encoding="utf-8") as f:
                json.dump({"ok": False, "error": str(e)}, f, ensure_ascii=False, indent=2)
        return 1
    except Exception as e:                   # noqa: BLE001
        say("未预期错误：%s" % e)
        traceback.print_exc()
        if getattr(args, "report", None):
            with open(args.report, "w", encoding="utf-8") as f:
                json.dump({"ok": False, "error": str(e)}, f, ensure_ascii=False, indent=2)
        return 1


def _attach_parent_console():
    if not IS_WIN:
        return
    try:
        import ctypes
        if not ctypes.windll.kernel32.AttachConsole(-1):
            return
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
    except Exception:                        # noqa: BLE001
        pass


# ================================================================ 图形界面

def gui_main():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    if IS_WIN:
        try:
            import ctypes
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:                # noqa: BLE001
                ctypes.windll.user32.SetProcessDPIAware()
        except Exception:                    # noqa: BLE001
            pass

    root = tk.Tk()
    root.title(APP_TITLE)
    try:
        root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
    except Exception:                        # noqa: BLE001
        pass

    ui_font = ("Microsoft YaHei UI", 9)
    bold_font = ("Microsoft YaHei UI", 9, "bold")
    mono_font = ("Consolas", 9)

    style = ttk.Style()
    for theme in ("vista", "winnative", "clam"):
        try:
            style.theme_use(theme)
            break
        except Exception:                    # noqa: BLE001
            continue
    style.configure(".", font=ui_font)
    style.configure("TLabelframe.Label", font=bold_font)
    style.configure("TNotebook.Tab", padding=(16, 7), font=ui_font)
    style.configure("Big.TButton", font=bold_font, padding=(10, 6))

    DEFAULT_FG = "#1f1f1f"
    MUTED_FG = "#5f6673"
    ACCENT = "#1a5fb4"

    # ------------------------------------------------ 公共状态
    state = {"busy": False, "cancel": threading.Event(), "last_outdir": ""}

    # ---------------- 顶部标题
    head = tk.Frame(root, background="#1a5fb4", height=58)
    head.pack(fill="x")
    head.pack_propagate(False)
    tk.Label(head, text=APP_NAME, background="#1a5fb4", foreground="white",
             font=("Microsoft YaHei UI", 15, "bold")).pack(side="left", padx=(16, 8))
    tk.Label(head, text="打包 · 分卷 · 合并 · 解压", background="#1a5fb4",
             foreground="#cfe0fa", font=("Microsoft YaHei UI", 9)).pack(side="left",
                                                                       pady=(6, 0))
    engine_lbl = tk.Label(head, text="", background="#1a5fb4", foreground="#cfe0fa",
                          font=("Microsoft YaHei UI", 8))
    engine_lbl.pack(side="right", padx=16)

    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=10, pady=(8, 0))

    # ------------------------------------------------ 通用组件工厂
    def labeled_entry(parent, row, label, var, width=None, browse=None,
                      browse_text="浏览...", rowspan=1):
        lb = ttk.Label(parent, text=label)
        lb.grid(row=row, column=0, sticky="w", pady=4, padx=(0, 6))
        ent = ttk.Entry(parent, textvariable=var, font=ui_font)
        ent.grid(row=row, column=1, sticky="ew", pady=4, rowspan=rowspan)
        if width:
            ent.configure(width=width)
        btn = None
        if browse:
            btn = ttk.Button(parent, text=browse_text, width=9, command=browse)
            btn.grid(row=row, column=2, sticky="w", padx=(6, 0), pady=4)
        return lb, ent, btn

    def section(parent, title, pad=10):
        f = ttk.LabelFrame(parent, text=" %s " % title, padding=pad)
        f.pack(fill="x", padx=4, pady=(6, 0))
        f.columnconfigure(1, weight=1)
        return f

    # 共享日志面板
    log_holder = ttk.LabelFrame(root, text=" 运行日志 ")
    log_holder.pack(fill="both", expand=False, padx=10, pady=(6, 0))
    log_txt = tk.Text(log_holder, height=8, wrap="none", font=mono_font,
                      background="#f7f8fa", foreground=DEFAULT_FG,
                      relief="flat", borderwidth=0, padx=8, pady=6)
    log_scr = ttk.Scrollbar(log_holder, orient="vertical", command=log_txt.yview)
    log_txt.configure(yscrollcommand=log_scr.set)
    log_scr.pack(side="right", fill="y")
    log_txt.pack(side="left", fill="both", expand=True)

    # 底部状态栏
    bar = ttk.Frame(root, padding=(10, 4, 10, 8))
    bar.pack(fill="x")
    prog = ttk.Progressbar(bar, mode="determinate", maximum=1000)
    prog.pack(fill="x")
    row = ttk.Frame(bar)
    row.pack(fill="x", pady=(4, 0))
    status_var = tk.StringVar(value="就绪")
    ttk.Label(row, textvariable=status_var, foreground=MUTED_FG).pack(side="left")
    cancel_btn = ttk.Button(row, text="取消当前任务", state="disabled",
                            command=lambda: state["cancel"].set())
    cancel_btn.pack(side="right")
    openout_btn = ttk.Button(
        row, text="打开输出目录",
        command=lambda: open_dir(state["last_outdir"])).pack(side="right", padx=6)
    ttk.Button(row, text="清空日志",
               command=lambda: log_txt.delete("1.0", "end")).pack(side="right")

    def ui_log(msg):
        log_txt.insert("end", msg + "\n")
        log_txt.see("end")

    def ui_progress(pct, text=None):
        prog.configure(value=int(pct * 10))
        if text:
            status_var.set("%s　%.1f%%" % (text, pct))

    def ui_clear():
        log_txt.delete("1.0", "end")
        prog.configure(value=0)

    # 全局 busy 控制
    widgets_to_lock = []

    def set_busy(busy):
        state["busy"] = busy
        st = "disabled" if busy else "normal"
        for w in widgets_to_lock:
            try:
                if isinstance(w, ttk.Combobox):
                    w.configure(state=("disabled" if busy else "readonly"))
                else:
                    w.configure(state=st)
            except Exception:                # noqa: BLE001
                pass
        try:
            cancel_btn.configure(state=("normal" if busy else "disabled"))
        except Exception:                    # noqa: BLE001
            pass

    def run_async(worker, on_success, what="任务"):
        if state["busy"]:
            return
        ui_clear()
        state["cancel"].clear()
        set_busy(True)
        prog.configure(value=0)
        status_var.set("正在%s ..." % what)

        ctx = Ctx(log=lambda m: root.after(0, lambda m=m: ui_log(m)),
                  progress=lambda p, t=None: root.after(0, lambda p=p, t=t: ui_progress(p, t)),
                  cancel=state["cancel"])

        def done(res):
            set_busy(False)
            prog.configure(value=1000)
            status_var.set("完成")
            try:
                on_success(res)
            except Exception:                # noqa: BLE001
                pass

        def failed(err):
            set_busy(False)
            status_var.set("失败")
            ui_log("错误：" + str(err))
            messagebox.showerror(APP_NAME, str(err))

        def thread_body():
            try:
                res = worker(ctx)
            except Cancelled:
                root.after(0, lambda: (set_busy(False), status_var.set("已取消"),
                                       ui_log("已取消。")))
            except ToolError as e:
                root.after(0, lambda e=e: failed(e))
            except Exception as e:           # noqa: BLE001
                root.after(0, lambda e=e: failed("%s\n\n%s" % (e, traceback.format_exc())))
            else:
                root.after(0, lambda r=res: done(r))

        threading.Thread(target=thread_body, daemon=True).start()

    # =================================================== 标签 1：打包压缩
    t1 = ttk.Frame(nb)
    nb.add(t1, text="  打包压缩  ")

    f = section(t1, "要压缩的内容")
    f.columnconfigure(1, weight=1)
    files_tree = ttk.Treeview(f, columns=("size", "type"), height=6, show="tree headings")
    files_tree.heading("#0", text="名称 / 路径")
    files_tree.heading("size", text="大小")
    files_tree.heading("type", text="类型")
    files_tree.column("#0", width=430)
    files_tree.column("size", width=100, anchor="e")
    files_tree.column("type", width=80, anchor="center")
    files_tree.grid(row=0, column=0, columnspan=4, sticky="nsew", pady=(0, 6))
    t1_sources = []

    def t1_refresh():
        files_tree.delete(*files_tree.get_children())
        for s in t1_sources:
            try:
                if os.path.isdir(s):
                    total = 0
                    for r, _d, fs in os.walk(s):
                        for n in fs:
                            try:
                                total += os.path.getsize(os.path.join(r, n))
                            except OSError:
                                pass
                    files_tree.insert("", "end", text=s, values=(fmt_bytes(total), "文件夹"))
                else:
                    files_tree.insert("", "end", text=s,
                                      values=(fmt_bytes(os.path.getsize(s)), "文件"))
            except OSError:
                files_tree.insert("", "end", text=s, values=("?", ""))

    def t1_add_files():
        ps = filedialog.askopenfilenames(title="选择要压缩的文件")
        for p in ps:
            p = os.path.normpath(p)
            if p not in t1_sources:
                t1_sources.append(p)
        t1_refresh()

    def t1_add_dir():
        p = filedialog.askdirectory(title="选择要压缩的文件夹")
        if p:
            p = os.path.normpath(p)
            if p not in t1_sources:
                t1_sources.append(p)
            t1_refresh()

    def t1_remove():
        for it in files_tree.selection():
            path = files_tree.item(it, "text")
            if path in t1_sources:
                t1_sources.remove(path)
        t1_refresh()

    btnrow = ttk.Frame(f)
    btnrow.grid(row=1, column=0, columnspan=4, sticky="w")
    for txt, cb in (("添加文件", t1_add_files), ("添加文件夹", t1_add_dir),
                    ("移除选中", t1_remove),
                    ("清空", lambda: (t1_sources.clear(), t1_refresh()))):
        ttk.Button(btnrow, text=txt, width=12, command=cb).pack(side="left", padx=(0, 6))
    hint1 = ttk.Label(f, text="提示：添加文件夹会连同子目录一起压缩。",
                      foreground=MUTED_FG)
    hint1.grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))

    g = section(t1, "输出与压缩参数")
    t1_out = tk.StringVar()
    _a, _b, _c = labeled_entry(g, 0, "输出压缩包：", t1_out,
                               browse=lambda: (lambda p: p and t1_out.set(p))(
                                   filedialog.asksaveasfilename(
                                       title="保存为",
                                       defaultextension=".7z",
                                       filetypes=[("7z 压缩包", "*.7z"), ("ZIP 压缩包", "*.zip"),
                                                  ("TAR", "*.tar"), ("所有文件", "*.*")])))
    t1_fmt = tk.StringVar(value="7z")
    ttk.Label(g, text="压缩格式：").grid(row=1, column=0, sticky="w", pady=4)
    fmt_box = ttk.Combobox(g, textvariable=t1_fmt, state="readonly", width=12,
                           values=["7z", "zip", "tar", "tar.gz", "tar.bz2", "tar.xz"])
    fmt_box.grid(row=1, column=1, sticky="w", pady=4)

    t1_lvl = tk.StringVar(value="5 - 标准")
    ttk.Label(g, text="压缩级别：").grid(row=2, column=0, sticky="w", pady=4)
    ttk.Combobox(g, textvariable=t1_lvl, state="readonly", width=14,
                 values=["0 - 仅存储", "1 - 最快", "5 - 标准", "7 - 较高", "9 - 极限"]
                 ).grid(row=2, column=1, sticky="w", pady=4)

    t1_vol = tk.StringVar(value="不分卷")
    ttk.Label(g, text="分卷大小：").grid(row=3, column=0, sticky="w", pady=4)
    ttk.Combobox(g, textvariable=t1_vol, state="readonly", width=18,
                 values=list(VOLUME_PRESETS)
                 ).grid(row=3, column=1, sticky="w", pady=4)
    t1_vol_custom = tk.StringVar(value="4GB")
    vol_custom_ent = ttk.Entry(g, textvariable=t1_vol_custom, width=10)
    vol_custom_ent.grid(row=3, column=2, sticky="w", padx=(6, 0), pady=4)

    t1_pwd = tk.StringVar()
    ttk.Label(g, text="密码（可选）：").grid(row=4, column=0, sticky="w", pady=4)
    pwd_ent = ttk.Entry(g, textvariable=t1_pwd, show="●", width=22)
    pwd_ent.grid(row=4, column=1, sticky="w", pady=4)
    show_pwd = tk.BooleanVar(value=False)
    ttk.Checkbutton(g, text="显示密码", variable=show_pwd,
                    command=lambda: pwd_ent.configure(show="" if show_pwd.get() else "●")
                    ).grid(row=4, column=2, sticky="w", pady=4)

    t1_solid = tk.BooleanVar(value=True)
    ttk.Checkbutton(g, text="固实压缩（7z，压缩率更高）", variable=t1_solid
                    ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))
    t1_threads = tk.StringVar(value="0")
    ttk.Label(g, text="线程数（0=自动）：").grid(row=6, column=0, sticky="w", pady=4)
    ttk.Spinbox(g, textvariable=t1_threads, from_=0, to=64, width=8
                ).grid(row=6, column=1, sticky="w", pady=4)

    t1_start = ttk.Button(t1, text="开始打包压缩", style="Big.TButton")
    t1_start.pack(pady=8)
    widgets_to_lock += [fmt_box, vol_custom_ent, pwd_ent, t1_start]

    def t1_run(ctx):
        if not t1_sources:
            raise ToolError("请先添加要压缩的文件或文件夹")
        out = t1_out.get().strip().strip('"')
        if not out:
            raise ToolError("请填写输出压缩包路径")
        fmt = t1_fmt.get()
        cfg = FORMATS[fmt]
        if not out.lower().endswith(cfg["ext"]):
            out += cfg["ext"]
            t1_out.set(out)
        level = int(t1_lvl.get().split(" ")[0])
        vol_txt = t1_vol.get()
        volume = VOLUME_PRESETS.get(vol_txt)
        if volume == "custom":
            volume = parse_size(t1_vol_custom.get(), default_unit=GB)
        if volume and not cfg["multi"]:
            raise ToolError("格式 %s 不支持直接分卷，\n请改用 7z / zip / tar，"
                            "或先用「分卷切割」把生成的文件切卷。" % fmt)
        state["last_outdir"] = os.path.dirname(os.path.abspath(out))
        ctx.log("输出 : %s" % out)
        ctx.log("格式 : %s   级别 : %d   分卷 : %s"
                % (fmt, level, fmt_bytes(volume) if volume else "不分卷"))
        ctx.log("-" * 62)
        made = ar_create(out, t1_sources, fmt=fmt, level=level,
                         password=t1_pwd.get() or None, volume=volume,
                         threads=int(t1_threads.get() or 0), ctx=ctx,
                         solid=t1_solid.get())
        ctx.log("-" * 62)
        total = 0
        for m in made:
            s = os.path.getsize(m)
            total += s
            ctx.log("生成 : %s  (%s)" % (m, fmt_bytes(s)))
        ctx.log("共 %d 个文件，合计 %s" % (len(made), fmt_bytes(total)))
        return made

    def t1_done(made):
        messagebox.showinfo(APP_NAME, "压缩完成！\n\n共生成 %d 个文件。" % len(made))

    t1_start.configure(command=lambda: run_async(t1_run, t1_done, "打包压缩"))

    # =================================================== 标签 2：分卷切割
    t2 = ttk.Frame(nb)
    nb.add(t2, text="  分卷切割  ")

    g = section(t2, "源文件与输出")
    t2_src = tk.StringVar()
    t2_out = tk.StringVar()
    labeled_entry(g, 0, "源压缩包：", t2_src, browse=lambda: (lambda p: p and (
        t2_src.set(os.path.normpath(p)),
        t2_out.set(os.path.dirname(p) or t2_out.get()),
        t2_preview()))(filedialog.askopenfilename(title="选择要分割的压缩包",
                                                 filetypes=[("压缩包", "*.zip *.rar *.7z *.tar *.gz *.iso"),
                                                            ("所有文件", "*.*")])))
    labeled_entry(g, 1, "输出目录：", t2_out,
                  browse=lambda: (lambda p: p and (t2_out.set(os.path.normpath(p)),
                                                   t2_preview()))(
                      filedialog.askdirectory(title="选择分卷存放目录")))

    g = section(t2, "分卷设置")
    t2_mode = tk.StringVar(value="size")
    ttk.Radiobutton(g, text="按大小分卷", value="size", variable=t2_mode,
                    command=lambda: t2_toggle()).grid(row=0, column=0, sticky="w")
    t2_size = tk.StringVar(value="4GB")
    size_ent = ttk.Entry(g, textvariable=t2_size, width=12)
    size_ent.grid(row=0, column=1, sticky="w", padx=(6, 8))
    ttk.Label(g, text="可填 4GB / 3.7GB / 4096MB / 4000000000B（纯数字=字节）",
              foreground=MUTED_FG).grid(row=0, column=2, columnspan=4, sticky="w")

    preset_row = ttk.Frame(g)
    preset_row.grid(row=1, column=0, columnspan=6, sticky="w", pady=(5, 0))
    for text, value in SPLIT_PRESETS:
        ttk.Button(preset_row, text=text, width=11,
                   command=lambda v=value: (t2_size.set(v), t2_preview())
                   ).pack(side="left", padx=(0, 5))

    ttk.Radiobutton(g, text="按份数等分", value="count", variable=t2_mode,
                    command=lambda: t2_toggle()).grid(row=2, column=0, sticky="w",
                                                      pady=(8, 0))
    t2_count = tk.StringVar(value="3")
    count_ent = ttk.Entry(g, textvariable=t2_count, width=8)
    count_ent.grid(row=2, column=1, sticky="w", padx=(6, 4), pady=(8, 0))
    ttk.Label(g, text="份", foreground=MUTED_FG).grid(row=2, column=2, sticky="w",
                                                     pady=(8, 0))

    t2_hash = tk.BooleanVar(value=True)
    t2_bat = tk.BooleanVar(value=True)
    t2_ow = tk.BooleanVar(value=False)
    ttk.Checkbutton(g, text="计算 SHA-256（可校验完整性）", variable=t2_hash,
                    command=lambda: t2_preview()).grid(row=3, column=0, columnspan=3,
                                                       sticky="w", pady=(8, 0))
    ttk.Checkbutton(g, text="生成“合并还原.bat”", variable=t2_bat
                    ).grid(row=3, column=3, columnspan=3, sticky="w", pady=(8, 0))
    ttk.Checkbutton(g, text="覆盖已有分卷", variable=t2_ow
                    ).grid(row=4, column=0, columnspan=3, sticky="w")

    note = ("说明：分卷按字节切分，不重新压缩，速度等于磁盘拷贝速度。\n"
            "命名规则 文件名.001 / .002 …，7-Zip、WinRAR 均可直接识别合并。\n"
            "1 GB = 1024 MB = 1,073,741,824 字节，“最大 4GB”＝ 4,294,967,296 字节。\n"
            "往网盘传：百度网盘非会员用 PC 客户端上传时单文件上限 4GB（网页端只有 1~2GB）。\n"
            "4GB 正好卡在临界值，且 4GiB = 4,294,967,296 > 40 亿字节；若要传百度网盘，\n"
            "建议选「3.7GB 网盘」＝ 3,972,929,536 字节，两种口径下都不会超限。")
    ttk.Label(g, text=note, foreground=MUTED_FG, justify="left"
              ).grid(row=5, column=0, columnspan=6, sticky="w", pady=(8, 0))

    t2_prev_var = tk.StringVar(value="请选择要分割的压缩包")
    pv = ttk.LabelFrame(t2, text=" 预览 ", padding=10)
    pv.pack(fill="x", padx=4, pady=(6, 0))
    ttk.Label(pv, textvariable=t2_prev_var, justify="left", foreground=DEFAULT_FG
              ).pack(anchor="w")

    t2_start = ttk.Button(t2, text="开始切割分卷", style="Big.TButton")
    t2_start.pack(pady=8)
    widgets_to_lock += [size_ent, count_ent, t2_start]

    def t2_toggle():
        if t2_mode.get() == "size":
            size_ent.configure(state="normal")
            count_ent.configure(state="disabled")
        else:
            size_ent.configure(state="disabled")
            count_ent.configure(state="normal")
        t2_preview()

    def t2_preview(*_):
        try:
            src = t2_src.get().strip().strip('"')
            if not src or not os.path.isfile(src):
                t2_prev_var.set("请选择要分割的压缩包")
                return
            total = os.path.getsize(src)
            if t2_mode.get() == "size":
                ps, sizes = plan_parts(total, part_size=parse_size(
                    t2_size.get(), default_unit=GB))
            else:
                ps, sizes = plan_parts(total, part_count=int(t2_count.get()))
            n = len(sizes)
            shown = " / ".join(fmt_bytes(s) for _, s in sizes[:5])
            if n > 5:
                shown += " / ... / %s" % fmt_bytes(sizes[-1][1])
            outdir = t2_out.get().strip() or os.path.dirname(src)
            t2_prev_var.set(
                "源文件大小：%s（%d 字节）\n"
                "每卷大小　：%s（%d 字节）\n"
                "将切分为　：%d 个分卷\n"
                "各卷大小　：%s\n"
                "输出目录　：%s"
                % (fmt_bytes(total), total, fmt_bytes(ps), ps, n, shown, outdir))
        except ToolError as e:
            t2_prev_var.set(str(e))
        except Exception as e:               # noqa: BLE001
            t2_prev_var.set("预览失败：%s" % e)

    for v in (t2_src, t2_out, t2_size, t2_count):
        v.trace_add("write", t2_preview)
    t2_mode.trace_add("write", t2_preview)

    def t2_run(ctx):
        src = t2_src.get().strip().strip('"')
        if not src:
            raise ToolError("请选择要分割的压缩包")
        outdir = t2_out.get().strip().strip('"') or os.path.dirname(src)
        state["last_outdir"] = outdir
        if t2_mode.get() == "size":
            res = split_bytes(src, outdir,
                              part_size=parse_size(t2_size.get(), default_unit=GB),
                              ctx=ctx, do_hash=t2_hash.get(),
                              make_bat=t2_bat.get(), overwrite=t2_ow.get())
        else:
            res = split_bytes(src, outdir, part_count=int(t2_count.get()),
                              ctx=ctx, do_hash=t2_hash.get(),
                              make_bat=t2_bat.get(), overwrite=t2_ow.get())
        return res

    def t2_done(res):
        messagebox.showinfo(APP_NAME,
                            "切割完成！\n\n分卷数量：%d\n每卷大小：%s\n输出目录：\n%s"
                            % (res["part_count"], fmt_bytes(res["part_size"]),
                               res["outdir"]))

    t2_start.configure(command=lambda: run_async(t2_run, t2_done, "切割分卷"))
    t2_toggle()

    # =================================================== 标签 3：合并还原
    t3 = ttk.Frame(nb)
    nb.add(t3, text="  合并还原  ")

    g = section(t3, "分卷与输出")
    t3_src = tk.StringVar()
    t3_out = tk.StringVar()
    labeled_entry(g, 0, "第一个分卷：", t3_src,
                  browse=lambda: (lambda p: p and (t3_src.set(os.path.normpath(p)),
                                                   t3_out.set(""),
                                                   t3_preview()))(
                      filedialog.askopenfilename(
                          title="选择 .001 分卷",
                          filetypes=[("分卷文件", "*.001"), ("所有文件", "*.*")])))
    labeled_entry(g, 1, "还原为（可空）：", t3_out,
                  browse=lambda: (lambda p: p and t3_out.set(os.path.normpath(p)))(
                      filedialog.asksaveasfilename(title="还原文件保存为")))
    ttk.Label(g, text="留空则还原到分卷所在目录，并使用原始文件名。",
              foreground=MUTED_FG).grid(row=2, column=1, sticky="w")

    g2 = section(t3, "选项")
    t3_verify = tk.BooleanVar(value=True)
    t3_ow = tk.BooleanVar(value=True)
    t3_del = tk.BooleanVar(value=False)
    ttk.Checkbutton(g2, text="校验 SHA-256（与分卷清单比对）", variable=t3_verify
                    ).grid(row=0, column=0, sticky="w")
    ttk.Checkbutton(g2, text="覆盖已存在的还原文件", variable=t3_ow
                    ).grid(row=0, column=1, sticky="w", padx=20)
    ttk.Checkbutton(g2, text="合并成功后删除分卷", variable=t3_del
                    ).grid(row=0, column=2, sticky="w")

    t3_prev_var = tk.StringVar(value="请选择第一个分卷（.001）")
    pv = ttk.LabelFrame(t3, text=" 预览 ", padding=10)
    pv.pack(fill="x", padx=4, pady=(6, 0))
    ttk.Label(pv, textvariable=t3_prev_var, justify="left").pack(anchor="w")

    t3_start = ttk.Button(t3, text="开始合并还原", style="Big.TButton")
    t3_start.pack(pady=8)
    widgets_to_lock += [t3_start]

    def t3_preview(*_):
        try:
            src = t3_src.get().strip().strip('"')
            if not src or not os.path.isfile(src):
                t3_prev_var.set("请选择第一个分卷（.001）")
                return
            base, parts = find_parts(src)
            sizes = [os.path.getsize(p) for p in parts]
            total = sum(sizes)
            t3_prev_var.set(
                "原始文件　：%s\n检测到分卷：%d 个\n合计大小　：%s（%d 字节）\n"
                "还原位置　：%s"
                % (base, len(parts), fmt_bytes(total), total,
                   t3_out.get().strip() or os.path.join(os.path.dirname(parts[0]), base)))
        except ToolError as e:
            t3_prev_var.set(str(e))
        except Exception as e:               # noqa: BLE001
            t3_prev_var.set("预览失败：%s" % e)

    t3_src.trace_add("write", t3_preview)

    def t3_run(ctx):
        src = t3_src.get().strip().strip('"')
        if not src:
            raise ToolError("请选择第一个分卷（.001）")
        out = t3_out.get().strip().strip('"') or None
        if out:
            state["last_outdir"] = os.path.dirname(os.path.abspath(out))
        else:
            state["last_outdir"] = os.path.dirname(os.path.abspath(src))
        res = merge_bytes(src, out, ctx=ctx, verify=t3_verify.get(),
                          overwrite=t3_ow.get(), delete_after=t3_del.get())
        return res

    def t3_done(res):
        msg = "合并完成！\n\n%s\n大小：%s" % (res["output"], fmt_bytes(res["output_size"]))
        if res.get("hash_match") is True:
            msg += "\n\nSHA-256 与原文件一致，文件完好。"
        elif res.get("hash_match") is False:
            messagebox.showwarning(
                APP_NAME, "合并完成，但哈希与清单不一致，请检查分卷完整性。\n\n"
                + res["output"])
            return
        if res.get("notice"):
            messagebox.showwarning(
                APP_NAME, msg + "\n\n" + "-" * 46 + "\n" + res["notice"])
            return
        messagebox.showinfo(APP_NAME, msg)

    t3_start.configure(command=lambda: run_async(t3_run, t3_done, "合并分卷"))

    # =================================================== 标签 4：解压解包
    t4 = ttk.Frame(nb)
    nb.add(t4, text="  解压解包  ")

    g = section(t4, "压缩包与输出")
    t4_src = tk.StringVar()
    t4_out = tk.StringVar()
    labeled_entry(g, 0, "压缩包：", t4_src,
                  browse=lambda: (lambda p: p and (t4_src.set(os.path.normpath(p)),
                                                   t4_out.set(os.path.dirname(p))))(
                      filedialog.askopenfilename(
                          title="选择压缩包（支持 .001 多卷）",
                          filetypes=[("压缩包", "*.zip *.rar *.7z *.tar *.gz *.bz2 *.xz *.iso *.001 *.cab"),
                                     ("所有文件", "*.*")])))
    labeled_entry(g, 1, "解压到：", t4_out,
                  browse=lambda: (lambda p: p and t4_out.set(os.path.normpath(p)))(
                      filedialog.askdirectory(title="选择解压目录")))
    ttk.Label(g, text="支持直接选择 .001 分卷，引擎会自动读取 .002、.003 …",
              foreground=MUTED_FG).grid(row=2, column=1, sticky="w")

    g2 = section(t4, "选项")
    t4_pwd = tk.StringVar()
    ttk.Label(g2, text="密码：").grid(row=0, column=0, sticky="w")
    ttk.Entry(g2, textvariable=t4_pwd, show="●", width=20).grid(row=0, column=1,
                                                                sticky="w", padx=(0, 16))
    t4_ow = tk.StringVar(value="overwrite")
    ttk.Label(g2, text="同名文件：").grid(row=1, column=0, sticky="w", pady=(6, 0))
    ttk.Combobox(g2, textvariable=t4_ow, state="readonly", width=12,
                 values=["overwrite", "skip", "rename"]).grid(row=1, column=1,
                                                              sticky="w", pady=(6, 0))
    ttk.Label(g2, text="overwrite=覆盖　skip=跳过　rename=自动改名",
              foreground=MUTED_FG).grid(row=1, column=2, sticky="w", padx=10, pady=(6, 0))
    t4_flat = tk.BooleanVar(value=False)
    ttk.Checkbutton(g2, text="不还原目录结构（全部平铺到同一目录）", variable=t4_flat
                    ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))

    row = ttk.Frame(t4)
    row.pack(pady=8)
    t4_start = ttk.Button(row, text="开始解压", style="Big.TButton")
    t4_start.pack(side="left", padx=4)
    t4_test = ttk.Button(row, text="测试完整性", width=14)
    t4_test.pack(side="left", padx=4)
    widgets_to_lock += [t4_start, t4_test]

    def t4_run(ctx):
        src = t4_src.get().strip().strip('"')
        out = t4_out.get().strip().strip('"')
        if not src:
            raise ToolError("请选择压缩包")
        if not out:
            raise ToolError("请选择解压目录")
        state["last_outdir"] = out
        ar_extract(src, out, password=t4_pwd.get() or None,
                   overwrite=t4_ow.get(), ctx=ctx, flat=t4_flat.get())
        return out

    def t4_test_run(ctx):
        src = t4_src.get().strip().strip('"')
        if not src:
            raise ToolError("请选择压缩包")
        ar_extract(src, None, password=t4_pwd.get() or None, ctx=ctx, test_only=True)
        return src

    t4_start.configure(command=lambda: run_async(
        t4_run, lambda out: messagebox.showinfo(APP_NAME, "解压完成！\n\n%s" % out),
        "解压"))
    t4_test.configure(command=lambda: run_async(
        t4_test_run, lambda s: messagebox.showinfo(APP_NAME, "完整性测试通过，压缩包无损坏。"),
        "测试"))

    # =================================================== 标签 5：查看内容
    t5 = ttk.Frame(nb)
    nb.add(t5, text="  查看内容  ")

    g = section(t5, "选择压缩包")
    t5_src = tk.StringVar()
    labeled_entry(g, 0, "压缩包：", t5_src,
                  browse=lambda: (lambda p: p and t5_src.set(os.path.normpath(p)))(
                      filedialog.askopenfilename(
                          title="选择压缩包",
                          filetypes=[("压缩包", "*.zip *.rar *.7z *.tar *.gz *.bz2 *.xz *.iso *.001"),
                                     ("所有文件", "*.*")])))
    t5_pwd = tk.StringVar()
    ttk.Label(g, text="密码：").grid(row=1, column=0, sticky="w", pady=4)
    ttk.Entry(g, textvariable=t5_pwd, show="●", width=20).grid(row=1, column=1,
                                                               sticky="w", pady=4)
    t5_btn = ttk.Button(t5, text="读取文件列表", style="Big.TButton")
    t5_btn.pack(pady=8)
    widgets_to_lock.append(t5_btn)

    info_var = tk.StringVar(value="")
    ttk.Label(t5, textvariable=info_var, foreground=MUTED_FG, justify="left"
              ).pack(anchor="w", padx=14)

    cols = ("size", "packed", "ratio", "mtime")
    lst = ttk.Treeview(t5, columns=cols, height=12, show="tree headings")
    lst.heading("#0", text="路径")
    lst.heading("size", text="原始大小")
    lst.heading("packed", text="压缩后")
    lst.heading("ratio", text="压缩率")
    lst.heading("mtime", text="修改时间")
    lst.column("#0", width=380)
    lst.column("size", width=100, anchor="e")
    lst.column("packed", width=100, anchor="e")
    lst.column("ratio", width=70, anchor="center")
    lst.column("mtime", width=140, anchor="center")
    vs = ttk.Scrollbar(t5, orient="vertical", command=lst.yview)
    lst.configure(yscrollcommand=vs.set)
    vs.pack(side="right", fill="y", padx=(0, 10), pady=(0, 10))
    lst.pack(fill="both", expand=True, padx=(10, 0), pady=(0, 10))

    def t5_run(ctx):
        src = t5_src.get().strip().strip('"')
        if not src:
            raise ToolError("请选择压缩包")
        ctx.log("正在读取 %s ..." % os.path.basename(src))
        info, entries = ar_list(src, t5_pwd.get() or None)
        return info, entries

    MAX_ROWS = 2000

    def t5_done(payload):
        info, entries = payload
        lst.delete(*lst.get_children())
        total = packed = 0
        nfiles = nshown = 0
        for e in entries:
            if e["_is_dir"]:
                continue
            nfiles += 1
            total += e["_size"]
            packed += e["_packed"]
            if nshown >= MAX_ROWS:
                continue
            nshown += 1
            ratio = ("%.1f%%" % (100.0 * e["_packed"] / e["_size"])) if e["_size"] else "-"
            lst.insert("", "end", text=e.get("Path", ""),
                       values=(fmt_bytes(e["_size"]), fmt_bytes(e["_packed"]),
                               ratio, e.get("Modified", "")[:19]))
        saved = (100.0 * (1 - packed / total)) if total else 0
        warn = ""
        if src.lower().endswith(".zip") and zip_is_zip64(src):
            warn = ("\n⚠ ZIP64 大压缩包：Windows 自带解压打不开（会提示“无效”），"
                    "请用本工具「解压解包」或 7-Zip / WinRAR / Bandizip。")
        info_var.set("类型：%s　文件数：%d　原始大小：%s　压缩后：%s　压缩节省：%.1f%%%s%s"
                     % (info.get("Type", "?"), nfiles, fmt_bytes(total),
                        fmt_bytes(packed), saved,
                        "" if nshown >= nfiles else
                        "　（列表仅显示前 %d 条）" % nshown, warn))

    t5_btn.configure(command=lambda: run_async(t5_run, t5_done, "读取列表"))

    # =================================================== 收尾
    ev = ar_version()
    engine_lbl.configure(text=(ev or "引擎未就绪：请把 7z.exe / 7z.dll 放到 engine\\ 目录"))

    def on_close():
        if state["busy"] and not messagebox.askyesno(APP_NAME, "任务正在进行，确定退出？"):
            return
        state["cancel"].set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    def show_about():
        messagebox.showinfo(
            "关于 " + APP_NAME,
            "%s\n\n"
            "一个可独立运行的压缩包处理工具：\n"
            "  · 打包压缩 7z / zip / tar / tar.gz / tar.bz2 / tar.xz\n"
            "  · 大文件分卷切割（.001 / .002 …），支持 4GB 以上分卷\n"
            "  · 分卷合并还原 + SHA-256 完整性校验\n"
            "  · 解压 7z / zip / rar / iso / cab / tar / gz / bz2 / xz …\n"
            "  · 直接读取 .001 多卷压缩包\n\n"
            "压缩引擎：%s\n"
            "引擎路径：%s\n\n"
            "命令行模式：%s --cli --help"
            % (APP_TITLE, ev or "未检测到", engine() or "-",
               os.path.basename(sys.executable)))

    menubar = tk.Menu(root)
    m_file = tk.Menu(menubar, tearoff=0)
    m_file.add_command(label="打开输出目录",
                       command=lambda: open_dir(state["last_outdir"]))
    m_file.add_separator()
    m_file.add_command(label="退出", command=on_close)
    menubar.add_cascade(label="文件", menu=m_file)
    m_help = tk.Menu(menubar, tearoff=0)
    m_help.add_command(label="关于", command=show_about)
    menubar.add_cascade(label="帮助", menu=m_help)
    root.configure(menu=menubar)

    root.update_idletasks()
    w = max(900, root.winfo_reqwidth())
    h = max(760, root.winfo_reqheight() + 40)
    x = max(0, (root.winfo_screenwidth() - w) // 2)
    y = max(0, (root.winfo_screenheight() - h) // 2 - 30)
    root.geometry("%dx%d+%d+%d" % (w, h, x, y))
    root.minsize(880, 640)
    root.mainloop()


def gui_selftest(seconds=1.2):
    """构建完整界面并短暂运行事件循环，用于验证打包后的程序界面可用。"""
    import tkinter as tk
    info = {}
    orig = tk.Tk.mainloop

    def fake_mainloop(self, *a, **k):
        def probe():
            try:
                def walk(w):
                    n = 0
                    for c in w.winfo_children():
                        n += 1 + walk(c)
                    return n
                info["title"] = self.title()
                info["widgets"] = walk(self)
                info["geometry"] = self.winfo_geometry()
                info["menu"] = bool(self.cget("menu"))
                info["tk_version"] = self.tk.call("info", "patchlevel")
                info["ok"] = True
            except Exception as e:           # noqa: BLE001
                info["ok"] = False
                info["error"] = repr(e)
            finally:
                self.after(150, self.destroy)
        self.after(int(seconds * 1000), probe)
        return orig(self, *a, **k)

    tk.Tk.mainloop = fake_mainloop
    try:
        gui_main()
    finally:
        tk.Tk.mainloop = orig
    if "ok" not in info:
        info["ok"] = False
        info["error"] = "事件循环未执行"
    return info


# ================================================================ 入口

def main():
    argv = sys.argv[1:]
    if argv and (argv[0] == "--cli" or argv[0] in
                 ("pack", "split", "merge", "extract", "list", "test", "engine",
                  "selftest")):
        if argv[0] != "--cli":
            argv = ["--cli"] + argv
        _attach_parent_console()
        sys.exit(run_cli(argv))
    gui_main()


if __name__ == "__main__":
    main()
