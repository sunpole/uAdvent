"""Сборка плана из ручных копий русскоязычных страниц YouVersion."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
INPUTS = ROOT / "Исходники"
OUTPUTS = ROOT / "Готовые планы"
HEADER = ("Перейти к содержимому", "Главная", "Библия", "Стих дня", "Видео", "Планы",
          "Русский", "Ищите стихи, темы и вопросы...", "Скачать приложение")
FOOTER = ("Блог", "Переводы Библии", "Языки текста Библии", "Помощь", "Обратная связь",
          "Пожертвования", "О нас", "Уголок прессы", "Церкви", "Разработчики",
          "Программа раскрытия уязвимостей", "Политика конфиденциальности", "Условия")
PROMO = "Приложение Библия абсолютно бесплатное, без рекламы и покупок внутри приложения. Скачать приложение"
START = "Начать этот план"
RELATED = "Планы, относящиеся к теме"
COPYRIGHT = re.compile(r"© [0-9]{4} YouVersion")
DAY = re.compile(r"День ([1-9][0-9]*) из ([1-9][0-9]*)")
DURATION = re.compile(r"([1-9][0-9]*) (?:день|дня|дней)")
UNAVAILABLE = "Некоторые отрывки из Священного Писания недоступны в используемом вами переводе Библии."


class InputError(ValueError):
    pass


@dataclass
class Source:
    path: Path
    lines: list[str]
    digest: str

    @classmethod
    def read(cls, path: Path) -> Source:
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise InputError(f"{path.name}: сохраните копию в кодировке UTF-8.") from exc
        # Только физические переводы строк. Например, U+2028 внутри абзаца — текст.
        lines = re.findall(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z", text)
        return cls(path, lines, hashlib.sha256(raw).hexdigest())


@dataclass
class Fragment:
    source: Source
    start: int
    stop: int
    label: str

    @property
    def text(self) -> str:
        return "".join(self.source.lines[self.start:self.stop])


class Page:
    """Указатели на непустые строки для распознавания. Текст берём из Source."""

    def __init__(self, source: Source, indexes: list[int]):
        self.source = source
        self.indexes = indexes
        self.keys = [source.lines[i].strip(" \t\r\n") for i in indexes]

    def fail(self, message: str, pos: int = 0):
        line = self.indexes[min(max(0, pos), len(self.indexes) - 1)] + 1
        raise InputError(f"{self.source.path.name}, строка {line}. {message}")

    def expect(self, pos: int, *keys: str) -> int:
        for i, key in enumerate(keys, pos):
            if i >= len(self.keys) or self.keys[i] != key:
                self.fail(f"Ожидалась строка «{key}». Проверьте полноту копии страницы.", i)
        return pos + len(keys)

    def line(self, pos: int, label: str) -> Fragment:
        i = self.indexes[pos]
        return Fragment(self.source, i, i + 1, label)

    def between(self, left: int, right: int, label: str) -> Fragment:
        return Fragment(self.source, self.indexes[left] + 1, self.indexes[right], label)


@dataclass
class Entry:
    title: Fragment
    total: int
    number: int
    heading: Fragment
    body: Fragment
    alternate: Fragment | None = None
    description: Fragment | None = None
    credit: Fragment | None = None


def without_recommendations(page: Page, start: int, end: int, publisher_prefix: str) -> int:
    """У текущего шаблона каждая карточка представлена парой одинаковых названий."""
    keys = page.keys
    markers = [i for i in range(start + 1, end)
               if keys[i] == RELATED and keys[i - 1].startswith(publisher_prefix)]
    if not markers:
        return end
    if len(markers) != 1:
        page.fail("Неоднозначные границы списка рекомендаций.", markers[0])
    marker = markers[0]
    cards = keys[marker + 1:end]
    if cards[::2] != cards[1::2]:
        page.fail("Незнакомая структура рекомендаций.", marker)
    return marker


def parse_cover(page: Page, start: int, end: int) -> Entry:
    keys = page.keys
    title = page.expect(start, "App icon", PROMO)
    if title + 5 >= end:
        page.fail("Не хватает строк обложки.", title)
    page.expect(title + 1, "Поделиться", keys[title])
    duration = title + 3
    match = DURATION.fullmatch(keys[duration])
    if not match:
        page.fail("Не найдено количество дней плана.", duration)
    body = page.expect(duration + 1, "День 1", START)
    end = without_recommendations(page, body, end, "Больше от ")
    if end <= body + 1 or not keys[end - 1].startswith("Больше от "):
        page.fail("Не распознана граница описания с издателем.", end - 1)
    return Entry(page.line(title + 2, "Название"), int(match[1]), 0,
                 page.line(duration, "Продолжительность"),
                 page.between(body - 1, end - 1, "Описание и издатель"))


def parse_day(page: Page, start: int, end: int) -> Entry:
    keys = page.keys
    title = page.expect(start, "О плане", "Поделиться", "Значок БиблииСкачать приложение")
    if title + 3 >= end:
        page.fail("Не хватает заголовка дня.", title)
    page.expect(title + 1, "Пример")
    header = title + 3
    match = DAY.fullmatch(keys[header])
    if not match:
        page.fail("Ожидался заголовок «День N из M».", header)
    number, total = map(int, match.groups())
    if number > total or total > end - header - 1:
        page.fail("Номер дня или длина календаря не совпадают с заголовком.", header)
    for n in range(1, total + 1):
        page.expect(header + n, str(n))
    last_button = header + total

    # Читаем низ страницы по порядку. Совпадение отдельного слова внутри
    # авторского абзаца или отдельной строки «День 2» ничего не отсекает.
    nav = ([f"День {number - 1}"] if number > 1 else []) + [START]
    if number < total:
        nav.append(f"День {number + 1}")
    nav += ["Об этом плане", keys[title + 2]]
    candidates = [i for i in range(last_button + 1, end)
                  if keys[i:i + len(nav)] == nav]
    if len(candidates) != 1:
        page.fail("Не удалось однозначно определить конец чтения и кнопки дня.", header)
    stop = candidates[0]
    if stop == last_button + 1:
        page.fail("Текст дня пуст.", stop)
    about_title = stop + len(nav) - 1
    end = without_recommendations(page, about_title + 1, end, "Об издателе")
    if keys[end - 1] != "Об издателе":
        page.fail("Не распознан конец сведений об издателе.", end - 1)
    more = [i for i in range(about_title + 1, end - 1) if keys[i] == "More"]
    if len(more) != 1 or more[0] <= about_title + 1 or more[0] >= end - 2:
        page.fail("Не распознаны описание и сведения об издателе.", about_title)
    return Entry(page.line(title, "Название"), total, number,
                 page.line(header, f"Заголовок дня {number}"),
                 page.between(last_button, stop, f"Текст дня {number}"),
                 page.line(title + 2, "Название в оригинале"),
                 page.between(about_title, more[0], "Описание"),
                 page.between(more[0], end - 1, "Издатель"))


def read_entries(source: Source) -> list[Entry]:
    indexes = [i for i, line in enumerate(source.lines) if line.strip(" \t\r\n")]
    if not indexes:
        raise InputError(f"{source.path.name}: пустой файл.")
    doc = Page(source, indexes)
    keys = doc.keys
    starts = [i for i, key in enumerate(keys)
              if key == HEADER[0] and keys[i:i + len(HEADER)] == list(HEADER)]
    if not starts or starts[0] > 1:
        doc.fail("Нужна полная копия русской страницы от верхнего меню до © YouVersion.")
    entries = []
    for page_no, start in enumerate(starts):
        stop = starts[page_no + 1] if page_no + 1 < len(starts) else len(keys)
        ends = [i for i in range(start + len(HEADER), stop) if COPYRIGHT.fullmatch(keys[i])
                and keys[i-len(FOOTER):i] == list(FOOTER)]
        if len(ends) != 1:
            doc.fail("Страница оборвана или нижнее меню сайта изменилось.", start)
        end = ends[0]
        page = Page(source, indexes[start:end + 1])
        p = len(HEADER)
        footer = len(page.keys) - len(FOOTER) - 1
        entry = (parse_cover if page.keys[p] == "App icon" else parse_day)(page, p, footer)
        entries.append(entry)
        # Заголовок вкладки бывает выше меню или после ©.
        extras = keys[end + 1:stop]
        if page_no == 0:
            extras = keys[:start] + extras
        title = entry.title.text.strip(" \t\r\n")
        if any(x not in (title + " Пример", title + " - Приложение Библия") for x in extras):
            doc.fail("Вне границ страницы есть неизвестный текст. Проверьте склейку.", end)
    return entries


def content_lines(fragment: Fragment) -> list[str]:
    # Для сравнения убираем только пустые строки на стыке с кнопками.
    # В результат всегда идёт исходный фрагмент целиком.
    lines = [s.rstrip("\r\n") for s in fragment.source.lines[fragment.start:fragment.stop]]
    start, end = 0, len(lines)
    while start < end and lines[start] == "":
        start += 1
    while end > start and lines[end - 1] == "":
        end -= 1
    return lines[start:end]


def assemble(entries: list[Entry]) -> tuple[list[Fragment], int]:
    covers = [e for e in entries if e.number == 0]
    days = sorted((e for e in entries if e.number), key=lambda e: e.number)
    if len(covers) > 1 or not days:
        raise InputError("Нужны дни одного плана и не более одной обложки.")
    first = days[0]
    if any(e.total != first.total or content_lines(e.title) != content_lines(first.title)
           for e in entries):
        raise InputError("В копиях различаются название или продолжительность плана.")
    numbers = [e.number for e in days]
    if len(set(numbers)) != len(numbers):
        raise InputError("Есть повторный день. Оставьте одну полную копию каждого дня.")
    missing = sorted(set(range(1, first.total + 1)) - set(numbers))
    if missing:
        raise InputError("Пропущены дни: " + ", ".join(map(str, missing)) + ".")
    for day in days[1:]:
        for a, b in ((first.alternate, day.alternate), (first.description, day.description),
                     (first.credit, day.credit)):
            if content_lines(a) != content_lines(b):
                raise InputError(f"День {day.number}: сведения о плане отличаются. Сверьте копии.")
    cover = covers[0] if covers else None
    pieces = [cover.title if cover else first.title]
    if first.alternate.text.strip(" \t\r\n") != first.title.text.strip(" \t\r\n"):
        pieces.append(first.alternate)
    if cover:
        all_lines = content_lines(cover.body)
        description = content_lines(first.description)
        credit = content_lines(first.credit)
        gap = all_lines[len(description):len(all_lines) - len(credit)]
        if (all_lines[:len(description)] != description or all_lines[-len(credit):] != credit
                or len(all_lines) < len(description) + len(credit) or any(gap)):
            raise InputError("Описание или издатель на обложке и в сведениях дня различаются.")
        pieces.extend([cover.heading, cover.body])
    else:
        pieces.extend([first.description, first.credit])
    for day in days:
        pieces.extend([day.heading, day.body])
    return pieces, first.total


def build(input_path: Path, output_root: Path = OUTPUTS) -> tuple[Path, int]:
    source_path = input_path.resolve()
    if source_path.is_dir():
        paths = sorted(p for p in source_path.iterdir() if p.is_file() and p.suffix.lower() in (".txt", ".md"))
        name = source_path.name
    elif source_path.is_file() and source_path.suffix.lower() in (".txt", ".md"):
        paths, name = [source_path], source_path.stem
    else:
        raise InputError("Выберите папку одного плана или его общий файл .txt или .md.")
    if not paths:
        raise InputError("В выбранной папке нет исходных файлов .txt или .md.")
    sources = [Source.read(p) for p in paths]
    pieces, total = assemble([entry for source in sources for entry in read_entries(source)])
    result = "".join(f.text for f in pieces).encode("utf-8")
    report_lines = [f"Проверено дней: {total} из {total}", "Исходные файлы и SHA-256:"]
    report_lines.extend(f"{s.path.name}  {s.digest}" for s in sources)
    report_lines.extend(["", "Фрагменты в порядке записи. Номера строк включают обе границы:"])
    report_lines.extend(f"{f.label} | {f.source.path.name} | {f.start + 1}–{f.stop}" for f in pieces)
    for f in pieces:
        if UNAVAILABLE in content_lines(f):
            report_lines.append(f"{f.label}: в копии есть сообщение о недоступных отрывках. Оно сохранено.")
    report_lines.extend(["", f"SHA-256 готового текста: {hashlib.sha256(result).hexdigest()}"])
    report = ("\n".join(report_lines) + "\n").encode("utf-8")
    output_root.mkdir(parents=True, exist_ok=True)
    # Публикуем пару файлов одним переименованием. Существующие версии сохраняются.
    with tempfile.TemporaryDirectory(dir=output_root) as temp:
        staged = Path(temp) / "сборка"
        staged.mkdir()
        (staged / "План.md").write_bytes(result)
        (staged / "Сверка.txt").write_bytes(report)
        version = 1
        while True:
            dest = output_root / (name if version == 1 else f"{name} ({version})")
            if dest.is_dir():
                try:
                    if (dest / "План.md").read_bytes() == result and (dest / "Сверка.txt").read_bytes() == report:
                        return dest / "План.md", total
                except FileNotFoundError:
                    pass
            elif not dest.exists():
                try:
                    staged.rename(dest)
                    return dest / "План.md", total
                except OSError:
                    if not dest.exists():
                        raise
                    continue
            version += 1


def choose_folder(output_root: Path) -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox

    root = tk.Tk()
    root.withdraw()
    try:
        folder = filedialog.askdirectory(parent=root, title="Выберите папку с копиями одного плана",
                                         initialdir=INPUTS, mustexist=True)
        if not folder:
            return 0
        try:
            result, days = build(Path(folder), output_root)
        except (ValueError, OSError) as exc:
            messagebox.showerror("План не собран", str(exc), parent=root)
            return 1
        if messagebox.askyesno("План готов", f"Собрано дней: {days}.\n\n{result}\n\nОткрыть папку результата?", parent=root):
            os.startfile(result.parent)
        return 0
    finally:
        root.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, add_help=False,
                                     usage="cleaner.py [ИСТОЧНИК] [--вывод ПАПКА]")
    options = parser.add_argument_group("Параметры")
    options.add_argument("-h", "--help", action="help", help="показать справку")
    options.add_argument("source", metavar="ИСТОЧНИК", type=Path, nargs="?",
                         help="папка одного плана или файл .md/.txt")
    options.add_argument("--вывод", dest="output", metavar="ПАПКА", type=Path,
                         default=OUTPUTS, help="папка для результатов")
    args = parser.parse_args()
    if args.source is None:
        return choose_folder(args.output)
    try:
        result, days = build(args.source, args.output)
    except (ValueError, OSError) as exc:
        print(f"План не собран. {exc}", file=sys.stderr)
        return 1
    print(f"Готово. Дней: {days}. Файл: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
