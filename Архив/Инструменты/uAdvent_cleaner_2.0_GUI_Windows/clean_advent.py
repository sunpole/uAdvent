#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""uAdvent Cleaner 2.0 — объединение страниц планов YouVersion в Markdown.

Поддерживает:
- отдельные файлы: обложка + день 1 + день 2 + ...;
- один длинный Markdown, в котором последовательно скопированы обложка и все дни;
- смешанный вариант: несколько страниц/дней в каждом файле.

Нужен Python 3.9+; сторонние библиотеки не нужны.
Исходные файлы никогда не меняются.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

DAY_HEADER = re.compile(r"^День\s+(\d+)\s+из\s+(\d+)\s*$", re.IGNORECASE)
DAY_LINK = re.compile(r"^День\s+\d+\s*$", re.IGNORECASE)
PAGINATION = re.compile(r"^\d{1,3}$")
PLAN_DURATION = re.compile(r"^\d+\s+(?:день|дня|дней)\s*$", re.IGNORECASE)
PAGE_START = "перейти к содержимому"
END_BODY_LINES = {"начать этот план", "об этом плане", "об издателе", "планы, относящиеся к теме"}
COVER_END_LINES = {"об издателе", "планы, относящиеся к теме", "блог", "more"}
SITE_LINES = {
    PAGE_START, "главная", "библия", "стих дня", "видео", "планы", "русский",
    "ищите стихи, темы и вопросы...", "скачать приложение", "о плане",
    "поделиться", "пример", "app icon", "значок библиискачать приложение",
    "переводы библии", "языки текста библии", "блог", "о нас", "уголок прессы",
    "помощь", "обратная связь", "пожертвования", "церкви", "разработчики",
    "политика конфиденциальности", "программа раскрытия уязвимостей", "условия",
}


def norm(s: str) -> str:
    """Только для сравнения — текст пользователя не редактируем."""
    return re.sub(r"\s+", " ", s.strip().lstrip("# ")).casefold()


def text_lines(s: str) -> list[str]:
    return s.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def normalise_block(lines: list[str]) -> str:
    """Сохраняет оригинальные слова, знаки препинания и пустые строки."""
    output: list[str] = []
    for line in lines:
        clean = line.rstrip()  # только конечные пробелы
        if clean.strip():
            output.append(clean)
        elif output and output[-1] != "":
            output.append("")
    while output and output[-1] == "":
        output.pop()
    return "\n".join(output)


def split_pages(lines: list[str]) -> list[list[str]]:
    """Делит длинную копию сайта на отдельные страницы, если есть навигационные шапки."""
    positions = [i for i, line in enumerate(lines) if norm(line) == PAGE_START]
    if not positions:
        # Резерв: страницы склеены без повторяющейся шапки сайта.
        day_positions = [i for i, line in enumerate(lines) if DAY_HEADER.fullmatch(line.strip())]
        if day_positions and (len(day_positions) > 1 or any(PLAN_DURATION.fullmatch(ln.strip()) for ln in lines[:day_positions[0]])):
            result = []
            if day_positions[0] > 0:
                result.append(lines[:day_positions[0]])
            result.extend(lines[a:b] for a, b in zip(day_positions, day_positions[1:] + [len(lines)]))
            return result
        return [lines]

    result: list[list[str]] = []
    for j, start in enumerate(positions):
        end = positions[j + 1] if j + 1 < len(positions) else len(lines)
        if j == 0:
            start = 0  # заголовок вкладки до первого "Перейти к содержимому"
        result.append(lines[start:end])
    return result


def cover_title(lines: list[str]) -> str:
    # На странице обложки после "Поделиться" идёт название, затем "7 дней".
    for i, line in enumerate(lines):
        if norm(line) == "поделиться":
            tail = [v.strip() for v in lines[i + 1:i + 6] if v.strip()]
            if len(tail) >= 2 and PLAN_DURATION.fullmatch(tail[1]):
                return tail[0]
    # Фоллбек: название непосредственно перед "7 дней".
    for i, line in enumerate(lines):
        if PLAN_DURATION.fullmatch(line.strip()):
            for prev in reversed(lines[max(0, i - 3):i]):
                if prev.strip() and norm(prev) not in SITE_LINES:
                    return prev.strip()
    # Фоллбек для одиночной страницы дня.
    for line in lines:
        cleaned = re.sub(r"\s+-\s+Приложение Библия\s*$", "", line.strip(), flags=re.I)
        cleaned = re.sub(r"\s+Пример\s*$", "", cleaned, flags=re.I)
        if cleaned and norm(cleaned) not in SITE_LINES and not cleaned.startswith("Приложение Библия"):
            return cleaned
    return "План чтения"


@dataclass
class Cover:
    title: str
    description: str
    source: str


