"""Проверки на сохранённых примерах и на повреждённых копиях."""

import codecs
import tempfile
import unittest
from pathlib import Path

from cleaner import InputError, ROOT, build


EXAMPLES = ROOT / "Архив" / "Прежние результаты"
# Номера строк именно в первоначальных копиях. Это независимая спецификация
# ожидаемого файла: эти границы не вычисляет проверяемый очиститель.
COMBINED = {
    "002": [(87, 96, 132), (202, 211, 242), (314, 323, 349), (421, 430, 458),
            (530, 539, 563), (635, 644, 676), (748, 757, 791)],
    "003": [(87, 96, 117), (187, 196, 216), (288, 297, 313), (385, 394, 414),
            (486, 495, 517), (589, 598, 622), (695, 704, 724)],
}


def segment(lines, first, last):
    return b"".join(lines[first - 1:last])


class CleanerTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.out = self.base / "результаты"
        self.samples = sorted((EXAMPLES / "001").glob("*.md"))

    def test_separate_pages_exact_bytes(self):
        lines = self.samples[0].read_bytes().splitlines(True)
        expected = segment(lines, 20, 21) + segment(lines, 26, 29)
        for source, end in zip(self.samples[1:], (62, 48, 50, 48, 52, 55, 45), strict=True):
            lines = source.read_bytes().splitlines(True)
            expected += lines[21] + segment(lines, 31, end)
        result, days = build(EXAMPLES / "001", self.out)
        self.assertEqual(days, 7)
        self.assertEqual(result.read_bytes(), expected)

    def test_combined_pages_exact_bytes(self):
        for name, day_ranges in COMBINED.items():
            with self.subTest(name=name):
                source, = (EXAMPLES / name).glob("*.md")
                lines = source.read_bytes().splitlines(True)
                expected = lines[18] + lines[85] + lines[19] + segment(lines, 25, 27)
                for header, start, end in day_ranges:
                    expected += lines[header - 1] + segment(lines, start, end)
                result, days = build(source, self.out)
                self.assertEqual(days, 7)
                self.assertEqual(result.read_bytes(), expected)

    def test_shuffled_combined_file_without_cover(self):
        path = self.base / "все дни.txt"
        path.write_bytes(codecs.BOM_UTF8 + b"\n".join(p.read_bytes() for p in self.samples[:0:-1]))
        result, days = build(path, self.out)
        data = result.read_bytes().decode("utf-8")
        self.assertEqual(days, 7)
        self.assertIn("https://www.ari-m.com/", data)
        self.assertEqual(data.count("https://www.ari-m.com/"), 1)
        self.assertLess(data.index("День 1 из 7"), data.index("День 7 из 7"))

    def test_body_keeps_same_words_as_menu_and_line_endings(self):
        marker = "В чём ИСТИННЫЙ смысл Рождества?".encode()
        replacement = ("  7\t  \r\nНачать этот план\nДень 2\rБлог\nMore\n"
                       "Об издателе\nПланы, относящиеся к теме\r\n"
                       " © 2026 YouVersion\r\n\r\n\r\n"
                       "\t # Слово\u00a0е\u0308 ⭐️\u2028строка  \t").encode()
        path = self.base / "копия.txt"
        original = b"\r\n".join(p.read_bytes() for p in self.samples)
        path.write_bytes(original)
        before, _ = build(path, self.out)
        expected = before.read_bytes().replace(marker, replacement)
        path.write_bytes(original.replace(marker, replacement))
        after, _ = build(path, self.out)
        self.assertEqual(after.read_bytes(), expected)
        self.assertNotEqual(before, after)
        self.assertEqual(build(path, self.out)[0], after)
        self.assertEqual(path.read_bytes(), original.replace(marker, replacement))

    def test_incomplete_or_ambiguous_input_never_publishes(self):
        cover, *days = [p.read_bytes() for p in self.samples]
        complete = [cover, *days]
        mixed = days[-1].replace("ДЕТЯМ о РОЖДЕСТВЕ".encode(), "Другой план".encode())
        changed_credit = days[-1].replace(b"ari-m.com", b"example.test")
        truncated_start = days[-1][days[-1].index("Священное Писание".encode()):]
        for label, pages in [
                ("пропуск", complete[:-1]), ("повтор", complete + [days[0]]),
                ("два плана", complete[:-1] + [mixed]),
                ("отличается издатель", complete[:-1] + [changed_credit]),
                ("обрыв", complete[:-1] + [days[-1][:-40]]),
                ("оборванное начало", complete[:-1] + [truncated_start]),
                ("постороннее содержимое", complete + ["Сохраните эту мысль".encode()]),
                ("кодировка", [b"\xff\xfe"])]:
            with self.subTest(label=label):
                path = self.base / "ошибка.txt"
                path.write_bytes(b"\r\n".join(pages))
                with self.assertRaises(InputError):
                    build(path, self.out)
                self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
