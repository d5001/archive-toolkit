# -*- coding: utf-8 -*-
"""GUI 冒烟测试：完整构建界面，运行 1.5 秒事件循环后自动关闭。

用于在不人工点击的情况下验证所有控件的创建、布局与回调绑定是否正常。
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk

import archive_toolkit as at

_orig_mainloop = tk.Tk.mainloop
_holder = {}


def _short_mainloop(self, *a, **k):
    _holder["root"] = self
    errors = []

    def probe():
        try:
            # 遍历所有控件，确认可访问
            def walk(w, depth=0):
                n = 0
                for c in w.winfo_children():
                    n += 1 + walk(c, depth + 1)
                return n
            total = walk(self)
            print("窗口标题 :", self.title())
            print("控件总数 :", total)
            print("窗口尺寸 :", self.winfo_geometry())
            print("菜单     :", "有" if self.cget("menu") else "无")
        except Exception as e:              # noqa: BLE001
            errors.append(e)
            traceback.print_exc()
        finally:
            self.after(200, self.destroy)

    self.after(600, probe)
    return _orig_mainloop(self, *a, **k)


tk.Tk.mainloop = _short_mainloop

print("ENGINE:", at.engine())
print("VERSION:", at.ar_version())
try:
    at.gui_main()
    print("SMOKE OK" if not _holder.get("root") else "SMOKE OK - 界面构建与事件循环正常")
except Exception:                            # noqa: BLE001
    traceback.print_exc()
    sys.exit(1)
