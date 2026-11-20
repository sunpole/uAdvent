#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Очистка сохранённых Markdown-страниц планов YouVersion.

Стандартная библиотека Python 3.9+. Исходные файлы НЕ изменяются.

Примеры:
    python clean_advent.py "C:/projects/uAdvent/data/001"
    python clean_advent.py "C:/projects/uAdvent/data"  # все подпапки-планы
    python clean_advent.py "C:/projects/uAdvent/data/001" --output "C:/ready/plan.md"
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

DAY_HEADER = re.compile(r"^День\s+(\d+)\s+из\s+(\d+)\s*$", re.IGNORECASE)
DAY_LINK = re.compile(r"^День\s+\d+\s*$", re.IGNORECASE)
NUMBER_LINK = re.compile(r"^\d{1,3}$")
DURATION = re.compile(r"^\d+\s+(?:день|дня|дней)\s*$", re.IGNORECASE)

# Признаки начала интерфейсного блока ПОСЛЕ основного текста.
# Точное совпадение не удаляет случайные слова внутри абзацев.
FOOTER_LINES = {
    "начать этот план", "об этом плане", "об издателе",
    "планы, относящиеся к теме", "more", "блог",
    "переводы библии", "языки текста библии", "помощь",
    "обратная связь", "пожертвования", "о нас", "уголок прессы",
    "церкви", "разработчики", "программа раскрытия уязвимостей",
    "политика конфиденциальности", "условия",
}
COVER_FOOTER_PREFIXES = ("больше от ", "© ", "об издателе")
HEADER_ONLY = {
    "перейти к содержимому", "главная", "библия", "стих дня",
    "видео", "планы", "русский", "скачать приложение",
    "о плане", "поделиться", "пример", "значок библиискачать приложение",
    "app icon", "начать этот план",
}


def norm(value: str) -> str:
    """Для распознавания служебных строк; исходный текст не изменяем."""
    return re.sub(r"\s+", " ", value.strip().lstrip("# ")).casefold()


def is_footer(line: str) -> bool:
    s = norm(line)
    return bool(
        s in FOOTER_LINES
        or DAY_LINK.fullmatch(s)
        or s.startswith("© ")
    )


def trim_paragraphs(lines: list[str]) -> str:
    """Только очистка пустых строк и концевых пробелов; слова не редактируем."""
    cleaned: list[str] = []
    for line in lines:
        line = line.strip()
        if line:
            cleaned.append(line)
        elif cleaned and cleaned[-1] != "":
            cleaned.append("")
    while cleaned and cleaned[-1] == "":
        cleaned.pop()
    return "\n".join(cleaned)


def get_title(lines: list[str]) -> str:
    """Получение названия из заголовка сохранённой страницы."""
    for line in lines:
        t = line.strip().lstrip("# ")
        if not t:
            continue
        t = re.sub(r"\s+-\s+Приложение Библия\s*$", "", t, flags=re.I)
        t = re.sub(r"\s+Пример\s*$", "", t, flags=re.I)
        if norm(t) not in HEADER_ONLY and not t.startswith("Ищите стихи"):
            return t
    return "План чтения"


@dataclass
class Day:
    number: int
    total: int
    body: str
    filename: str
    original_chars: int
    warning: list[str] = field(default_factory=list)