@dataclass
class Day:
    number: int
    total: int
    body: str
    source: str
    warnings: list[str] = field(default_factory=list)


def parse_cover(lines: list[str], source: str) -> Cover | None:
    # Обложка распознаётся по продолжительности плана и кнопке старта,
    # но не должна содержать "День N из M".
    if any(DAY_HEADER.fullmatch(line.strip()) for line in lines):
        return None
    if not any(PLAN_DURATION.fullmatch(line.strip()) for line in lines):
        return None
    start = next((i for i, line in enumerate(lines) if norm(line) == "начать этот план"), None)
    if start is None:
        return Cover(cover_title(lines), "", source)

    content: list[str] = []
    for line in lines[start + 1:]:
        n = norm(line)
        if n in COVER_END_LINES or n.startswith("больше от ") or n.startswith("© "):
            break
        content.append(line)
    return Cover(cover_title(lines), normalise_block(content), source)


def parse_day(lines: list[str], source: str) -> Day | None:
    located = next(((i, DAY_HEADER.fullmatch(line.strip())) for i, line in enumerate(lines)
                    if DAY_HEADER.fullmatch(line.strip())), None)
    if located is None:
        return None
    header_idx, match = located
    assert match is not None
    number, total = int(match.group(1)), int(match.group(2))
    warnings: list[str] = []
    if number < 1 or total < 1 or number > total:
        warnings.append("Некорректный номер дня или общее количество дней.")

    # Сразу после "День N из M" сайт показывает кликабельные номера 1...N.
    start = header_idx + 1
    while start < len(lines) and (not lines[start].strip() or PAGINATION.fullmatch(lines[start].strip())):
        start += 1

    # Окончание — начало элемента навигации, а НЕ поиск одинаковых слов в абзацах.
    stop = None
    for i in range(start, len(lines)):
        if norm(lines[i]) in END_BODY_LINES:
            stop = i
            break
        if norm(lines[i]) == PAGE_START:
            stop = i
            break
    if stop is None:
        stop = len(lines)
        warnings.append("Не найден стандартный конец страницы после текста дня.")

    core = lines[start:stop]
    # В навигации перед кнопкой "Начать этот план" находится "День 4".
    # Удаляем его только если этот токен — последняя непустая строка после
    # библейских ссылок или непосредственно перед концом сайта.
    while core and not core[-1].strip():
        core.pop()
    if core and DAY_LINK.fullmatch(core[-1].strip()):
        # Обычная ссылка на соседний день, никогда не "День 1: Давид".
        core.pop()
    body = normalise_block(core)

    if len(body) < 80:
        warnings.append(f"Слишком короткое содержание дня ({len(body)} символов).")
    if "Священное Писание" not in body:
        warnings.append("Не найден раздел «Священное Писание» — проверьте полноту.")
    if any(norm(line) in {"политика конфиденциальности", "об этом плане", "перейти к содержимому"}
           for line in body.splitlines()):
        warnings.append("В содержании обнаружена служебная строка сайта.")
    return Day(number, total, body, source, warnings)


def sort_key(path: Path) -> tuple:
    return tuple(int(v) if v.isdigit() else v.casefold() for v in re.split(r"(\d+)", path.name))


def atomic_write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(value)
    temp.replace(path)


