# -*- coding: utf-8 -*-
"""YouVersion -> one Markdown file. Python 3.9+, no third-party dependencies.

Reads only public plan pages.  No authentication or paywall bypass.
"""
from __future__ import annotations

import concurrent.futures
import html
from html.parser import HTMLParser
import json
import os
import shutil
import ssl
import subprocess
import tempfile
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from threading import Lock

ROOT = Path(__file__).resolve().parent
BASE = 'https://www.bible.com/reading-plans/'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept-Encoding': 'identity',
}
MAX_DAYS = 1000
WORKERS = 3
RETRIES = 2
PARSER_VERSION = 2

class ExportError(Exception):
    pass

class TextPage(HTMLParser):
    """Recover readable server-rendered HTML, preserving original text characters."""
    BLOCKS = {'div', 'p', 'section', 'article', 'main', 'h1', 'h2', 'h3',
              'h4', 'h5', 'h6', 'li', 'ul', 'ol', 'blockquote', 'pre',
              'br', 'tr', 'td', 'table', 'figure', 'figcaption'}
    IGNORE = {'script', 'style', 'noscript', 'svg', 'template'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.bits = []
        self.lines = []
        self.ignored = []
        self.tag_stack = []
        self.meta = {}
        self.h1 = []
        self.in_h1 = 0
        self.images = []
        self.anchor_bits = None
        self.hrefs = []

    def push(self):
        if self.bits:
            v = ''.join(self.bits)
            v = v.replace('\r', ' ').replace('\n', ' ').replace('\xa0', ' ')
            v = re.sub(r'[\t ]+', ' ', v).strip()
            if v:
                self.lines.append(v)
            self.bits.clear()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in self.IGNORE:
            self.ignored.append(tag)
            return
        if self.ignored:
            return
        if tag == 'a':
            if attrs.get('href'):
                self.hrefs.append(attrs['href'])
            self.anchor_bits = []
        if tag == 'meta':
            name = attrs.get('property', attrs.get('name', '')).lower()
            if name and name not in self.meta:
                self.meta[name] = attrs.get('content', '')
        if tag == 'img':
            src = attrs.get('src') or attrs.get('data-src')
            if src:
                self.images.append((attrs.get('alt', ''), src))
        if tag == 'h1':
            self.in_h1 += 1
            self.push()
        if tag in self.BLOCKS:
            self.push()

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if self.ignored:
            return
        if self.anchor_bits is not None:
            self.anchor_bits.append(data)
        else:
            self.bits.append(data)
        if self.in_h1:
            self.h1.append(data)

    def handle_endtag(self, tag):
        if self.ignored:
            if self.ignored[-1] == tag:
                self.ignored.pop()
            return
        if tag == 'a' and self.anchor_bits is not None:
            anchor_text = ''.join(self.anchor_bits)
            self.anchor_bits = None
            if re.fullmatch(r'\s*\d{1,4}\s*', anchor_text):
                self.push()
                self.lines.append(anchor_text.strip())
            else:
                self.bits.append(anchor_text)
        if tag in self.BLOCKS:
            self.push()
        if tag == 'h1':
            self.in_h1 = max(0, self.in_h1 - 1)

    def finish(self):
        self.push()
        # Remove exact repeated neighboring chunks introduced by wrapper tags.
        return [line for line in self.lines if line.strip()]


def _checked_hostname(url):
    host = urllib.parse.urlparse(url).hostname
    if host not in ('bible.com', 'www.bible.com'):
        raise ExportError('Переадресация за пределы Bible.com: ' + url)


def _decode(raw):
    try:
        return raw.decode('utf-8-sig', errors='strict')
    except UnicodeDecodeError as err:
        raise ExportError('Страница не является корректным UTF-8: ' + str(err)) from err


def _curl_executable():
    if os.name == 'nt':
        expected = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'curl.exe'
        if expected.is_file():
            return str(expected)
    return shutil.which('curl.exe') if os.name == 'nt' else shutil.which('curl')


def _curl_get(url):
    exe = _curl_executable()
    if not exe:
        raise ExportError('Системная утилита curl.exe не найдена.')
    # Windows curl uses Schannel when built with that backend; no -k/--insecure.
    with tempfile.TemporaryDirectory(prefix='youversion_') as tmp:
        dest = Path(tmp) / 'page.html'
        cmd = [exe, '--fail', '--location', '--silent', '--show-error',
               '--compressed', '--connect-timeout', '10', '--max-time', '35',
               '--max-filesize', '15000000', '--proto', '=https',
               '--proto-redir', '=https',
               '--user-agent', HEADERS['User-Agent'],
               '--header', 'Accept-Language: ru-RU,ru;q=0.9,en-US;q=0.8',
               '--output', str(dest), '--write-out', '%{url_effective}', url]
        try:
            result = subprocess.run(cmd, text=True, capture_output=True,
                                    encoding='utf-8', errors='replace', timeout=40,
                                    shell=False, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ExportError('curl.exe не смог завершить запрос: ' + str(exc)) from exc
        if result.returncode != 0:
            detail = (result.stderr or '').strip()[:400]
            raise ExportError(f'curl.exe: код {result.returncode}. {detail}')
        _checked_hostname(result.stdout.strip())
        raw = dest.read_bytes()
        if not raw:
            raise ExportError('curl.exe получил пустую страницу.')
        return _decode(raw)


def get(url):
    _checked_hostname(url)
    last_error = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=12) as resp:
                _checked_hostname(resp.geturl())
                raw = resp.read(15_000_001)
                if len(raw) > 15_000_000:
                    raise ExportError('Страница больше 15 МБ, нельзя сохранять обрезанный текст.')
                if not raw:
                    raise ExportError('Сайт вернул пустую страницу.')
                return _decode(raw)
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as err:
            last_error = err
            if isinstance(err, urllib.error.HTTPError) and err.code in (404, 401, 403):
                break
            # A failed certificate check is NOT permission to disable TLS validation.
            reason = getattr(err, 'reason', err)
            if isinstance(reason, ssl.SSLError) or 'CERTIFICATE_VERIFY_FAILED' in str(err):
                break
            if attempt < RETRIES - 1:
                time.sleep(1 + attempt)
    try:
        return _curl_get(url)
    except ExportError as curl_error:
        raise ExportError('Не удалось загрузить ' + url + '\n'
                          + 'Python: ' + str(last_error) + '\n'
                          + 'Windows curl: ' + str(curl_error) + '\n'
                          + 'Проверьте системные сертификаты, антивирус и доступ к сайту в браузере.') from curl_error


def load_page(url):
    page = TextPage()
    page.feed(get(url))
    return page, page.finish()


def input_plan_id(value):
    value = value.strip().strip('"\'')
    # Both site URL and bare numerical ID accepted.
    if re.fullmatch(r'\d{1,10}', value):
        return int(value)
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ('https', 'http') or parsed.hostname not in ('www.bible.com','bible.com'):
        raise ExportError('Нужна ссылка с www.bible.com/reading-plans/..., или номер плана.')
    match = re.search(r'/reading-plans/(\d+)(?:-|/|$)', parsed.path)
    if not match:
        raise ExportError('Не найден номер плана в адресе.')
    return int(match.group(1))


def input_locale(value):
    m = re.search(r'https?://(?:www\.)?bible\.com/([a-z]{2}(?:-[A-Z]{2})?)/reading-plans/', value, re.I)
    return '/' + m.group(1).lower() if m else '/ru'


def plan_metadata(pid, locale):
    url = 'https://www.bible.com' + locale + '/reading-plans/' + str(pid)
    page, lines = load_page(url)
    title = ''.join(page.h1).strip() or page.meta.get('og:title', '').split(' - Bible App')[0].strip()
    if not title:
        raise ExportError('Не удалось определить название плана.')
    count = None
    # Only match a *standalone* 'N Days' element, never numbers in the description.
    day_re = re.compile(r'^\s*(\d{1,4})\s*(?:Days?|дней|дня|день)\s*$', re.I)
    for line in lines:
        m = day_re.match(line)
        if m:
            count = int(m.group(1))
            break
    cover = page.meta.get('og:image', '') or page.meta.get('twitter:image', '')
    if not cover:
        for alt, src in page.images:
            if title.casefold() in alt.casefold() and src.startswith('http'):
                cover = src
                break
    if cover.startswith('//'):
        cover = 'https:' + cover
    if not cover.startswith('https://'):
        cover = ''
    intro = []
    # Locate plan description after Start this Plan and before publisher/Related Plans.
    idxs = [i for i, v in enumerate(lines) if re.fullmatch(r'(Start this Plan|Начать план|Начать этот план)', v, flags=re.I)]
    if idxs:
        start = idxs[-1] + 1
        while start < len(lines):
            v = lines[start]
            if re.match(r'^(?:More from |Больше от |Loading[.…]*|Загрузка[.…]*|Related Plans|About The Publisher|Об издателе|Blog$)', v, flags=re.I):
                break
            intro.append(v)
            start += 1
    intro = [x for x in intro if x not in ('More', 'Подробнее')]
    day1 = None
    # Use the plan's own Day 1 link, including the canonical slug. A URL
    # without that slug is not guaranteed to be accepted by Bible.com.
    pattern = re.compile(r'/reading-plans/' + str(pid) + r'(?:-[^/?#]+)?/day/1(?:[/?#]|$)')
    for href in page.hrefs:
        candidate = urllib.parse.urljoin(url, href)
        if pattern.search(urllib.parse.urlparse(candidate).path + '/'):
            try:
                _checked_hostname(candidate)
            except ExportError:
                continue
            day1 = candidate
            break
    if day1 is None:
        day1 = 'https://www.bible.com' + locale + '/reading-plans/' + str(pid) + '/day/1'
    return title, count, cover, intro, day1


DAY_RE = re.compile(r'\b(?:DAY|ДЕНЬ)\s+(\d{1,4})\s+(?:OF|ИЗ)\s+(\d{1,4})\b', re.I)
SCRIPTURE_RE = re.compile(r'^(Scripture|Священное Писание|Писание|Места Писания|Bible Readings?)$', re.I)
STOP_RE = re.compile(r'^(Start this Plan|About this Plan|Начать план|Начать этот план|Об этом плане|About The Publisher|Related Plans|Loading[.…]*|Загрузка[.…]*)$', re.I)
DAY_LINK_RE = re.compile(r'^(?:Day|День)\s+\d{1,4}$', re.I)


def day_content(lines, expected_day, total):
    """Extract devotional + Scripture references from a rendered day, not site UI."""
    anchors = [(i, m) for i, x in enumerate(lines) if (m := DAY_RE.search(x))]
    anchors = [(i, m) for i, m in anchors if int(m.group(1)) == expected_day and int(m.group(2)) == total]
    if len(anchors) != 1:
        raise ExportError(f'нет однозначного заголовка «День {expected_day} из {total}»')
    ix, anchor = anchors[0]
    tail = lines[ix + 1:]
    if len(tail) < 2:
        raise ExportError('после заголовка отсутствует содержимое')
    # Day navigation (1...N) appears before the actual material.
    while tail and re.fullmatch(r'\d{1,4}', tail[0]) and 1 <= int(tail[0]) <= total:
        tail.pop(0)
    # Sometimes the UI arranges several navigation links in a single line.
    if tail:
        nums = tail[0].split()
        if len(nums) > 1 and all(x.isdigit() and 1 <= int(x) <= total for x in nums):
            tail.pop(0)
    # Determine exact content boundary, stripping the related-plan and start buttons.
    limit = None
    for j, row in enumerate(tail):
        if STOP_RE.fullmatch(row) or DAY_LINK_RE.fullmatch(row):
            limit = j
            break
    if limit is None:
        raise ExportError('не найдена граница текста (Начать этот план / Об этом плане)')
    content = tail[:limit]
    # Anomalous UI signals: protect against polluted export.
    forbidden = ('Get the app', 'Skip to content', 'Related Plans', 'Loading…', 'Загрузка...', 'Перейти к содержимому')
    if any(x in content for x in forbidden):
        raise ExportError('внутри текста обнаружены элементы интерфейса')
    if not content:
        raise ExportError('текст дня пустой')
    # Search Scripture heading if present; allow Scripture-only days.
    scripture_positions = [j for j, x in enumerate(content) if SCRIPTURE_RE.fullmatch(x)]
    if scripture_positions:
        i = scripture_positions[0]
        material, scripture = content[:i], content[i+1:]
        if not material and not scripture:
            raise ExportError('раздел Scripture пустой')
        # Avoid treating another nav as Bible reference.
        scripture = [s for s in scripture if s.strip()]
        if not scripture:
            raise ExportError('раздел Scripture не содержит ссылок на чтения')
        parts = []
        if material:
            parts.append('\n\n'.join(material))
        parts.append(content[i] + '\n\n' + '\n\n'.join(scripture))
        text = '\n\n'.join(parts)
    else:
        text = '\n\n'.join(content)
    if len(text.strip()) < 5:
        raise ExportError('слишком короткое содержимое')
    return text.strip()


def filename_clean(title):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', '_', title).strip(' ._')
    return name[:85] or 'plan'


def cached_read(cache_dir, day):
    path = cache_dir / f'{day:04}.json'
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(data, dict) and data.get('version') == PARSER_VERSION and data.get('day') == day and isinstance(data.get('text'), str) and data['text'].strip():
            return data['text']
    except (OSError, ValueError, KeyError):
        pass
    return None


def export(pid, locale):
    print(f'Читаю описание плана {pid}...', flush=True)
    title, count, cover, intro, day1_url = plan_metadata(pid, locale)
    if count is None:
        print('Количество дней не указано на странице плана. Проверяю день 1...')
        _, first_lines = load_page(day1_url)
        counts = [int(m.group(2)) for ln in first_lines if (m := DAY_RE.search(ln)) and int(m.group(1)) == 1]
        if len(set(counts)) != 1:
            raise ExportError('Не удалось автоматически определить число дней.')
        count = counts[0]
    if not 1 <= count <= MAX_DAYS:
        raise ExportError(f'Неподдерживаемое количество дней: {count}')
    print(f'План: {title}\nДней: {count}\n', flush=True)
    base_file = f'YouVersion_{pid}_{filename_clean(title)}.md'
    dest = ROOT / base_file
    temp = ROOT / (base_file + '.part')
    cache = ROOT / f".youversion_cache_{pid}_{locale.lstrip('/')}_v2"
    cache.mkdir(exist_ok=True)
    content = {}
    for day in range(1, count + 1):
        saved = cached_read(cache, day)
        if saved is not None:
            content[day] = saved
    if content:
        print(f'Найдено уже проверенных дней в кеше: {len(content)}')
    print(f'Загружаю оставшиеся дни (до {WORKERS} одновременно)...', flush=True)
    lock = Lock()
    errors = {}

    def retrieve(day):
        day_url = re.sub(r'/day/1(?=\?|$)', '/day/' + str(day), day1_url)
        _, lines = load_page(day_url)
        text = day_content(lines, day, count)
        # Save each confirmed day immediately so interrupted large plans can resume.
        cached = cache / f'{day:04}.json'
        cached.write_text(json.dumps({'version': PARSER_VERSION, 'day': day, 'text': text}, ensure_ascii=False), encoding='utf-8')
        return text

    undone = [d for d in range(1, count + 1) if d not in content]
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        tasks = {pool.submit(retrieve, d): d for d in undone}
        for future in concurrent.futures.as_completed(tasks):
            d = tasks[future]
            try:
                text = future.result()
                content[d] = text
                result = 'OK'
            except (ExportError, OSError, ValueError) as exc:
                errors[d] = str(exc)
                result = 'ОШИБКА: ' + str(exc)
            with lock:
                print(f'[{len(content) + len(errors)}/{count}] День {d}: {result}', flush=True)

    if errors:
        print('\nНе все дни удалось получить. Итоговый .md НЕ перезаписан.', flush=True)
        for d in sorted(errors):
            print(f'  День {d}: {errors[d]}', flush=True)
        print('Повторный запуск продолжит с сохранённых дней.', flush=True)
        return 1

    # Minimal file structure, no paraphrasing, no inserted devotional passages.
    md = [f'# {title}', '', f'Источник: https://www.bible.com{locale}/reading-plans/{pid}', f'Дней: {count}', '']
    if cover:
        md.extend([f'![Обложка]({cover})', ''])
    if intro:
        md.extend(intro)
        md.append('')
    for d in range(1, count + 1):
        md.extend([f'---', '', f'## День {d}', '', content[d], ''])
    temp.write_text('\n'.join(md).rstrip() + '\n', encoding='utf-8-sig')
    # Atomic replacement, only after EVERY day passed checks.
    temp.replace(dest)
    print('\nГОТОВО: ' + str(dest), flush=True)
    print(f'Сохранены {count} из {count} дней. Формат: Markdown UTF-8 (BOM).', flush=True)
    # Keep the cache for potential comparisons/re-exports; temporary files are not output.
    return 0


def main():
    print('YouVersion -> один Markdown-файл | версия 2.1 (без библиотек)')
    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        url = input('Вставьте ссылку на план (или ID): ').strip()
    try:
        pid = input_plan_id(url)
        locale = input_locale(url)
        return export(pid, locale)
    except (ExportError, KeyboardInterrupt) as exc:
        print('\nОШИБКА: ' + str(exc), flush=True)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
