"""tkinter による Web フォント最適化 GUI

任意の OTF / TTF を選び、Noto Sans JP と同じ unicode-range 分割で
サブセット化した Web フォント一式(+ CSS)を生成する。
"""

from __future__ import annotations

import queue
import threading
import traceback
from pathlib import Path
from tkinter import (
    BOTH,
    DISABLED,
    END,
    HORIZONTAL,
    LEFT,
    NORMAL,
    RIGHT,
    StringVar,
    Tk,
    W,
    X,
    filedialog,
    messagebox,
)
from tkinter import ttk

from .subsetter import (
    BuildCancelled,
    BuildSummary,
    FORMATS,
    build_web_font,
    derive_family_name,
)

APP_TITLE = "Subset Web Font Maker"


def _human_size(num: int) -> str:
    size = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


class SubsetApp:
    """アプリ本体。ワーカースレッドで生成し、UI スレッドへキューで通知する。"""

    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.minsize(640, 520)

        self.font_path = StringVar()
        self.output_dir = StringVar()
        self.family = StringVar()
        self.fmt = StringVar(value="woff2")

        self._queue: queue.Queue[tuple] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()

        self._build_ui()
        self.root.after(100, self._poll_queue)

    # ---- UI 構築 ----------------------------------------------------------
    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 6}
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill=BOTH, expand=True)

        # 入力フォント
        in_frame = ttk.LabelFrame(main, text="入力フォント (OTF / TTF)")
        in_frame.pack(fill=X, **pad)
        ttk.Entry(in_frame, textvariable=self.font_path).pack(
            side=LEFT, fill=X, expand=True, padx=(8, 4), pady=8
        )
        ttk.Button(in_frame, text="参照…", command=self._choose_font).pack(
            side=RIGHT, padx=8, pady=8
        )

        # 出力先
        out_frame = ttk.LabelFrame(main, text="出力先フォルダ")
        out_frame.pack(fill=X, **pad)
        ttk.Entry(out_frame, textvariable=self.output_dir).pack(
            side=LEFT, fill=X, expand=True, padx=(8, 4), pady=8
        )
        ttk.Button(out_frame, text="参照…", command=self._choose_output).pack(
            side=RIGHT, padx=8, pady=8
        )

        # font-family 名
        fam_frame = ttk.LabelFrame(main, text="font-family 名(空欄ならフォントから自動取得)")
        fam_frame.pack(fill=X, **pad)
        ttk.Entry(fam_frame, textvariable=self.family).pack(
            fill=X, padx=8, pady=8
        )

        # 出力フォーマット(ラジオボタン)
        fmt_frame = ttk.LabelFrame(main, text="出力フォーマット")
        fmt_frame.pack(fill=X, **pad)
        labels = {
            "woff2": "woff2(推奨・最小)",
            "woff": "woff(互換)",
            "ttf": "ttf(サブセット)",
        }
        for key in FORMATS:
            ttk.Radiobutton(
                fmt_frame,
                text=labels.get(key, key),
                value=key,
                variable=self.fmt,
            ).pack(side=LEFT, padx=12, pady=8)

        # 実行ボタン
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill=X, **pad)
        self.run_btn = ttk.Button(
            btn_frame, text="Web フォントを生成", command=self._start_build
        )
        self.run_btn.pack(side=LEFT)
        self.cancel_btn = ttk.Button(
            btn_frame, text="中断", command=self._request_cancel, state=DISABLED
        )
        self.cancel_btn.pack(side=LEFT, padx=8)

        # 進捗バー
        self.progress = ttk.Progressbar(main, orient=HORIZONTAL, mode="determinate")
        self.progress.pack(fill=X, **pad)
        self.status = StringVar(value="待機中")
        ttk.Label(main, textvariable=self.status, anchor=W).pack(fill=X, padx=10)

        # ログ
        log_frame = ttk.LabelFrame(main, text="ログ")
        log_frame.pack(fill=BOTH, expand=True, **pad)
        self.log = ttk.Treeview(
            log_frame, show="", columns=("msg",), height=8
        )
        self.log.column("msg", anchor=W)
        self.log.pack(fill=BOTH, expand=True, padx=8, pady=8)

    # ---- イベント ---------------------------------------------------------
    def _choose_font(self) -> None:
        path = filedialog.askopenfilename(
            title="フォントを選択",
            filetypes=[("フォント", "*.otf *.ttf"), ("すべて", "*.*")],
        )
        if not path:
            return
        self.font_path.set(path)
        p = Path(path)
        if not self.output_dir.get():
            self.output_dir.set(str(p.parent / f"{p.stem}-webfont"))
        if not self.family.get():
            try:
                self.family.set(derive_family_name(p))
            except Exception:
                self.family.set(p.stem)

    def _choose_output(self) -> None:
        path = filedialog.askdirectory(title="出力先フォルダを選択")
        if path:
            self.output_dir.set(path)

    def _log(self, msg: str) -> None:
        self.log.insert("", END, values=(msg,))
        children = self.log.get_children()
        if children:
            self.log.see(children[-1])

    def _start_build(self) -> None:
        font_path = self.font_path.get().strip()
        output_dir = self.output_dir.get().strip()
        if not font_path:
            messagebox.showwarning(APP_TITLE, "入力フォントを選択してください。")
            return
        if not Path(font_path).exists():
            messagebox.showerror(APP_TITLE, "入力フォントが存在しません。")
            return
        if not output_dir:
            messagebox.showwarning(APP_TITLE, "出力先フォルダを指定してください。")
            return

        self._cancel.clear()
        self.run_btn.config(state=DISABLED)
        self.cancel_btn.config(state=NORMAL)
        self.progress.config(value=0, maximum=100)
        for item in self.log.get_children():
            self.log.delete(item)
        self.status.set("生成を開始しました…")
        self._log(f"入力: {font_path}")
        self._log(f"出力: {output_dir}  /  形式: {self.fmt.get()}")

        self._worker = threading.Thread(
            target=self._run_build,
            args=(font_path, output_dir, self.fmt.get(), self.family.get().strip()),
            daemon=True,
        )
        self._worker.start()

    def _request_cancel(self) -> None:
        self._cancel.set()
        self.status.set("中断要求を受け付けました…")

    # ---- ワーカースレッド ------------------------------------------------
    def _run_build(
        self, font_path: str, output_dir: str, fmt: str, family: str
    ) -> None:
        def on_progress(current: int, total: int, message: str) -> None:
            self._queue.put(("progress", current, total, message))

        try:
            summary = build_web_font(
                font_path,
                output_dir,
                fmt=fmt,
                family=family or None,
                progress=on_progress,
                should_cancel=self._cancel.is_set,
            )
            self._queue.put(("done", summary))
        except BuildCancelled:
            self._queue.put(("cancelled",))
        except Exception:  # noqa: BLE001 - UI へ全エラーを通知
            self._queue.put(("error", traceback.format_exc()))

    # ---- UI スレッド: キュー処理 -----------------------------------------
    def _poll_queue(self) -> None:
        try:
            while True:
                msg = self._queue.get_nowait()
                self._handle_message(msg)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _handle_message(self, msg: tuple) -> None:
        kind = msg[0]
        if kind == "progress":
            _, current, total, text = msg
            self.progress.config(maximum=total, value=current)
            self.status.set(text)
        elif kind == "done":
            self._on_done(msg[1])
        elif kind == "cancelled":
            self._finish()
            self.status.set("中断しました。")
            self._log("ユーザーにより中断されました。")
        elif kind == "error":
            self._finish()
            self.status.set("エラーが発生しました。")
            self._log(msg[1].strip().splitlines()[-1])
            messagebox.showerror(APP_TITLE, f"生成に失敗しました:\n\n{msg[1]}")

    def _on_done(self, summary: BuildSummary) -> None:
        self._finish()
        self.progress.config(value=self.progress["maximum"])
        self.status.set(
            f"完了: {summary.subset_count} サブセット / 合計 {_human_size(summary.total_size)}"
        )
        self._log(f"font-family: {summary.family}")
        self._log(f"CSS: {summary.css_path}")
        self._log(
            f"出力サブセット数: {summary.subset_count}  "
            f"合計サイズ: {_human_size(summary.total_size)}"
        )
        if messagebox.askyesno(
            APP_TITLE,
            f"生成が完了しました。\n\n"
            f"font-family: {summary.family}\n"
            f"サブセット数: {summary.subset_count}\n"
            f"合計サイズ: {_human_size(summary.total_size)}\n\n"
            f"出力フォルダを開きますか?",
        ):
            self._open_folder(summary.output_dir)

    def _finish(self) -> None:
        self.run_btn.config(state=NORMAL)
        self.cancel_btn.config(state=DISABLED)

    @staticmethod
    def _open_folder(path: Path) -> None:
        import os
        import subprocess
        import sys

        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            pass


def main() -> None:
    root = Tk()
    try:
        # Windows の高 DPI でぼやけないように
        from ctypes import windll  # type: ignore

        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    SubsetApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
