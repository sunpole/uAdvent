"""Публикует полные тексты, проверенные очистителем, для чтения и поиска."""

import argparse
import hashlib
import json
import re
from html import escape
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = "https://raw.githubusercontent.com/sunpole/uAdvent/main/library/"
STYLE = """
:root{color-scheme:dark}body{background:#101b30;color:#eef3f8;font:18px/1.55 system-ui,sans-serif;
max-width:48rem;margin:auto;padding:1.5rem}a{color:#f3c67b}a:focus-visible{outline:2px solid}
h1,h2{line-height:1.2}section{border-top:1px solid #516078;margin-top:3rem}
.text{white-space:pre-wrap;overflow-wrap:anywhere}nav li{margin-bottom:.5rem}
"""


def sections(folder):
    raw = (folder / "План.md").read_bytes()
    report = (folder / "Сверка.txt").read_text(encoding="utf-8").splitlines()
    digest = hashlib.sha256(raw).hexdigest()
    if f"SHA-256 готового текста: {digest}" not in report:
        raise ValueError(f"{folder.name}: контрольная сумма текста отличается от сверки.")
    lines = re.findall(rb"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z", raw)
    title = lines[0].decode("utf-8").rstrip("\r\n")
    breaks = [0]
    offset = 0
    for row in report:
        if " | " not in row:
            continue
        label, _, interval = row.split(" | ")
        first, last = map(int, interval.split("–"))
        if last < first or first < 1:
            raise ValueError(f"{folder.name}: неверный диапазон сверки.")
        if label.startswith("Заголовок дня "):
            day = len(breaks)
            if label != f"Заголовок дня {day}" or offset >= len(lines):
                raise ValueError(f"{folder.name}: нарушен порядок дней.")
            breaks.append(offset)
        offset += last - first + 1
    if offset != len(lines) or len(breaks) < 2:
        raise ValueError(f"{folder.name}: сверка не покрывает весь план.")
    count = len(breaks) - 1
    breaks.append(offset)
    chunks = [b"".join(lines[a:b]) for a, b in zip(breaks, breaks[1:])]
    for day, chunk in enumerate(chunks[1:], 1):
        if chunk.decode("utf-8").splitlines()[0].strip() != f"День {day} из {count}":
            raise ValueError(f"{folder.name}: граница дня {day} не совпадает с заголовком.")
    return title, raw, chunks


def generate(ready):
    assets, contents, navigation, records = {}, [], [], []
    for plan in sorted(ready.iterdir()):
        if not plan.is_dir():
            continue
        title, raw, chunks = sections(plan)
        key = plan.name
        url = quote(key, safe="")
        assets[f"{key}/plan.txt"] = raw
        navigation.append(f'<li><a href="#p-{url}">{escape(title)}</a> · {len(chunks)-1} дн.</li>')
        contents.append(f'<section id="p-{escape(key, quote=True)}"><h2>{escape(title)}</h2>'
                        f'<a href="{url}/plan.txt">Скачать полный текст</a>')
        for day, chunk in enumerate(chunks):
            filename = f"day-{day:02}.txt" if day else "intro.txt"
            assets[f"{key}/{filename}"] = chunk
            href = f"{url}/{filename}"
            text = chunk.decode("utf-8")
            records.append(json.dumps(dict(plan=key, title=title, day=day or None,
                                           url=PUBLIC+href, text=text), ensure_ascii=False))
            label = f"День {day}" if day else "О плане"
            contents.append(f'<h3><a href="{href}">{label}</a></h3>'
                            f'<div class="text">{escape(text)}</div>')
        contents.append('</section>')
    if not navigation:
        raise ValueError("Нет готовых планов для публикации.")
    html = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Все планы чтения · uAdvent</title><style>{STYLE}</style></head>
<body><header><a href="../">uAdvent · Главная</a><h1>Все планы чтения</h1>
<p>Все тексты раскрыты. Для поиска слова или фразы нажмите Ctrl+F.
Ссылка у каждого дня открывает его полный текст отдельно.</p></header>
<nav aria-label="Оглавление"><ul>{''.join(navigation)}</ul>
<a href="index.jsonl">Скачать тексты для поиска и индексации (JSONL)</a></nav>
<main>{chr(10).join(contents)}</main><footer><a href="../THIRD_PARTY_NOTICES.md">Авторы и источники</a></footer></body></html>
"""
    assets["index.html"] = html.encode("utf-8")
    assets["index.jsonl"] = ("\n".join(records) + "\n").encode("utf-8")
    return assets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="сверить опубликованные файлы")
    args = parser.parse_args()
    assets = generate(ROOT / "Готовые планы")
    folder = ROOT / "library"
    for name, data in assets.items():
        path = folder / name
        if args.check:
            if not path.is_file() or path.read_bytes() != data:
                parser.exit(1, f"Требуется пересборка: {name}\n")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    obsolete = [p for p in folder.rglob("*") if p.is_file() and p.relative_to(folder).as_posix() not in assets]
    if obsolete:
        parser.exit(1, f"Проверьте лишние файлы перед публикацией: {obsolete}\n")
    print(f"Файлов библиотеки: {len(assets)}")


if __name__ == "__main__":
    main()