def clean_plan(folder: Path, output_override: Path | None = None) -> tuple[int, Path | None]:
    sources = sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".md"), key=sort_key)
    if not sources:
        print(f"[ПРОПУСК] Нет исходных .md в {folder}")
        return 1, None

    output = output_override or folder / "cleaned" / (folder.name + "_full.md")
    if output.suffix.lower() != ".md" or output.resolve() in {p.resolve() for p in sources}:
        print(f"[ОШИБКА] Недопустимый путь результата: {output}")
        return 1, None

    cover: Cover | None = None
    days: dict[int, Day] = {}
    warnings: list[str] = []
    other: list[str] = []
    processed_pages = 0
    for path in sources:
        try:
            content = path.read_text(encoding="utf-8-sig")
        except (UnicodeError, OSError) as e:
            warnings.append(f"{path.name}: ошибка чтения: {e}")
            continue
        for page_number, page in enumerate(split_pages(text_lines(content)), start=1):
            processed_pages += 1
            source = f"{path.name} (страница {page_number})"
            day = parse_day(page, source)
            if day is not None:
                if day.number in days:
                    warnings.append(f"Повтор дня {day.number}: {days[day.number].source}; {source}. Первый сохранён.")
                else:
                    days[day.number] = day
                    warnings.extend(f"{source}: {w}" for w in day.warnings)
                continue
            this_cover = parse_cover(page, source)
            if this_cover is not None:
                if cover is None:
                    cover = this_cover
                else:
                    warnings.append(f"Повторная обложка: {source}.")
            elif any(line.strip() for line in page):
                other.append(source)

    if not days:
        print(f"[ОШИБКА] {folder}: ни одного дня с заголовком 'День N из M'.")
        return 1, None

    ordered = sorted(days.values(), key=lambda d: d.number)
    totals = sorted({d.total for d in ordered})
    total = max(totals)
    if len(totals) > 1:
        warnings.append(f"Противоречивое количество дней на страницах: {totals}.")
    missing = sorted(set(range(1, total + 1)) - set(days))
    if missing:
        warnings.append("Не найдены дни: " + ", ".join(map(str, missing)))
    if not cover:
        warnings.append("Не найдена обложка: название, описание и авторство могли отсутствовать в источниках.")
        # Для плана без обложки берём заголовок сайта до первого дня.
        title = cover_title(text_lines(sources[0].read_text(encoding="utf-8-sig")))
        description = ""
    else:
        title, description = cover.title, cover.description
        if not description:
            warnings.append("На обложке нет распознанного описания.")

    combined = [f"# {title}"]
    if description:
        combined.extend(["## О плане", description])
    for d in ordered:
        combined.extend([f"## День {d.number}", d.body])
    merged = "\n\n".join(combined).rstrip() + "\n"

    state = "ГОТОВО" if not warnings else "ПРОВЕРЬТЕ ПРЕДУПРЕЖДЕНИЯ"
    report = [
        f"Статус: {state}", f"Папка: {folder}", f"Результат: {output}",
        f"Название плана: {title}",
        f"Исходных Markdown-файлов: {len(sources)}", f"Просмотрено страниц: {processed_pages}",
        f"Найдено дней: {len(days)} из {total}", f"Обложка: {'да' if cover else 'нет'}",
        "", "Содержание по дням:",
    ]
    report.extend(f"  День {d.number}: {len(d.body)} символов | {d.source}" for d in ordered)
    if cover:
        report.extend(["", f"Обложка: {cover.source}"])
    if other:
        report.extend(["", "Неопознанные части (проверьте при необходимости):", *["  " + v for v in other]])
    if warnings:
        report.extend(["", "Предупреждения:", *["  - " + v for v in warnings]])
    else:
        report.extend(["", "Все заявленные дни присутствуют, границы страниц распознаны."])
    report.extend(["", "Оригинальные Markdown-файлы не изменены.", ""])
    report_file = output.with_name(output.stem + "_report.txt")
    try:
        atomic_write(output, merged)
        atomic_write(report_file, "\n".join(report))
    except OSError as e:
        print(f"[ОШИБКА] Не удалось записать результат: {e}")
        return 1, None

    print(f"[{state}] {folder.name}: {len(days)}/{total} дней, файлов: {len(sources)}, страниц: {processed_pages}")
    print(f"  Итог:   {output}")
    print(f"  Отчёт:  {report_file}")
    for issue in warnings:
        print(f"  ВНИМАНИЕ: {issue}")
    return 0 if not warnings else 2, output


def choose_folder() -> Path | None:
    try:
        from tkinter import Tk, filedialog
        root = Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selection = filedialog.askdirectory(title="Выберите папку плана или папку со всеми планами")
        root.destroy()
        return Path(selection) if selection else None
    except Exception:
        value = input("Введите путь к папке с .md: ").strip().strip('"')
        return Path(value) if value else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Очищает Markdown-планы YouVersion и объединяет все дни.")
    parser.add_argument("folder", nargs="?", help="Папка одного плана либо родительский каталог")
    parser.add_argument("--output", help="Итоговый Markdown (только для одного плана)")
    args = parser.parse_args(argv)
    folder = Path(args.folder).expanduser() if args.folder else choose_folder()
    if folder is None:
        print("Папка не выбрана; ничего не изменено.")
        return 1
    if not folder.is_dir():
        print(f"[ОШИБКА] Папка не найдена: {folder}")
        return 1

    if any(p.is_file() and p.suffix.lower() == ".md" for p in folder.iterdir()):
        folders = [folder]
    else:
        folders = sorted((p for p in folder.iterdir() if p.is_dir()
                          and any(f.is_file() and f.suffix.lower() == ".md" for f in p.iterdir())),
                         key=sort_key)
    if not folders:
        print(f"[ОШИБКА] Нет .md в папке или её непосредственных подпапках: {folder}")
        return 1
    if args.output and len(folders) != 1:
        print("[ОШИБКА] --output допускается только для одного плана.")
        return 1

    statuses = []
    for path in folders:
        status, _ = clean_plan(path, Path(args.output).expanduser() if args.output else None)
        statuses.append(status)
    print(f"\nПланов обработано: {sum(s in (0, 2) for s in statuses)} из {len(folders)}")
    if 1 in statuses:
        return 1
    if 2 in statuses:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
