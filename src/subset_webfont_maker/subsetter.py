"""フォントのサブセット化と Web フォント生成のコア処理

Noto Sans JP が採用する 120 分割の unicode-range ごとにフォントをサブセット化し、
各サブセットを Web フォント(woff2 / woff / ttf)として書き出したうえで、
`unicode-range` 付きの `@font-face` を並べた CSS を生成する。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from fontTools import subset
from fontTools.ttLib import TTFont

from .unicode_ranges import UNICODE_RANGES

# サブセット化できないテーブル(LTSH / MERG / meta など)を落とす際の
# 「don't know how to subset; dropped」警告は Web フォント生成では想定内のため抑制する。
logging.getLogger("fontTools.subset").setLevel(logging.ERROR)

# 出力フォーマット。値は @font-face の format() / 拡張子に対応。
FORMATS: dict[str, dict[str, str]] = {
    "woff2": {"ext": "woff2", "flavor": "woff2", "css_format": "woff2"},
    "woff": {"ext": "woff", "flavor": "woff", "css_format": "woff"},
    "ttf": {"ext": "ttf", "flavor": "", "css_format": "truetype"},
}

_UNICODE_TOKEN_RE = re.compile(r"U\+([0-9A-Fa-f]+)(?:-([0-9A-Fa-f]+))?")


@dataclass
class SubsetResult:
    """1 つのサブセットの生成結果。"""

    index: int
    unicode_range: str
    filename: str
    size: int  # bytes
    glyph_count: int


@dataclass
class BuildSummary:
    """ビルド全体の結果サマリ。"""

    family: str
    output_dir: Path
    css_path: Path
    fmt: str
    results: list[SubsetResult] = field(default_factory=list)

    @property
    def total_size(self) -> int:
        return sum(r.size for r in self.results)

    @property
    def subset_count(self) -> int:
        return len(self.results)


def parse_unicodes(unicode_range: str) -> list[int]:
    """`U+xxxx, U+yyyy-zzzz` 形式の文字列をコードポイントのリストへ展開する。"""
    codepoints: list[int] = []
    for begin, end in _UNICODE_TOKEN_RE.findall(unicode_range):
        start = int(begin, 16)
        stop = int(end, 16) if end else start
        codepoints.extend(range(start, stop + 1))
    return codepoints


def derive_family_name(font_path: Path) -> str:
    """フォントファイルから font-family 名(英語優先)を推定する。"""
    try:
        font = TTFont(str(font_path), lazy=True)
    except Exception:
        return font_path.stem
    try:
        name_table = font["name"]
        # 16=Typographic Family, 1=Font Family の順で英語(langID=0x409)を優先
        for name_id in (16, 1):
            record = name_table.getName(name_id, 3, 1, 0x409) or name_table.getName(
                name_id, 1, 0, 0
            )
            if record:
                value = record.toUnicode().strip()
                if value:
                    return value
    except Exception:
        pass
    finally:
        font.close()
    return font_path.stem


def _font_weight(font_path: Path) -> int:
    """OS/2 テーブルから font-weight を取得(取れなければ 400)。"""
    try:
        font = TTFont(str(font_path), lazy=True)
        try:
            return int(getattr(font["OS/2"], "usWeightClass", 400))
        finally:
            font.close()
    except Exception:
        return 400


def _make_subsetter(unicodes: list[int]) -> subset.Subsetter:
    options = subset.Options()
    # 参考リポジトリの pyftsubset 設定に合わせる: 全 layout 機能を保持
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.name_legacy = True
    options.name_languages = ["*"]
    options.notdef_outline = True
    options.recalc_bounds = True
    options.recalc_timestamp = False
    options.drop_tables = []
    options.ignore_missing_unicodes = True
    s = subset.Subsetter(options=options)
    s.populate(unicodes=unicodes)
    return s


def build_web_font(
    font_path: str | Path,
    output_dir: str | Path,
    fmt: str = "woff2",
    family: str | None = None,
    *,
    skip_empty: bool = True,
    progress: Callable[[int, int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> BuildSummary:
    """フォントをサブセット化し Web フォント一式 + CSS を生成する。

    Args:
        font_path: 入力の OTF / TTF パス。
        output_dir: 出力先ディレクトリ(無ければ作成)。
        fmt: 出力フォーマット("woff2" / "woff" / "ttf")。
        family: 生成する font-family 名。None ならフォントから推定。
        skip_empty: そのレンジに含まれるグリフが 0 個ならファイルを出力しない。
        progress: (現在, 全体, メッセージ) を受け取る進捗コールバック。
        should_cancel: True を返すと処理を中断する。

    Returns:
        BuildSummary: 生成結果のサマリ。
    """
    if fmt not in FORMATS:
        raise ValueError(f"未対応のフォーマットです: {fmt}")

    font_path = Path(font_path)
    if not font_path.exists():
        raise FileNotFoundError(f"フォントが見つかりません: {font_path}")

    spec = FORMATS[fmt]
    family = family or derive_family_name(font_path)
    weight = _font_weight(font_path)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    base = _safe_basename(family)
    total = len(UNICODE_RANGES)
    results: list[SubsetResult] = []

    for i, unicode_range in enumerate(UNICODE_RANGES):
        if should_cancel and should_cancel():
            raise BuildCancelled()
        if progress:
            progress(i, total, f"[{i + 1}/{total}] サブセット生成中…")

        unicodes = parse_unicodes(unicode_range)

        # 入力ごとに読み直す(Subsetter はフォントを破壊的に変更するため)
        font = TTFont(str(font_path))
        try:
            subsetter = _make_subsetter(unicodes)
            subsetter.subset(font)
            glyph_count = len(font.getGlyphOrder())
            # .notdef のみ(=実グリフ無し)はスキップ対象
            has_glyphs = glyph_count > 1
            if skip_empty and not has_glyphs:
                continue

            filename = f"{base}-{i}.{spec['ext']}"
            out_path = output_dir / filename
            if spec["flavor"]:
                font.flavor = spec["flavor"]
            font.save(str(out_path))
        finally:
            font.close()

        results.append(
            SubsetResult(
                index=i,
                unicode_range=unicode_range,
                filename=filename,
                size=out_path.stat().st_size,
                glyph_count=glyph_count,
            )
        )

    css_path = output_dir / f"{base}.css"
    css_path.write_text(
        _render_css(family, weight, spec["css_format"], results),
        encoding="utf-8",
    )

    if progress:
        progress(total, total, "完了")

    return BuildSummary(
        family=family,
        output_dir=output_dir,
        css_path=css_path,
        fmt=fmt,
        results=results,
    )


class BuildCancelled(Exception):
    """ユーザーによる中断。"""


def _safe_basename(name: str) -> str:
    """ファイル名に使えない文字を除いた基底名を返す。"""
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "-", name).strip("-")
    return cleaned or "webfont"


def _render_css(
    family: str,
    weight: int,
    css_format: str,
    results: list[SubsetResult],
) -> str:
    header = (
        "@charset \"UTF-8\";\n\n"
        "/*\n"
        f" * font-family: {family}\n"
        " */\n\n"
    )
    blocks = [header]
    for r in results:
        blocks.append(
            f"/* [{r.index}] */\n"
            "@font-face {\n"
            f"    font-family: '{family}';\n"
            "    font-style: normal;\n"
            f"    font-weight: {weight};\n"
            "    font-display: swap;\n"
            f"    src: url(./{r.filename}) format('{css_format}');\n"
            f"    unicode-range: {r.unicode_range};\n"
            "}\n"
        )
    return "\n".join(blocks) + "\n"
