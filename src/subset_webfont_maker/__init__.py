"""subset-webfont-maker: 任意の OTF / TTF を Web フォントへ最適化する GUI アプリ。"""

from __future__ import annotations


def main() -> None:
    """GUI を起動する。"""
    from .gui import main as gui_main

    gui_main()
