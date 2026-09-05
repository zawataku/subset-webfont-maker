# Subset Web Font Maker

任意の **OTF / TTF** フォントを、Web フォント(woff2 / woff / ttf)として最適化する
GUI アプリケーション(Python + tkinter)です。

[genjyuugothic-subsets](https://github.com/shogo82148/genjyuugothic-subsets) の手法を参考にしています。
Google Fonts が配信する **Noto Sans JP と同じ 120 分割の `unicode-range`** でフォントをサブセット化し、
`unicode-range` 付きの `@font-face` を並べた CSS を生成します。
ブラウザはページの表示に実際に必要なサブセットだけをダウンロードするため、
大容量の日本語フォントでも読み込みが軽くなります。

## 特徴

- OTF / TTF を選ぶだけで Web フォント一式 + CSS を自動生成
- 出力フォーマットを **woff2 / woff / ttf** からラジオボタンで選択
- Noto Sans JP 互換の 120 分割 unicode-range を同梱しているため、実行時に外部リソース不要
- `font-display: swap` 付きの `@font-face` を自動生成
- 生成はバックグラウンドスレッドで実行し、進捗表示・中断に対応

## 必要環境

- Python 3.13 以上
- [uv](https://docs.astral.sh/uv/)
- tkinter(標準ライブラリ。OS によっては別途インストールが必要)

依存パッケージ(`fonttools`, `brotli`)は uv が自動で解決します。

## セットアップ & 起動

```sh
uv sync
uv run subset-webfont-maker
```

または:

```sh
uv run python -m subset_webfont_maker
```

## 使い方

1. **入力フォント**: 最適化したい OTF / TTF を選択
2. **出力先フォルダ**: woff2/CSS の出力先(未指定なら入力名 + `-webfont`)
3. **font-family 名**: 生成する CSS の `font-family`(空欄ならフォントから自動取得)
4. **出力フォーマット**: woff2 / woff / ttf を選択
5. **「Web フォントを生成」** を押す

生成後、出力フォルダに `<family>.css` と `<family>-<n>.<ext>` が並びます。
HTML から次のように読み込むだけで利用できます。

```html
<link href="./path/to/MyFont.css" rel="stylesheet" />
```

```css
body {
  font-family: "MyFont", sans-serif;
}
```

## 仕組み

- `src/subset_webfont_maker/unicode_ranges.py`
  Noto Sans JP の CSS から抽出した 120 個の `unicode-range`(自動生成・同梱)。
- `src/subset_webfont_maker/subsetter.py`
  `fontTools` の Python API で各 range ごとにサブセット化し、Web フォント + CSS を出力。
- `src/subset_webfont_maker/gui.py`
  tkinter による GUI。

## ライセンス / クレジット

- unicode-range の分割定義は Google Fonts の **Noto Sans JP**(SIL Open Font License)に基づきます。
- 出力する Web フォントの著作権・ライセンスは、入力フォントのライセンスに従います。
  サブセット化・Web 配信が許諾されているフォントであることを確認のうえご利用ください。
