"""Tests for the standalone Python Octavo port: python -m unittest -v."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from octavo import Document, Octavo, TextLayout


class DocumentTests(unittest.TestCase):
    def test_typing_run_and_undo(self):
        d = Document()
        for ch in "hello":
            self.assertTrue(d.insert(ch, typed=True))
        self.assertEqual(d.text, "hello")
        self.assertEqual(len(d.undo_stack), 1)
        self.assertTrue(d.undo())
        self.assertEqual(d.text, "")
        self.assertTrue(d.undo(redo=True))
        self.assertEqual(d.text, "hello")
        self.assertTrue(d.insert("\n"))
        self.assertTrue(d.insert("x", typed=True))
        self.assertTrue(d.undo())
        self.assertEqual(d.text, "hello\n")
        self.assertTrue(d.undo())
        self.assertEqual(d.text, "hello")

    def test_replace_selection_and_delete_joins_lines(self):
        d = Document()
        d.clear_document("a\nb", None)
        d.move(1)
        d.insert("\n")
        self.assertEqual(d.text, "a\n\nb")
        d.backspace()
        self.assertEqual(d.text, "a\nb")
        d.delete_forward()
        self.assertEqual(d.text, "ab")
        d.anchor, d.cursor = 0, 2
        d.insert("X")
        self.assertEqual(d.text, "X")
        self.assertEqual(d.selection, (1, 1))

    def test_same_text_replacement_collapses_selection_without_history(self):
        d = Document()
        d.clear_document("abc")
        d.anchor, d.cursor = 0, 3
        self.assertTrue(d.insert("abc"))
        self.assertEqual(d.selection, (3, 3))
        self.assertEqual(d.text, "abc")
        self.assertEqual(len(d.undo_stack), 0)
        self.assertFalse(d.dirty)

    def test_word_boundaries(self):
        d = Document()
        d.clear_document("alpha  23, next\nword")
        self.assertEqual(d.next_word(0), 7)
        self.assertEqual(d.next_word(7), 9)
        self.assertEqual(d.next_word(9), 11)
        self.assertEqual(d.previous_word(7), 0)
        self.assertEqual(d.previous_word(12), 11)
        self.assertEqual(d.next_word(15), 16)
        self.assertEqual(d.next_word(16), 20)

    def test_status_emoji_selection(self):
        d = Document()
        d.clear_document("ab\n\tcd\nA😀Z")
        d.move(len(d.text))
        self.assertEqual(d.status(), "Ln 3, Col 4")
        d.anchor = 3
        self.assertEqual(d.status(), "Ln 3, Col 4  (7 selected)")

    def test_find_and_replace(self):
        d = Document()
        d.clear_document("aXXbxxcXX")
        d.search_term = "xx"
        self.assertTrue(d.find())
        self.assertEqual(d.selection, (4, 6))

    def test_find_case_and_wrap(self):
        d = Document()
        d.clear_document("Red red RED")
        d.search_term = "Red"
        self.assertTrue(d.find())
        self.assertEqual(d.selection, (0, 3))
        self.assertTrue(d.find())
        self.assertEqual(d.selection, (0, 3))
        d.match_case = False
        self.assertTrue(d.find())
        self.assertEqual(d.selection, (4, 7))
        self.assertTrue(d.find(backwards=True))
        self.assertEqual(d.selection, (0, 3))
        d.replace_term = "green"
        self.assertTrue(d.replace_match())
        self.assertEqual(d.text, "green red RED")
        self.assertEqual(d.selection, (5, 5))
        self.assertEqual(d.replace_all(), 2)
        self.assertEqual(d.text, "green green green")
        self.assertTrue(d.undo())
        self.assertEqual(d.text, "green red RED")

    def test_find_replace_first_selects(self):
        d = Document()
        d.clear_document("cat cat")
        d.search_term, d.replace_term = "cat", "dog"
        self.assertFalse(d.replace_match())
        self.assertEqual(d.selection, (0, 3))
        self.assertTrue(d.replace_match())
        self.assertEqual(d.text, "dog cat")

    def test_replace_all_nonrecursive_and_unchanged(self):
        d = Document()
        d.clear_document("aaaa")
        d.search_term = "aa"
        d.replace_term = "aaa"
        self.assertEqual(d.replace_all(), 2)
        self.assertEqual(d.text, "aaaaaa")
        self.assertTrue(d.undo())
        self.assertEqual(d.text, "aaaa")
        d.replace_term = "aa"
        prior = len(d.undo_stack)
        self.assertEqual(d.replace_all(), 2)
        self.assertEqual(len(d.undo_stack), prior)

    def test_io_utf8_crlf_and_invalid_files(self):
        d = Document()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.lemur"
            d.insert("a\n😀\n")
            self.assertTrue(d.dirty)
            d.write_path(path)
            self.assertEqual(path.read_bytes(), "a\r\n😀\r\n".encode("utf-8"))
            self.assertFalse(d.dirty)
            d.insert("!")
            self.assertTrue(d.dirty)
            self.assertTrue(d.undo())
            self.assertFalse(d.dirty)
            path.write_bytes(b"\xef\xbb\xbfX\rY\r\nZ")
            d.open_path(path)
            self.assertEqual(d.text, "X\nY\nZ")
            self.assertFalse(d.undo())
            path.write_bytes(b"abc\x00def")
            with self.assertRaises(ValueError):
                d.open_path(path)
            self.assertEqual(d.text, "X\nY\nZ")
            path.write_bytes(b"\xff")
            with self.assertRaises(UnicodeDecodeError):
                d.open_path(path)
            self.assertEqual(d.text, "X\nY\nZ")


class LayoutTests(unittest.TestCase):
    @staticmethod
    def measure(value: str) -> int:
        return len(value) * 10

    def test_soft_wrap_and_tiling(self):
        text = "alpha beta gamma delta " * 60
        layout = TextLayout(text, True, 200, self.measure)
        self.assertGreater(len(layout.rows), 2)
        self.assertEqual(layout.rows[0].start, 0)
        self.assertEqual(layout.rows[-1].end, len(text))
        self.assertFalse(layout.rows[-1].soft)
        for a, b in zip(layout.rows, layout.rows[1:]):
            self.assertEqual(b.start, a.end if a.soft else a.end + 1)
            if a.soft:
                self.assertNotIn(text[a.start], " \t")
                trimmed = text[a.start:a.end].rstrip(" \t")
                self.assertLessEqual(self.measure(trimmed), 200)

    def test_hard_rows_and_empty_last_row(self):
        layout = TextLayout("one\ntwo three\n\nfour\n", True, 200, self.measure)
        self.assertEqual(len(layout.rows), 5)
        self.assertEqual(layout.rows[2].start, layout.rows[2].end)
        self.assertFalse(layout.rows[2].soft)
        self.assertEqual(layout.rows[-1].start, len(layout.text))
        unwrapped = TextLayout("x" * 2000, False, 10, self.measure)
        self.assertEqual(len(unwrapped.rows), 1)

    def test_emoji_tabs_and_affinity(self):
        text = "😀" * 300
        layout = TextLayout(text, True, 80, self.measure)
        self.assertEqual("".join(text[row.start:row.end] for row in layout.rows), text)
        boundary = layout.rows[0].end
        self.assertEqual(layout.row_of(boundary), 1)
        self.assertEqual(layout.row_of(boundary, end_affinity=True), 0)
        self.assertEqual(layout.offset_at_x(layout.rows[0], 1_000_000), boundary)
        tabs = TextLayout("a\tb\tc\td\te\tf\tg", True, 45, self.measure)
        self.assertGreater(len(tabs.rows), 1)
        self.assertEqual("".join(tabs.text[r.start:r.end] for r in tabs.rows), tabs.text)


class GuiSmokeTests(unittest.TestCase):
    def test_viewport_vertical_navigation_and_drag_scroll(self):
        import tkinter as tk
        root = tk.Tk()
        try:
            app = Octavo(root)
            root.update()
            app.doc.clear_document("abc\n" * 40)
            app._invalidate_layout()
            app.refresh()
            layout = app._layout()
            self.assertEqual(len(layout.rows), 41)
            app.doc.move(0)
            app.refresh()
            app._move_vertical(True, False, page=True)
            self.assertGreater(app.doc.cursor, 0)
            app.doc.move(0)
            app.refresh()
            app._mouse_down(SimpleNamespace(x=24, y=24))
            app._mouse_drag(SimpleNamespace(x=24, y=app.canvas.winfo_height() + 20))
            self.assertIsNotNone(app.autoscroll_id)
            app._autoscroll_tick()
            self.assertGreater(app.doc.first_row, 0)
            self.assertTrue(app.doc.has_selection)
            app._mouse_up(SimpleNamespace())
            self.assertIsNone(app.autoscroll_id)
        finally:
            root.destroy()

    def test_hylic_save_and_open_via_dialogs(self):
        import tkinter as tk
        from unittest.mock import patch
        root = tk.Tk()
        try:
            app = Octavo(root)
            root.update()
            app.doc.insert("alpha\nbeta")
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / "sample.txt"
                with patch("tkinter.filedialog.asksaveasfilename", return_value=str(p)) as saver:
                    self.assertTrue(app.save(as_hylic=True))
                    self.assertEqual(saver.call_args.kwargs["title"], "Save as hylic")
                self.assertEqual(p.read_bytes(), b"alpha\r\nbeta")
                self.assertEqual(app.doc.path, p)
                self.assertFalse(app.doc.dirty)
                p.write_bytes(b"changed\r\n")
                with patch("tkinter.filedialog.askopenfilename", return_value=str(p)):
                    app.open()
                self.assertEqual(app.doc.text, "changed\n")
        finally:
            root.destroy()

    def test_tk_starts_draws_typing_and_wraps(self):
        import tkinter as tk
        root = tk.Tk()
        try:
            app = Octavo(root)
            root.update_idletasks()
            root.update()
            self.assertEqual(app.doc.text, "")
            self.assertGreater(app.canvas.winfo_height(), 1)
            for ch in "hello":
                app._key(SimpleNamespace(keysym=ch, char=ch, state=0))
            self.assertEqual(app.doc.text, "hello")
            app._key(SimpleNamespace(keysym="Home", char="", state=0))
            self.assertEqual(app.doc.cursor, 0)
            app._key(SimpleNamespace(keysym="End", char="", state=0))
            self.assertEqual(app.doc.cursor, 5)
            app._key(SimpleNamespace(keysym="BackSpace", char="\b", state=0))
            self.assertEqual(app.doc.text, "hell")
            app.toggle_wrap()
            self.assertFalse(app.doc.word_wrap)
            app.toggle_wrap()
            self.assertTrue(app.doc.word_wrap)
            app.show_find(False)
            root.update()
            self.assertIsNotNone(app.find_window)
            app.find_window.destroy()
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