def parse_day(path: Path, lines: list[str]) -> Day | None:
    header = next(
        ((i, m) for i, line in enumerate(lines) if (m := DAY_HEADER.fullmatch(line.strip()))),
        None,
    )
    if header is None:
        return None

    pos, match = header
    number, total = int(match.group(1)), int(match.group(2))
    warnings: list[str] = []
    # Меню 1,2,3...7 является частью сайта, а не содержимого.
    start = pos + 1
    while start < len(lines):
        s = norm(lines[start])
        if not s or NUMBER_LINK.fullmatch(s) or s in HEADER_ONLY:
            start += 1
        else:
            break

    body_lines: list[str] = []
    for line in lines[start:]:
        if is_footer(line):
            break
        body_lines.append(line)
    else:
        warnings.append("Не найден маркер начала подвала; проверьте конец дня вручную.")

    body = trim_paragraphs(body_lines)
    if len(body) < 80:
        warnings.append("Подозрительно короткий текст дня (<80 символов).")
    leaked = [line for line in body_lines if norm(line) in {"поделиться", "политика конфиденциальности"}]
    if leaked:
        warnings.append("В основном тексте обнаружена строка интерфейса.")

    return Day(number, total, body, path.name, len("\n".join(lines)), warnings)


def parse_cover(lines: list[str]) -> tuple[str, str, list[str]]:
    title = get_title(lines)
    notices: list[str] = []

    starts = [i for i, line in enumerate(lines) if norm(line) == "начать этот план"]
    if starts:
        start = starts[-1] + 1
    else:
        # Альтернативная разметка: описание следует за «7 дней» и навигацией.
        durations = [i for i, line in enumerate(lines) if DURATION.fullmatch(line.strip())]
        if not durations:
            notices.append("Не найдено начало описания обложки.")
            return title, "", notices
        start = durations[-1] + 1

    content: list[str] = []
    for line in lines[start:]:
        s = norm(line)
        if s in FOOTER_LINES or any(s.startswith(prefix) for prefix in COVER_FOOTER_PREFIXES):
            break
        if s in HEADER_ONLY or DAY_LINK.fullmatch(s):
            if not content:
                continue
        content.append(line)

    description = trim_paragraphs(content)
    if not description:
        notices.append("Описание плана отсутствует либо не распознано.")
    return title, description, notices


