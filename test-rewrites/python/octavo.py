#!/usr/bin/env python3
"""Octavo v0.1 — a self-contained, cross-platform Python port.

Requires Python 3.10+ and Tkinter (standard Python GUI toolkit). Run:
    python octavo.py

Unlike the original Win32/GDI editor, this port uses Tkinter for its window,
font metrics, drawing, menus, dialogs, and clipboard. It still owns the text
buffer, selections, layout, editing, and full-document undo history itself.
"""

from __future__ import annotations

import bisect
import os
from pathlib import Path
import re
import tempfile
import time
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class Snapshot:
    text: str
    cursor: int
    anchor: int
    first_row: int
    horizontal: int


class Document:
    """Editing engine with Python Unicode code-point offsets, no GUI dependency."""

    HISTORY_LIMIT = 100
    TYPING_GROUP_SECONDS = 2.0

    def __init__(self) -> None:
        self.text = ""
        self.cursor = 0
        self.anchor = 0
        self.path: Optional[Path] = None
        self.last_saved_text = ""
        self.undo_stack: list[Snapshot] = []
        self.redo_stack: list[Snapshot] = []
        self.first_row = 0
        self.horizontal = 0
        self.search_term = ""
        self.replace_term = ""
        self.match_case = True
        self.word_wrap = True
        self._typed_at = 0.0
        self._typed_cursor = -1
        self._typed_length = -1

    @property
    def dirty(self) -> bool:
        return self.text != self.last_saved_text

    @property
    def selection(self) -> tuple[int, int]:
        return min(self.cursor, self.anchor), max(self.cursor, self.anchor)

    @property
    def has_selection(self) -> bool:
        return self.cursor != self.anchor

    def snapshot(self) -> Snapshot:
        return Snapshot(self.text, self.cursor, self.anchor, self.first_row, self.horizontal)

    def _stop_typing(self) -> None:
        self._typed_at = 0
        self._typed_cursor = -1
        self._typed_length = -1

    @staticmethod
    def _push(stack: list[Snapshot], snapshot: Snapshot) -> None:
        stack.append(snapshot)
        if len(stack) > Document.HISTORY_LIMIT:
            del stack[: len(stack) - Document.HISTORY_LIMIT]

    def change(self, begin: int, end: int, replacement: str, *, typed: bool = False) -> bool:
        """Replace a span as one undoable edit; coalesce adjacent character typing."""
        assert 0 <= begin <= end <= len(self.text)
        replacement = self.normalize(replacement)
        before = self.snapshot()
        new_text = self.text[:begin] + replacement + self.text[end:]
        if new_text == self.text:
            # Replacing selected text with itself still collapses that selection,
            # but creates no undo entry or dirty-state change.
            self.cursor = self.anchor = begin + len(replacement)
            self._stop_typing()
            return (before.cursor, before.anchor) != (self.cursor, self.anchor)
        now = time.monotonic()
        continuous = (
            typed and before.cursor == before.anchor
            and begin == before.cursor == end
            and bool(self.undo_stack)
            and now - self._typed_at <= self.TYPING_GROUP_SECONDS
            and before.cursor == self._typed_cursor
            and len(before.text) == self._typed_length
        )
        if not continuous:
            self._push(self.undo_stack, before)
        self.redo_stack.clear()
        self.text = new_text
        self.cursor = self.anchor = begin + len(replacement)
        if typed:
            self._typed_at = now
            self._typed_cursor = self.cursor
            self._typed_length = len(self.text)
        else:
            self._stop_typing()
        return True

    @staticmethod
    def normalize(value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n")

    def insert(self, value: str, *, typed: bool = False) -> bool:
        begin, end = self.selection
        # A typed replacement of a selection starts a fresh undo run.
        return self.change(begin, end, value, typed=typed and not self.has_selection)

    def backspace(self) -> bool:
        begin, end = self.selection
        if begin != end:
            return self.change(begin, end, "")
        if self.cursor > 0:
            return self.change(self.cursor - 1, self.cursor, "")
        self._stop_typing()
        return False

    def delete_forward(self) -> bool:
        begin, end = self.selection
        if begin != end:
            return self.change(begin, end, "")
        if self.cursor < len(self.text):
            return self.change(self.cursor, self.cursor + 1, "")
        self._stop_typing()
        return False

    @staticmethod
    def word_class(ch: str) -> int:
        if ch in " \t":
            return 0
        if ch == "_" or ch.isalnum():
            return 1
        return 2

    def next_word(self, pos: int) -> int:
        n = len(self.text)
        if pos >= n:
            return n
        if self.text[pos] == "\n":
            return pos + 1
        kind = self.word_class(self.text[pos])
        if kind:
            while pos < n and self.text[pos] != "\n" and self.word_class(self.text[pos]) == kind:
                pos += 1
        while pos < n and self.word_class(self.text[pos]) == 0:
            pos += 1
        return pos

    def previous_word(self, pos: int) -> int:
        if pos == 0:
            return 0
        if self.text[pos - 1] == "\n":
            return pos - 1
        while pos and self.word_class(self.text[pos - 1]) == 0:
            pos -= 1
        if not pos or self.text[pos - 1] == "\n":
            return pos
        kind = self.word_class(self.text[pos - 1])
        while pos and self.text[pos - 1] != "\n" and self.word_class(self.text[pos - 1]) == kind:
            pos -= 1
        return pos

    def delete_word(self, forward: bool) -> bool:
        begin, end = self.selection
        if begin != end:
            return self.change(begin, end, "")
        if forward:
            return self.change(self.cursor, self.next_word(self.cursor), "")
        return self.change(self.previous_word(self.cursor), self.cursor, "")

    def move(self, position: int, *, extend: bool = False) -> None:
        self.cursor = max(0, min(len(self.text), position))
        if not extend:
            self.anchor = self.cursor

    def horizontal_move(self, right: bool, *, extend: bool = False, word: bool = False) -> None:
        if word:
            position = self.next_word(self.cursor) if right else self.previous_word(self.cursor)
        elif self.has_selection and not extend:
            position = self.selection[1 if right else 0]
        else:
            position = self.cursor + (1 if right else -1)
        self.move(position, extend=extend)

    def select_all(self) -> None:
        self.anchor = 0
        self.cursor = len(self.text)

    def status(self) -> str:
        before = self.text[:self.cursor]
        line = before.count("\n") + 1
        column = len(before) - before.rfind("\n")
        status = f"Ln {line}, Col {column}"
        if self.has_selection:
            begin, end = self.selection
            status += f"  ({end - begin} selected)"
        return status

    def undo(self, *, redo: bool = False) -> bool:
        source = self.redo_stack if redo else self.undo_stack
        dest = self.undo_stack if redo else self.redo_stack
        if not source:
            return False
        self._push(dest, self.snapshot())
        snap = source.pop()
        self.text, self.cursor, self.anchor = snap.text, snap.cursor, snap.anchor
        self.first_row, self.horizontal = snap.first_row, snap.horizontal
        self._stop_typing()
        return True

    def clear_document(self, text: str = "", path: Optional[Path] = None) -> None:
        self.text = self.normalize(text)
        self.path = path
        self.last_saved_text = self.text
        self.cursor = self.anchor = self.first_row = self.horizontal = 0
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._stop_typing()

    @staticmethod
    def read_path(path: Path) -> str:
        content = path.read_bytes().decode("utf-8-sig", errors="strict")
        if "\x00" in content:
            raise ValueError("This file contains embedded NUL characters; Octavo only opens text.")
        return Document.normalize(content)

    def open_path(self, path: Path) -> None:
        content = self.read_path(path)
        self.clear_document(content, path)

    def write_path(self, path: Path) -> None:
        """Save strictly encoded UTF-8, BOM-free, with CRLF and atomic replacement."""
        data = self.text.replace("\n", "\r\n").encode("utf-8", errors="strict")
        file_handle, temp_path = tempfile.mkstemp(prefix=".octavo-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(file_handle, "wb") as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp_path, path)
        except BaseException:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise
        self.path = path
        self.last_saved_text = self.text

    def _matches(self, term: str) -> list[tuple[int, int]]:
        if not term:
            return []
        flags = 0 if self.match_case else re.IGNORECASE
        return [(m.start(), m.end()) for m in re.finditer(re.escape(term), self.text, flags)]

    def find(self, *, backwards: bool = False) -> bool:
        """Find with wraparound. Uses the beginning/end of the current selection."""
        matches = self._matches(self.search_term)
        if not matches:
            return False
        start, end = self.selection
        if backwards:
            eligible = [match for match in matches if match[1] <= start]
            begin, finish = eligible[-1] if eligible else matches[-1]
        else:
            eligible = [match for match in matches if match[0] >= end]
            begin, finish = eligible[0] if eligible else matches[0]
        self.anchor, self.cursor = begin, finish
        return True

    def selection_matches_search(self) -> bool:
        if not self.search_term or not self.has_selection:
            return False
        begin, end = self.selection
        selected = self.text[begin:end]
        if self.match_case:
            return selected == self.search_term
        return re.fullmatch(re.escape(self.search_term), selected, re.IGNORECASE) is not None

    def replace_match(self) -> bool:
        """First select a match if needed, then replace it on the next invocation."""
        if not self.search_term:
            return False
        if not self.selection_matches_search():
            self.find()
            return False
        begin, end = self.selection
        return self.change(begin, end, self.replace_term)

    def replace_all(self) -> int:
        if not self.search_term:
            return 0
        matches = self._matches(self.search_term)
        if not matches:
            return 0
        result: list[str] = []
        previous = 0
        last_end = 0
        for begin, finish in matches:
            result.extend((self.text[previous:begin], self.replace_term))
            last_end += begin - previous + len(self.replace_term)
            previous = finish
        result.append(self.text[previous:])
        replacement = "".join(result)
        if replacement != self.text:
            before = self.snapshot()
            self.text = replacement
            self.cursor = self.anchor = last_end
            self._push(self.undo_stack, before)
            self.redo_stack.clear()
            self._stop_typing()
        return len(matches)


@dataclass(frozen=True)
class VisualRow:
    start: int
    end: int
    soft: bool = False


class TextLayout:
    """Word-wrapped visual rows, measured with an injected font-width callable."""

    def __init__(self, text: str, wrap: bool, width: int, measure: Callable[[str], int]):
        self.text, self.wrap, self.width, self.font_measure = text, wrap, max(1, width), measure
        self.tab_width = max(1, measure("    "))
        self.rows: list[VisualRow] = []
        self._build()
        self.starts = [row.start for row in self.rows]

    def span_width(self, start: int, end: int) -> int:
        """Width from a visual row start, where tabs always restart at x=0."""
        segment = self.text[start:end]
        if "\t" not in segment:
            return self.font_measure(segment) if segment else 0
        x = 0
        parts = segment.split("\t")
        for i, part in enumerate(parts):
            if part:
                x += self.font_measure(part)
            if i != len(parts) - 1:
                x = (x // self.tab_width + 1) * self.tab_width
        return x

    def _wrap_line(self, start: int, end: int) -> None:
        if not self.wrap:
            self.rows.append(VisualRow(start, end))
            return
        row_start = start
        while True:
            fits = row_start
            over = end
            probe = row_start + 64
            while True:
                probe = min(probe, end)
                if self.span_width(row_start, probe) <= self.width:
                    fits = probe
                    if probe == end:
                        break
                    probe = row_start + 2 * (probe - row_start)
                else:
                    over = probe
                    break
            if fits == end:
                self.rows.append(VisualRow(row_start, end))
                break
            while over - fits > 1:
                middle = (fits + over) // 2
                if self.span_width(row_start, middle) <= self.width:
                    fits = middle
                else:
                    over = middle
            cut = fits if fits > row_start else row_start + 1
            # Python code-point offsets make splitting a UTF-16 surrogate pair impossible.
            if cut < end and self.text[cut] in " \t":
                break_at = cut
                while break_at < end and self.text[break_at] in " \t":
                    break_at += 1
            else:
                break_at = 0
                for pos in range(cut, row_start, -1):
                    if self.text[pos - 1] in " \t":
                        break_at = pos
                        break
                if break_at == 0:
                    break_at = cut
            if break_at >= end:
                self.rows.append(VisualRow(row_start, end))
                break
            self.rows.append(VisualRow(row_start, break_at, True))
            row_start = break_at

    def _build(self) -> None:
        start = 0
        for i, character in enumerate(self.text):
            if character == "\n":
                self._wrap_line(start, i)
                start = i + 1
        self._wrap_line(start, len(self.text))

    def row_of(self, position: int, *, end_affinity: bool = False) -> int:
        idx = max(0, min(len(self.rows) - 1, bisect.bisect_right(self.starts, position) - 1))
        if end_affinity and idx > 0 and self.rows[idx - 1].soft and self.rows[idx - 1].end == position:
            return idx - 1
        return idx

    def offset_at_x(self, row: VisualRow, x: int) -> int:
        if x <= 0 or row.start == row.end:
            return row.start
        length = self.span_width(row.start, row.end)
        if x >= length:
            return row.end
        lo, hi = row.start, row.end
        while lo < hi:
            middle = (lo + hi) // 2
            if self.span_width(row.start, middle) < x:
                lo = middle + 1
            else:
                hi = middle
        after = lo
        before = max(row.start, after - 1)
        return after if length and (self.span_width(row.start, after) - x) < (x - self.span_width(row.start, before)) else before


class Octavo:
    """Tk canvas frontend; no native multiline text widget manages editor state."""

    MARGIN = 24
    FOOTER_HEIGHT = 64

    def __init__(self, root) -> None:
        import tkinter as tk
        from tkinter import font as tkfont

        self.tk = tk
        self.root = root
        self.doc = Document()
        root.title("Octavo v0.1")
        root.geometry("760x440")
        root.minsize(130, 150)
        self.font = tkfont.Font(root=root, family="Segoe UI", size=-32)
        self.line_height = max(40, self.font.metrics("linespace") + 6)
        self.font_ascent = self.font.metrics("ascent")
        self.canvas = tk.Canvas(root, background="white", highlightthickness=0, takefocus=1, cursor="xterm")
        self.canvas.pack(side="top", fill="both", expand=True)
        self.footer = tk.Frame(root, height=self.FOOTER_HEIGHT, background="white")
        self.footer.pack(side="bottom", fill="x")
        self.footer.pack_propagate(False)
        self.hint = tk.Label(self.footer, background="white", font=("Segoe UI", 8), anchor="center",
                             text="Ctrl+F: Find. Ctrl+H: Replace. F3 / Shift+F3: next / previous. Ctrl+S: save. Ctrl+Z/Y: undo/redo.")
        self.hint.pack(fill="x", expand=True)
        self.status = tk.Label(self.footer, background="white", font=("Segoe UI", 9), anchor="e", padx=24)
        self.status.pack(fill="x", expand=True)
        self.menu_bar = tk.Menu(root, tearoff=False)
        self._create_menus()
        root.config(menu=self.menu_bar)

        self.layout_cache: Optional[TextLayout] = None
        self.layout_text: Optional[str] = None
        self.layout_wrap: Optional[bool] = None
        self.layout_width = -1
        self.preferred_x: Optional[int] = None
        self.caret_at_row_end = False
        self.dragging = False
        self.drag_point = (0, 0)
        self.autoscroll_id = None
        self.wheel_remainder = 0
        self.hwheel_remainder = 0
        self.find_window = None
        self.find_entry = None
        self.replace_entry = None
        self.case_var = tk.BooleanVar(value=True)
        self._last_canvas_width = 0

        self.canvas.bind("<KeyPress>", self._key)
        self.canvas.bind("<ButtonPress-1>", self._mouse_down)
        self.canvas.bind("<B1-Motion>", self._mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self._mouse_up)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", self._wheel)
        self.canvas.bind("<Button-5>", self._wheel)
        self.canvas.bind("<Configure>", self._resize)
        self.canvas.bind("<FocusIn>", lambda _event: self.paint())
        self.root.bind_all("<F3>", lambda e: self._shortcut_find(e, False))
        self.root.bind_all("<Shift-F3>", lambda e: self._shortcut_find(e, True))
        self.root.bind_all("<Control-f>", lambda _e: self.show_find(False))
        self.root.bind_all("<Control-h>", lambda _e: self.show_find(True))
        self.root.bind_all("<Alt-z>", lambda _e: self.toggle_wrap())
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.canvas.focus_set()
        self.refresh()

    def _create_menus(self) -> None:
        tk = self.tk
        file_menu = tk.Menu(self.menu_bar, tearoff=False)
        file_menu.add_command(label="New", accelerator="Ctrl+N", command=self.new)
        file_menu.add_command(label="Open...", accelerator="Ctrl+O", command=self.open)
        file_menu.add_separator()
        file_menu.add_command(label="Save", accelerator="Ctrl+S", command=self.save)
        file_menu.add_command(label="Save as hylic...", accelerator="Ctrl+Shift+S", command=lambda: self.save(as_hylic=True))
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.close)
        self.menu_bar.add_cascade(label="File", menu=file_menu)

        self.edit_menu = tk.Menu(self.menu_bar, tearoff=False)
        entries = [
            ("Undo", "Ctrl+Z", lambda: self.history(False)),
            ("Redo", "Ctrl+Y", lambda: self.history(True)),
            None,
            ("Cut", "Ctrl+X", self.cut),
            ("Copy", "Ctrl+C", self.copy),
            ("Paste", "Ctrl+V", self.paste),
            None,
            ("Select All", "Ctrl+A", self.select_all),
            None,
            ("Find...", "Ctrl+F", lambda: self.show_find(False)),
            ("Find Next", "F3", lambda: self.find(False)),
            ("Find Previous", "Shift+F3", lambda: self.find(True)),
            ("Replace...", "Ctrl+H", lambda: self.show_find(True)),
        ]
        for entry in entries:
            if entry is None:
                self.edit_menu.add_separator()
            else:
                label, accelerator, callback = entry
                self.edit_menu.add_command(label=label, accelerator=accelerator, command=callback)
        self.edit_menu.configure(postcommand=self._update_edit_menu)
        self.menu_bar.add_cascade(label="Edit", menu=self.edit_menu)
        self.wrap_var = tk.BooleanVar(value=True)
        view_menu = tk.Menu(self.menu_bar, tearoff=False)
        view_menu.add_checkbutton(label="Word Wrap", accelerator="Alt+Z", variable=self.wrap_var, command=self.toggle_wrap)
        self.menu_bar.add_cascade(label="View", menu=view_menu)

    def _update_edit_menu(self) -> None:
        availability = {0: bool(self.doc.undo_stack), 1: bool(self.doc.redo_stack),
                        3: self.doc.has_selection, 4: self.doc.has_selection,
                        7: bool(self.doc.text)}
        for idx, enabled in availability.items():
            self.edit_menu.entryconfigure(idx, state="normal" if enabled else "disabled")
        try:
            self.root.clipboard_get()
            pasted = True
        except self.tk.TclError:
            pasted = False
        self.edit_menu.entryconfigure(5, state="normal" if pasted else "disabled")

    def _layout(self) -> TextLayout:
        width = max(1, self.canvas.winfo_width() - 2 * self.MARGIN - 2)
        if (self.layout_cache is None or self.layout_text is not self.doc.text or
                self.layout_wrap != self.doc.word_wrap or self.layout_width != width):
            self.layout_cache = TextLayout(self.doc.text, self.doc.word_wrap, width, self.font.measure)
            self.layout_text, self.layout_wrap, self.layout_width = self.doc.text, self.doc.word_wrap, width
        return self.layout_cache

    def _invalidate_layout(self) -> None:
        self.layout_cache = None

    def _visible_rows(self) -> int:
        return max(1, (self.canvas.winfo_height() - self.MARGIN) // self.line_height)

    def _reset_target(self) -> None:
        self.preferred_x = None
        self.caret_at_row_end = False

    def _caret_row(self, layout: TextLayout) -> int:
        return layout.row_of(self.doc.cursor, end_affinity=self.caret_at_row_end)

    def refresh(self, *, reveal: bool = True, row_delta: int = 0, pixel_delta: int = 0) -> None:
        layout = self._layout()
        doc = self.doc
        shown = self._visible_rows()
        max_first = max(0, len(layout.rows) - shown)
        doc.first_row = min(max_first, max(0, doc.first_row + row_delta))
        if doc.word_wrap:
            doc.horizontal = 0
        else:
            widest = max((layout.span_width(row.start, row.end) for row in layout.rows), default=0)
            max_horizontal = max(0, widest + 8 - layout.width)
            doc.horizontal = max(0, min(max_horizontal, doc.horizontal + pixel_delta))
        if reveal:
            row_number = self._caret_row(layout)
            if row_number < doc.first_row:
                doc.first_row = row_number
            elif row_number >= doc.first_row + shown:
                doc.first_row = row_number - shown + 1
            if not doc.word_wrap:
                row = layout.rows[row_number]
                caret_x = layout.span_width(row.start, doc.cursor)
                if caret_x < doc.horizontal:
                    doc.horizontal = caret_x
                elif caret_x > doc.horizontal + layout.width:
                    doc.horizontal = caret_x - layout.width
        self.top_offset = layout.rows[doc.first_row].start
        self.paint()

    def _reflow(self) -> None:
        top_offset = getattr(self, "top_offset", 0)
        self._invalidate_layout()
        layout = self._layout()
        self.doc.first_row = layout.row_of(top_offset)
        self.refresh()

    def _draw_run(self, x: int, y: int, text: str, selected: bool) -> None:
        if text:
            self.canvas.create_text(x, y, text=text, anchor="nw", font=self.font,
                                    fill="#ffffff" if selected else "#000000", tags="text")

    def paint(self) -> None:
        layout = self._layout()
        doc = self.doc
        canvas = self.canvas
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        selection_start, selection_end = doc.selection
        text = doc.text
        first_row = doc.first_row
        for idx in range(first_row, len(layout.rows)):
            row = layout.rows[idx]
            y = self.MARGIN + (idx - first_row) * self.line_height
            if y >= height:
                break
            start_x = self.MARGIN - doc.horizontal
            selected_start = max(row.start, selection_start)
            selected_end = min(row.end, selection_end)
            if selected_start < selected_end:
                x1 = start_x + layout.span_width(row.start, selected_start)
                x2 = start_x + layout.span_width(row.start, selected_end)
                canvas.create_rectangle(x1, y, x2, y + self.line_height, fill="#0a64ad", outline="")
            # Highlight a selected *hard* line break, never a soft wrap boundary.
            if not row.soft and row.end < len(text) and text[row.end] == "\n" and selection_start <= row.end < selection_end:
                x1 = start_x + layout.span_width(row.start, row.end)
                canvas.create_rectangle(x1, y, x1 + 8, y + self.line_height, fill="#0a64ad", outline="")
            pos = row.start
            x = start_x
            while pos < row.end:
                if text[pos] == "\t":
                    x = start_x + layout.span_width(row.start, pos + 1)
                    pos += 1
                    continue
                selected = selection_start <= pos < selection_end
                nxt = pos + 1
                while nxt < row.end and text[nxt] != "\t" and (selection_start <= nxt < selection_end) == selected:
                    nxt += 1
                run = text[pos:nxt]
                self._draw_run(x, y, run, selected)
                x += self.font.measure(run)
                pos = nxt
        if self._caret_row(layout) >= first_row:
            row_number = self._caret_row(layout)
            y = self.MARGIN + (row_number - first_row) * self.line_height
            if y < height:
                row = layout.rows[row_number]
                x = self.MARGIN - doc.horizontal + layout.span_width(row.start, doc.cursor)
                canvas.create_line(x, y, x, y + self.font.metrics("linespace"), width=2, fill="#111111")
        # Tk canvas has no clip rectangle, so masks enforce the original 24px side margins.
        canvas.create_rectangle(0, 0, self.MARGIN - 1, height, fill="white", outline="")
        canvas.create_rectangle(max(self.MARGIN, width - self.MARGIN), 0, width, height, fill="white", outline="")
        canvas.create_rectangle(0, 0, width, self.MARGIN - 1, fill="white", outline="")
        self.status.configure(text=doc.status())
        stem = doc.path.name if doc.path else ""
        title = "Octavo v0.1" + (" — " + stem if stem else "") + (" *" if doc.dirty else "")
        self.root.title(title)

    def _position_at(self, x: int, y: int) -> tuple[int, bool]:
        layout = self._layout()
        row_idx = max(0, min(len(layout.rows) - 1,
                              self.doc.first_row + (y - self.MARGIN) // self.line_height))
        row = layout.rows[row_idx]
        local_x = x - self.MARGIN + self.doc.horizontal
        position = layout.offset_at_x(row, local_x)
        return position, row.soft and position == row.end

    def _mouse_down(self, event) -> str:
        self.canvas.focus_set()
        self.dragging = True
        self.drag_point = (event.x, event.y)
        position, affinity = self._position_at(event.x, event.y)
        self.doc.move(position)
        self._reset_target()
        self.caret_at_row_end = affinity
        self.refresh(reveal=False)
        return "break"

    def _mouse_drag(self, event) -> str:
        if not self.dragging:
            return "break"
        self.drag_point = (event.x, event.y)
        position, affinity = self._position_at(event.x, event.y)
        self.doc.move(position, extend=True)
        self._reset_target()
        self.caret_at_row_end = affinity
        self.refresh(reveal=False)
        self._ensure_autoscroll()
        return "break"

    def _mouse_up(self, _event) -> str:
        self.dragging = False
        self._stop_autoscroll()
        return "break"

    def _stop_autoscroll(self) -> None:
        if self.autoscroll_id is not None:
            self.root.after_cancel(self.autoscroll_id)
            self.autoscroll_id = None

    def _outside_scroll(self) -> tuple[int, int]:
        x, y = self.drag_point
        height, width = self.canvas.winfo_height(), self.canvas.winfo_width()
        up = max(0, self.MARGIN - y)
        down = max(0, y - height + 1)
        left = max(0, self.MARGIN - x)
        right = max(0, x - (width - self.MARGIN))
        vertical = min(10, 1 + max(up, down) // max(1, self.line_height)) * (-1 if up else 1) if (up or down) else 0
        horizontal = min(160, 16 + max(left, right)) * (-1 if left else 1) if (left or right) else 0
        return vertical, 0 if self.doc.word_wrap else horizontal

    def _ensure_autoscroll(self) -> None:
        vertical, horizontal = self._outside_scroll()
        if not (vertical or horizontal) or not self.dragging:
            self._stop_autoscroll()
        elif self.autoscroll_id is None:
            self.autoscroll_id = self.root.after(50, self._autoscroll_tick)

    def _autoscroll_tick(self) -> None:
        self.autoscroll_id = None
        if not self.dragging:
            return
        vertical, horizontal = self._outside_scroll()
        if vertical or horizontal:
            self.refresh(reveal=False, row_delta=vertical, pixel_delta=horizontal)
            position, affinity = self._position_at(*self.drag_point)
            self.doc.move(position, extend=True)
            self._reset_target()
            self.caret_at_row_end = affinity
            self.refresh(reveal=False)
            self.autoscroll_id = self.root.after(50, self._autoscroll_tick)

    def _wheel(self, event) -> str:
        # Windows uses delta=120 per notch; X11 uses Button-4/5.
        if getattr(event, "num", None) == 4:
            ticks = 1
        elif getattr(event, "num", None) == 5:
            ticks = -1
        else:
            delta = event.delta
            if self.root.tk.call("tk", "windowingsystem") == "aqua":
                ticks = 1 if delta > 0 else -1 if delta < 0 else 0
            else:
                self.wheel_remainder += delta
                ticks = int(self.wheel_remainder / 120)
                self.wheel_remainder -= ticks * 120
        if ticks:
            if event.state & 0x1 and not self.doc.word_wrap:
                self.refresh(reveal=False, pixel_delta=-96 * ticks)
            else:
                self.refresh(reveal=False, row_delta=-3 * ticks)
        return "break"

    def _resize(self, event) -> None:
        if event.width != self._last_canvas_width:
            self._last_canvas_width = event.width
            self._reflow()
        else:
            self.refresh()

    def _move_vertical(self, down: bool, extend: bool, page: bool = False) -> None:
        layout = self._layout()
        current_row = self._caret_row(layout)
        if self.preferred_x is None:
            row = layout.rows[current_row]
            self.preferred_x = layout.span_width(row.start, self.doc.cursor)
        step = self._visible_rows() if page else 1
        target = max(0, min(len(layout.rows) - 1, current_row + (step if down else -step)))
        row = layout.rows[target]
        position = layout.offset_at_x(row, self.preferred_x)
        self.doc.move(position, extend=extend)
        self.caret_at_row_end = row.soft and position == row.end
        self.refresh()

    def _move_home_end(self, to_end: bool, extend: bool) -> None:
        layout = self._layout()
        row = layout.rows[self._caret_row(layout)]
        self.doc.move(row.end if to_end else row.start, extend=extend)
        self._reset_target()
        self.caret_at_row_end = to_end and row.soft
        self.refresh()

    def _key(self, event) -> str | None:
        sym = event.keysym
        ctrl = bool(event.state & 0x4)
        shift = bool(event.state & 0x1)
        alt = bool(event.state & 0x8)
        doc = self.doc
        if ctrl and not alt:
            if sym in ("Left", "Right"):
                doc.horizontal_move(sym == "Right", extend=shift, word=True)
                self._reset_target()
                self.refresh()
                return "break"
            if sym in ("BackSpace", "Delete"):
                if doc.delete_word(sym == "Delete"):
                    self._reset_target()
                    self.refresh()
                return "break"
            if sym in ("Home", "End"):
                doc.move(0 if sym == "Home" else len(doc.text), extend=shift)
                self._reset_target()
                self.refresh()
                return "break"
            shortcuts = {
                "a": self.select_all,
                "c": self.copy,
                "x": self.cut,
                "v": self.paste,
                "z": lambda: self.history(False),
                "y": lambda: self.history(True),
                "n": self.new,
                "o": self.open,
                "s": lambda: self.save(as_hylic=shift),
                "f": lambda: self.show_find(False),
                "h": lambda: self.show_find(True),
            }
            action = shortcuts.get(sym.lower())
            if action:
                action()
                return "break"
            return "break"
        if sym in ("Left", "Right"):
            doc.horizontal_move(sym == "Right", extend=shift)
            self._reset_target()
            self.refresh()
            return "break"
        if sym in ("Up", "Down", "Prior", "Next"):
            self._move_vertical(sym in ("Down", "Next"), shift, page=sym in ("Prior", "Next"))
            return "break"
        if sym in ("Home", "End"):
            self._move_home_end(sym == "End", shift)
            return "break"
        if sym == "BackSpace":
            if doc.backspace():
                self._reset_target()
                self.refresh()
            return "break"
        if sym == "Delete":
            if doc.delete_forward():
                self._reset_target()
                self.refresh()
            return "break"
        if sym in ("Return", "KP_Enter"):
            if doc.insert("\n"):
                self._reset_target()
                self.refresh()
            return "break"
        if sym == "Tab":
            if doc.insert("\t", typed=True):
                self._reset_target()
                self.refresh()
            return "break"
        if not alt and event.char and event.char.isprintable() and event.char != "\x7f":
            if doc.insert(event.char, typed=True):
                self._reset_target()
                self.refresh()
            return "break"
        return None

    def _shortcut_find(self, _event, backwards: bool) -> str:
        self.find(backwards)
        return "break"

    def select_all(self) -> None:
        self.doc.select_all()
        self._reset_target()
        self.refresh()

    def copy(self) -> bool:
        if not self.doc.has_selection:
            return False
        first, last = self.doc.selection
        value = self.doc.text[first:last].replace("\n", "\r\n")
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(value)
            self.root.update_idletasks()
        except self.tk.TclError:
            return False
        return True

    def cut(self) -> None:
        if self.copy():
            first, last = self.doc.selection
            if self.doc.change(first, last, ""):
                self._reset_target()
                self.refresh()

    def paste(self) -> None:
        try:
            value = self.root.clipboard_get()
        except self.tk.TclError:
            return
        if value and self.doc.insert(value):
            self._reset_target()
            self.refresh()

    def history(self, redo: bool) -> None:
        if self.doc.undo(redo=redo):
            self._reset_target()
            self._invalidate_layout()
            self.refresh()

    def toggle_wrap(self) -> str:
        self.doc.word_wrap = not self.doc.word_wrap
        self.wrap_var.set(self.doc.word_wrap)
        self.doc.horizontal = 0
        self._reset_target()
        self._reflow()
        return "break"

    def _confirm_change(self, action: str) -> bool:
        from tkinter import messagebox
        if not self.doc.dirty:
            return True
        answer = messagebox.askyesnocancel("Octavo", f"Save changes before {action}?", parent=self.root)
        if answer is None:
            return False
        if answer:
            return self.save()
        return True

    def new(self) -> None:
        if not self._confirm_change("creating a new document"):
            return
        self.doc.clear_document()
        self._reset_target()
        self._invalidate_layout()
        self.refresh()
        self.canvas.focus_set()

    def open(self) -> None:
        from tkinter import filedialog, messagebox
        filename = filedialog.askopenfilename(parent=self.root, title="Open text file",
                                              filetypes=[("Text files", "*.lemur *.txt *.md *.log"), ("All files", "*.*")])
        if not filename or not self._confirm_change("opening another document"):
            return
        try:
            self.doc.open_path(Path(filename))
        except (OSError, UnicodeError, ValueError) as exc:
            messagebox.showerror("Open failed", str(exc), parent=self.root)
            return
        self._reset_target()
        self._invalidate_layout()
        self.refresh()
        self.canvas.focus_set()

    def save(self, *, as_hylic: bool = False) -> bool:
        from tkinter import filedialog, messagebox
        path = self.doc.path
        if as_hylic or path is None:
            if as_hylic:
                suggested = path.with_suffix(".txt").name if path else "Untitled.txt"
                default_ext = ".txt"
                filters = [("Text files", "*.txt"), ("Markdown", "*.md"),
                           ("Log files", "*.log"), ("All files", "*.*")]
            else:
                suggested = "Untitled.lemur"
                default_ext = ".lemur"
                filters = [("Lemur files", "*.lemur"), ("All files", "*.*")]
            chosen = filedialog.asksaveasfilename(parent=self.root,
                                                  title="Save as hylic" if as_hylic else "Save document",
                                                  initialfile=suggested,
                                                  initialdir=str(path.parent) if path else None,
                                                  defaultextension=default_ext, filetypes=filters,
                                                  confirmoverwrite=True)
            if not chosen:
                return False
            path = Path(chosen)
        assert path is not None
        try:
            self.doc.write_path(path)
        except (OSError, UnicodeError, ValueError) as exc:
            messagebox.showerror("Save failed", str(exc), parent=self.root)
            return False
        self.refresh(reveal=False)
        return True

    def close(self) -> None:
        if self._confirm_change("closing Octavo"):
            self._stop_autoscroll()
            self.root.destroy()

    def show_find(self, replace: bool) -> str:
        tk = self.tk
        if self.find_window is not None:
            self.find_window.destroy()
        dialog = tk.Toplevel(self.root)
        self.find_window = dialog
        dialog.title("Replace" if replace else "Find")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        panel = tk.Frame(dialog, padx=12, pady=12)
        panel.pack(fill="both", expand=True)
        tk.Label(panel, text="Find what:").grid(row=0, column=0, sticky="w")
        self.find_entry = tk.Entry(panel, width=35)
        self.find_entry.insert(0, self.doc.search_term)
        self.find_entry.grid(row=0, column=1, columnspan=3, padx=8, pady=4, sticky="ew")
        if replace:
            tk.Label(panel, text="Replace with:").grid(row=1, column=0, sticky="w")
            self.replace_entry = tk.Entry(panel, width=35)
            self.replace_entry.insert(0, self.doc.replace_term)
            self.replace_entry.grid(row=1, column=1, columnspan=3, padx=8, pady=4, sticky="ew")
        self.case_var.set(self.doc.match_case)
        tk.Checkbutton(panel, text="Match case", variable=self.case_var).grid(row=2, column=0, columnspan=2, sticky="w")
        tk.Button(panel, text="Find Next", command=lambda: self._dialog_action("find"), width=12).grid(row=3, column=0, padx=4, pady=7)
        if replace:
            tk.Button(panel, text="Replace", command=lambda: self._dialog_action("replace"), width=12).grid(row=3, column=1, padx=4, pady=7)
            tk.Button(panel, text="Replace All", command=lambda: self._dialog_action("all"), width=12).grid(row=3, column=2, padx=4, pady=7)
        tk.Button(panel, text="Close", command=dialog.destroy, width=12).grid(row=3, column=3, padx=4, pady=7)
        self.find_entry.bind("<Return>", lambda _e: self._dialog_action("find"))
        dialog.bind("<Destroy>", lambda e: self._forget_dialog(e, dialog))
        self.find_entry.focus_set()
        return "break"

    def _forget_dialog(self, event, dialog) -> None:
        if event.widget is dialog and self.find_window is dialog:
            self.find_window = None
            self.canvas.focus_set()

    def _dialog_action(self, action: str) -> None:
        if self.find_window is None:
            return
        self.doc.search_term = self.find_entry.get()[:1023]
        self.doc.match_case = self.case_var.get()
        if self.replace_entry is not None and self.replace_entry.winfo_exists() and action != "find":
            self.doc.replace_term = self.replace_entry.get()[:1023]
        if not self.doc.search_term:
            return
        if action == "find":
            self.find(False)
        elif action == "replace":
            if self.doc.replace_match():
                self._reset_target()
                self.refresh()
            else:
                self.refresh()
        elif action == "all":
            from tkinter import messagebox
            count = self.doc.replace_all()
            self._reset_target()
            self.refresh()
            messagebox.showinfo("Replace All", f"{count} occurrence{'s' if count != 1 else ''} matched.", parent=self.find_window)

    def find(self, backwards: bool) -> None:
        from tkinter import messagebox
        if not self.doc.search_term:
            self.show_find(False)
            return
        if not self.doc.find(backwards=backwards):
            messagebox.showinfo("Find", f"No match found for:\n{self.doc.search_term}",
                                parent=self.find_window or self.root)
        else:
            self._reset_target()
            self.refresh()


def main() -> None:
    import tkinter as tk
    root = tk.Tk()
    Octavo(root)
    root.mainloop()


if __name__ == "__main__":
    main()
