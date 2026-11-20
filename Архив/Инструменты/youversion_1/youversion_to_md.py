# -*- coding: utf-8 -*-
"""Сохранение исходного видимого текста дней плана YouVersion в Markdown.

Зависимость: pip install playwright
Браузер: установленный Microsoft Edge (Windows).
Загружаются только публичные страницы; авторизация и ограничения не обходятся.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

PLAN_ID = 29132
DAYS = 7
BASE = f"https://www.bible.com/reading-plans/{PLAN_ID}/day"
OUT_DIR = Path(__file__).resolve().parent / "YouVersion_29132"


class ExtractionError(RuntimeError):
    pass


def without_blank_edges(lines: list[str]) -> list[str]:
    result = list(lines)
    while result and not result[0].strip():
        result.pop(0)
    while result and not result[-1].strip():
        result.pop()
    return result


def extract_day(body_text: str, day: int) -> str:
    """Извлечь только видимый текст чтения + перечень библейских ссылок.

    Не перефразирует текст, не исправляет орфографию, не переводит,
    сохраняет эмодзи и пунктуацию как браузер показывает на странице.
    """
    lines = body_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    header = re.compile(rf"\s*DAY\s+{day}\s+OF\s+{DAYS}\s*", re.IGNORECASE)
    starts = [i for i, line in enumerate(lines) if header.fullmatch(line)]
    if len(starts) != 1:
        raise ExtractionError(f"Не найден однозначный заголовок 'DAY {day} OF {DAYS}'.")

    # Ссылки дней могут выглядеть как "1 2 3 4 5 6 7" на одной строке
    # или как семь отдельных строк. Удаляем их только при полном совпадении.
    after_header = "\n".join(lines[starts[0] + 1:])
    menu_pattern = r"^\s*" + r"\s*".join(str(n) for n in range(1, DAYS + 1)) + r"(?=\s|$)"
    menu_match = re.match(menu_pattern, after_header)
    if not menu_match:
        raise ExtractionError("Не удалось подтвердить навигационный список дней 1–7.")
    lines = after_header[menu_match.end():].split("\n")
    index = 0

    # Названия из интерфейса — исключительно граница; внутри материала ничего не меняем.
    scripture_heading = re.compile(r"\s*(Scripture|Писание|Места Писания)\s*", re.IGNORECASE)
    scripture_matches = [i for i in range(index, len(lines)) if scripture_heading.fullmatch(lines[i])]
    if not scripture_matches:
        raise ExtractionError("Не найден блок библейских ссылок Scripture / Писание.")
    scripture_index = scripture_matches[0]
    main_lines = without_blank_edges(lines[index:scripture_index])
    if not main_lines:
        raise ExtractionError("Основной текст дня пустой.")

    # После списка мест Писания идут ссылки на соседние дни, кнопка запуска и описание плана.
    rest = "\n".join(lines[scripture_index:])
    end_match = re.search(r"(?<![\w])(?:Day\s+[1-7]|Start this Plan|About this Plan)(?![\w])", rest, re.IGNORECASE)
    if not end_match:
        raise ExtractionError("Не найдена граница окончания блока Scripture.")
    scripture_lines = without_blank_edges(rest[:end_match.start()].split("\n"))
    if len("\n".join(main_lines)) < 100:
        raise ExtractionError("Основной текст неожиданно короткий (менее 100 символов).")
    if len(scripture_lines) < 2:
        raise ExtractionError("Не найдено ни одной библейской ссылки после Scripture.")
    content = "\n".join(main_lines) + "\n\n" + "\n".join(scripture_lines)
    if any(s in content for s in ("Start this Plan", "About this Plan", "Get the app", "Loading…")):
        raise ExtractionError("В выделенный текст попал интерфейс сайта.")
    return content


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    except ModuleNotFoundError:
        print("ОШИБКА: Playwright не установлен. Запустите START.cmd.")
        return 2

    results: list[str] = []
    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(channel="msedge", headless=True)
            except Exception as exc:
                raise ExtractionError("Не удалось запустить Microsoft Edge. "
                                      "Установите/обновите Edge в Windows.") from exc
            try:
                context = browser.new_context(locale="en-US", viewport={"width": 1360, "height": 900})
                page = context.new_page()
                for day in range(1, DAYS + 1):
                    url = f"{BASE}/{day}?segment=0"
                    print(f"[{day}/{DAYS}] Читаю {url}", flush=True)
                    try:
                        response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                        if response is None or response.status >= 400:
                            raise ExtractionError(f"HTTP-ошибка: {response.status if response else 'нет ответа'}")
                        if not re.search(rf"/(?:[a-z]{{2}}/)?reading-plans/{PLAN_ID}(?:-[^/]+)?/day/{day}(?:[?#]|$)", page.url):
                            raise ExtractionError(f"Страница перенаправила на другой адрес: {page.url}")
                        # Дождаться реального видимого материала, а не 'Loading…'.
                        page.wait_for_function(
                            "(v) => {const t=document.body?.innerText || ''; "
                            "return t.includes('DAY ' + v.day + ' OF ' + v.days) && "
                            "/(?:^|\\n)(?:Scripture|Писание|Места Писания)(?:\\n|$)/i.test(t);}",
                            arg={"day": day, "days": DAYS}, timeout=25000
                        )
                        visible_text = page.locator("body").inner_text(timeout=10000)
                        article = extract_day(visible_text, day)
                    except PlaywrightTimeoutError as exc:
                        raise ExtractionError(f"День {day}: сайт не отдал читаемый материал вовремя") from exc
                    except ExtractionError as exc:
                        raise ExtractionError(f"День {day}: {exc}") from exc
                    results.append(article)
                    print(f"    OK, {len(article)} символов; начало: {article[:65]!r}", flush=True)
            finally:
                browser.close()

        # Не писать общий файл, пока не проверены ВСЕ 7 дней.
        if len(results) != DAYS or len(set(results)) != DAYS:
            raise ExtractionError("Не все дни извлечены либо среди них обнаружены дубликаты")

        # Дополнительная контрольная точка для известного второго дня.
        if "Зачем пришёл Иисус" not in results[1] or "Римлянам 6:23" not in results[1]:
            raise ExtractionError("Проверка дня 2 не прошла. Содержание страницы изменилось?")

        # Новый каталог на каждый запуск, чтобы не спутать новые результаты со старыми.
        output = OUT_DIR / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output.mkdir(parents=True, exist_ok=False)
        for day, article in enumerate(results, 1):
            (output / f"day_{day:02d}.md").write_text(article + "\n", encoding="utf-8")

        # Разделитель --- — только разметка Markdown между днями, не часть авторского текста.
        (output / "all_7_days.md").write_text("\n\n---\n\n".join(results) + "\n", encoding="utf-8")
        print("\nГОТОВО. Все семь дней обработаны.")
        print(f"Файл всех дней: {output / 'all_7_days.md'}")
        print("Отдельные дни: day_01.md ... day_07.md")
        print("Примечание: сохранён текст, видимый на страницах плана, включая ссылки на библейские чтения.")
        print("Полный текст глав Библии по внешним ссылкам НЕ копируется: его нет внутри страницы плана.")
        print("Проверьте день 2 визуально по оригиналу перед дальнейшим использованием.")
        return 0
    except ExtractionError as exc:
        print(f"\nОШИБКА: {exc}", file=sys.stderr)
        print("Общий файл НЕ создавался: неполное извлечение нельзя считать точным.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"\nНеожиданная ошибка: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("Общий файл НЕ создавался.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