def read_file(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8-sig")
    return raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def sort_key(path: Path) -> tuple:
    return tuple(int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", path.name))


def write_utf8_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(path)


def clean_plan(folder: Path, custom_output: Path | None = None) -> tuple[bool, Path | None]:
    sources = sorted(folder.glob("*.md"), key=sort_key)
    if not sources:
        print(f"[ПРОПУСК] В папке нет .md: {folder}")
        return False, None

    cover: tuple[str, str, list[str]] | None = None
    days: dict[int, Day] = {}
    warnings: list[str] = []
    ignored: list[str] = []
    for source in sources:
        try:
            lines = read_file(source)
        except (UnicodeError, OSError) as exc:
            warnings.append(f"{source.name}: не удалось прочитать файл ({exc}).")
            continue
        day = parse_day(source, lines)
        if day:
            if day.number in days:
                warnings.append(f"Повторяется день {day.number}: {days[day.number].filename} / {source.name}.")
            else:
                days[day.number] = day
                warnings.extend(f"{source.name}: {w}" for w in day.warning)
        elif cover is None:
            # Описание плана обычно хранится отдельным первым файлом.
            candidate = parse_cover(lines)
            if candidate[1]:
                cover = candidate
                warnings.extend(f"{source.name}: {w}" for w in candidate[2])
            else:
                ignored.append(source.name)
        else:
            ignored.append(source.name)

    if not days:
        print(f"[ОШИБКА] В {folder} не найдено ни одного файла с 'День N из M'.")
        return False, None

    sorted_days = sorted(days.values(), key=lambda item: item.number)
    expected_totals = {d.total for d in sorted_days}
    total = max(expected_totals)
    if len(expected_totals) > 1:
        warnings.append(f"Противоречивое количество дней: {sorted(expected_totals)}.")
    missing = [n for n in range(1, total + 1) if n not in days]
    if missing:
        warnings.append("Отсутствуют дни: " + ", ".join(map(str, missing)) + ".")
    if cover is None:
        warnings.append("Обложка не найдена: общее описание и авторство могут отсутствовать.")
        first_lines = read_file(folder / sorted_days[0].filename)
        title = get_title(first_lines)
        description = ""
    else:
        title, description, _ = cover

    parts = [f"# {title}"]
    if description:
        parts.extend(["## О плане", description])
    for day in sorted_days:
        parts.extend([f"## День {day.number}", day.body])
    result = "\n\n".join(parts).rstrip() + "\n"

    # «cleaned» внутри плана; .md исходники в корне и не переписываются.
    output = custom_output or folder / "cleaned" / (folder.name + "_full.md")
    report = output.with_name(output.stem + "_report.txt")
    if output.resolve() in {p.resolve() for p in sources}:
        print("[ОШИБКА] Файл вывода совпадает с исходным .md. Выберите другой путь.")
        return False, None
    if output.suffix.lower() != ".md":
        print("[ОШИБКА] Файл вывода должен иметь расширение .md.")
        return False, None

    status = "ПРОВЕРЬТЕ ПРЕДУПРЕЖДЕНИЯ" if warnings else "ГОТОВО"
    log = [
        f"Статус: {status}", f"Папка: {folder}", f"Результат: {output}",
        f"Найдено дней: {len(days)} из {total}",
        f"Обложка: {'да' if cover else 'нет'}",
        "", "Файлы дней:",
    ]
    log.extend(f"  День {d.number}: {d.filename} | текст {len(d.body)} символов" for d in sorted_days)
    if ignored:
        log.extend(["", "Проигнорированные .md:", *("  " + f for f in ignored)])
    if warnings:
        log.extend(["", "ВНИМАНИЕ — проверьте:", *("  - " + w for w in warnings)])
    else:
        log.extend(["", "Все ожидаемые дни присутствуют; структура распознана."])
    log.extend(["", "Исходные файлы не изменены.", ""])

    write_utf8_atomic(output, result)
    write_utf8_atomic(report, "\n".join(log))
    print(f"[{status}] {folder.name}: {len(days)}/{total} дней")
    print(f"  Объединённый Markdown: {output}")
    print(f"  Отчёт проверки:       {report}")
    for warning in warnings:
        print(f"  ВНИМАНИЕ: {warning}")
    return not warnings, output


def choose_folder() -> Path | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        chosen = filedialog.askdirectory(title="Выберите папку с .md одного плана или data")
        root.destroy()
        if chosen:
            return Path(chosen)
        return None
    except Exception:  # Tk может отсутствовать или быть без графического дисплея.
        if not sys.stdin.isatty():
            return None
        entered = input("Введите путь к папке с .md: ").strip().strip('"')
        return Path(entered) if entered else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Удаляет навигацию YouVersion и собирает все дни плана в один Markdown."
    )
    parser.add_argument("folder", nargs="?", help="Папка с .md или родительская папка с планами")
    parser.add_argument("--output", help="Путь к итоговому .md (только для одного плана)")
    args = parser.parse_args()

    folder = Path(args.folder).expanduser() if args.folder else choose_folder()
    if folder is None:
        print("Папка не выбрана. Ничего не изменено.")
        return 1
    if not folder.is_dir():
        print(f"[ОШИБКА] Папка не найдена: {folder}")
        return 1

    if list(folder.glob("*.md")):
        folders = [folder]
    else:
        folders = sorted(
            (p for p in folder.iterdir() if p.is_dir() and list(p.glob("*.md"))),
            key=sort_key,
        )
    if not folders:
        print(f"[ОШИБКА] Нет Markdown-файлов в папке или её непосредственных подпапках: {folder}")
        return 1
    if args.output and len(folders) != 1:
        print("[ОШИБКА] --output поддерживается только для одной папки-плана.")
        return 1

    good = 0
    for plan in folders:
        ok, _ = clean_plan(plan, Path(args.output).expanduser() if args.output else None)
        good += int(ok)
    print(f"\nБез предупреждений: {good} из {len(folders)} планов.")
    return 0 if good == len(folders) else 2


if __name__ == "__main__":
    raise SystemExit(main())
