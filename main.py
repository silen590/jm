# -*- coding: utf-8 -*-
"""
JM 漫画下载导出 App（Android / Kivy）
====================================
功能：
  - 输入禁漫车号（可多个，逗号分隔）下载漫画
  - 导出 PDF / APNG（可同时）
  - APNG 支持封面首帧（自动下载本子封面）、逐帧时长、缩放
  - 输出到应用外部目录：/storage/emulated/0/Android/data/<包名>/files/jm_export
"""
import os
import sys
import threading

# ---- 基础环境 ----
KIVY_NO_ARGS = "1"
os.environ.setdefault("KIVY_NO_ARGS", KIVY_NO_ARGS)

# vendor 目录（预打补丁的 jmcomic / commonx 纯 Python 包）
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_VENDOR = os.path.join(_APP_DIR, "vendor")
if os.path.isdir(_VENDOR):
    sys.path.insert(0, _VENDOR)

import jm_export  # 我们的核心逻辑（随 app 打包，位于私有目录根）

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.spinner import Spinner
from kivy.uix.checkbox import CheckBox
from kivy.uix.stacklayout import StackLayout
from kivy.core.window import Window


def get_output_dir():
    """获取输出目录（应用外部文件目录，无需权限，可通过 USB/文件管理器访问）。"""
    try:
        from android import mActivity
        d = mActivity.getExternalFilesDir(None)
        if d is not None:
            return os.path.join(d.getAbsolutePath(), "jm_export")
    except Exception:
        pass
    # 兜底
    return os.path.join(os.path.expanduser("~"), "jm_export")


# 捕获子线程输出到 UI
class _Tee:
    def __init__(self, sink):
        self.sink = sink  # list 缓冲

    def write(self, s):
        self.sink.append(s)

    def flush(self):
        pass


class JmExportApp(App):
    title = "JM 漫画导出"

    def build(self):
        self.log_buf = []
        self.output_dir = get_output_dir()
        os.makedirs(self.output_dir, exist_ok=True)

        root = BoxLayout(orientation="vertical", padding=12, spacing=8)

        # 标题
        root.add_widget(Label(
            text="JM 漫画下载导出工具\n(下载并导出 PDF / APNG)",
            size_hint_y=None, height=64, bold=True, font_size=18,
        ))

        # 输入区
        form = GridLayout(cols=2, size_hint_y=None, height=300, spacing=(8, 6))

        form.add_widget(Label(text="禁漫车号", size_hint_x=0.3))
        self.inp_jmid = TextInput(text="", hint_text="如 350234，多个用逗号分隔", multiline=False)
        form.add_widget(self.inp_jmid)

        form.add_widget(Label(text="章节车号(可选)"))
        self.inp_photo = TextInput(text="", hint_text="只下载某章节时填", multiline=False)
        form.add_widget(self.inp_photo)

        form.add_widget(Label(text="导出格式"))
        self.sp_format = Spinner(text="both", values=("both", "pdf", "apng"), size_hint_x=0.7)
        form.add_widget(self.sp_format)

        form.add_widget(Label(text="封面首帧"))
        cover_box = BoxLayout(orientation="horizontal", spacing=6)
        self.chk_cover = CheckBox(active=True, size_hint_x=None, width=40)
        self.lbl_cover = Label(text="自动下载封面", size_hint_x=None, width=120)
        cover_box.add_widget(self.chk_cover)
        cover_box.add_widget(self.lbl_cover)
        form.add_widget(cover_box)

        form.add_widget(Label(text="帧时长(ms)"))
        self.inp_dur = TextInput(text="400", hint_text="400 或 800,400 逐帧", multiline=False)
        form.add_widget(self.inp_dur)

        form.add_widget(Label(text="封面时长(ms)"))
        self.inp_cdur = TextInput(text="1500", hint_text="可选，默认同帧时长", multiline=False)
        form.add_widget(self.inp_cdur)

        form.add_widget(Label(text="缩放(0-1)"))
        self.inp_scale = TextInput(text="1.0", multiline=False)
        form.add_widget(self.inp_scale)

        root.add_widget(form)

        # 按钮
        btns = BoxLayout(orientation="horizontal", size_hint_y=None, height=56, spacing=8)
        self.btn_start = Button(text="开始下载")
        self.btn_start.bind(on_press=self.on_start)
        self.btn_open = Button(text="打开输出目录")
        self.btn_open.bind(on_press=self.on_open_dir)
        btns.add_widget(self.btn_start)
        btns.add_widget(self.btn_open)
        root.add_widget(btns)

        # 状态
        self.lbl_status = Label(
            text=f"输出目录:\n{self.output_dir}", halign="left", valign="top",
            size_hint_y=None, height=60, font_size=12,
        )
        self.lbl_status.bind(size=self._refresh_text_size)
        root.add_widget(self.lbl_status)

        # 日志
        log_scroll = ScrollView(size_hint=(1, 1))
        self.lbl_log = Label(
            text="", halign="left", valign="top", font_size=11,
            text_size=(None, None),
        )
        self.lbl_log.bind(size=self._refresh_text_size)
        log_scroll.add_widget(self.lbl_log)
        root.add_widget(log_scroll)

        Clock.schedule_interval(self._flush_log, 0.4)
        return root

    @staticmethod
    def _refresh_text_size(widget, size):
        widget.text_size = (size[0] - 20, None)

    def _log(self, msg):
        self.log_buf.append(msg)

    def _flush_log(self, dt):
        if self.log_buf:
            text = "\n".join(self.log_buf[-400:])
            self.lbl_log.text = text
            self.log_buf = []
        if self.running:
            self.lbl_status.text = f"运行中... 输出目录:\n{self.output_dir}"
        else:
            self.lbl_status.text = f"输出目录:\n{self.output_dir}"

    def on_start(self, _btn):
        jmids = [x.strip() for x in self.inp_jmid.text.replace("，", ",").split(",") if x.strip()]
        if not jmids:
            self._log("请先输入禁漫车号")
            return

        photo = self.inp_photo.text.strip() or None
        fmt = self.sp_format.text
        cover = "auto" if self.chk_cover.active else None
        dur = self.inp_dur.text.strip() or "400"
        cdur_text = self.inp_cdur.text.strip()
        cdur = int(cdur_text) if cdur_text else None
        try:
            scale = float(self.inp_scale.text.strip() or "1.0")
        except ValueError:
            scale = 1.0

        self.btn_start.disabled = True
        self.running = True
        self.lbl_log.text = ""

        t = threading.Thread(target=self._worker, args=(
            jmids, photo, fmt, cover, dur, cdur, scale,
        ), daemon=True)
        t.start()

    def _worker(self, jmids, photo, fmt, cover, dur, cdur, scale):
        old_out, old_err = sys.stdout, sys.stderr
        tee_out, tee_err = _Tee(self.log_buf), _Tee(self.log_buf)
        sys.stdout, sys.stderr = tee_out, tee_err
        ok = 0
        try:
            for jmid in jmids:
                self.log_buf.append(f">>> 开始处理车号 [{jmid}]")
                try:
                    album_root, detail, produced = jm_export.run_download(
                        jmid=jmid,
                        fmt=fmt,
                        output_dir=self.output_dir,
                        duration=dur,
                        loop=0,
                        scale=scale,
                        delete_original=False,
                        photo_id=photo,
                        option_file=None,
                        flat=False,
                        cover=cover,
                        cover_duration=cdur,
                    )
                    ok += 1
                    self.log_buf.append(f"[OK] {jmid} → {album_root}")
                except Exception as e:
                    self.log_buf.append(f"[FAIL] {jmid} → {type(e).__name__}: {e}")
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            self.running = False
            self.btn_start.disabled = False
            self.log_buf.append(f"\n===== 完成：成功 {ok}/{len(jmids)}，输出目录：{self.output_dir} =====")

    def on_open_dir(self, _btn):
        """尝试用系统文件管理器打开输出目录（尽力而为）。"""
        try:
            from jnius import autoclass
            Intent = autoclass("android.content.Intent")
            Uri = autoclass("android.net.Uri")
            File = autoclass("java.io.File")
            from android import mActivity
            uri = Uri.fromFile(File(self.output_dir))
            intent = Intent()
            intent.setAction(Intent.ACTION_VIEW)
            intent.setDataAndType(uri, "*/*")
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            mActivity.startActivity(intent)
        except Exception as e:
            self._log(f"无法打开目录：{e}（请用文件管理器访问 {self.output_dir}）")


if __name__ == "__main__":
    JmExportApp().run()
