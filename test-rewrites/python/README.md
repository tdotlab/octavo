# Octavo — Python port

Standalone Python/Tkinter rewrite of [tdotlab/octavo](https://github.com/tdotlab/octavo) (Octavo v0.1) at source commit [`01ce815932855f1b6dadb34c81b3b940092408ae`](https://github.com/tdotlab/octavo/tree/01ce815932855f1b6dadb34c81b3b940092408ae), authored 2026-10-27 and inspected 2026-11-04. This port lives in `test-rewrites/python/`; the original editor is unchanged.

## Requirements and run

Python 3.10 or newer, with Tkinter installed. No pip dependencies.

```sh
python octavo.py
```

On some Linux distributions, Tkinter is an optional OS package (`python3-tk`). Run the included tests using:

```sh
python -m unittest -v
```

GUI tests need a display. On a headless Linux machine with Xvfb installed:

```sh
xvfb-run -a python -m unittest -v
```

## Features

A custom-drawn plain-text editor rather than a native multiline text control. Supports keyboard/mouse selection; clipboard; word and visual-row movement; tabs every four columns; word wrap (on by default) and Alt+Z toggle; page movement; wheel scrolling, Shift+wheel sideways scrolling when unwrapped; drag autoscroll; 100-entry, full-snapshot undo/redo with grouped typing; case-sensitive or case-insensitive Find, Replace, and Replace All; menu actions and status footer.

New files default to `.lemur`; Save as hylic proposes `.txt`. Files are strict UTF-8 (optional BOM on read, none on write), stored internally with LF and written with CRLF. Saving writes through an adjacent temporary file and uses `os.replace`. Unsaved changes prompt before New, Open, or Exit.

Most original keyboard shortcuts are preserved, including Ctrl+N/O/S, Ctrl+Shift+S, Ctrl+Z/Y, Ctrl+C/X/V/A, Ctrl+F/H, F3/Shift+F3, Ctrl+Arrow, Ctrl+Backspace/Delete, Home/End, Ctrl+Home/End, and Page Up/Down.

## Differences and limits

- The original uses Win32/GDI and stores UTF-16 code-unit offsets. This version uses Tkinter and Python Unicode code-point offsets, which avoids splitting supplementary Unicode characters but may produce different selection counts for unusual combinations of code points.
- Native Windows dialogs, GDI text metrics, and font substitution have been replaced by Tk equivalents. Visual rendering and precise wrapping may differ by platform.
- The original's advanced horizontal-wheel event is not mapped across every operating system; Shift+wheel works without wrapping.
- As with the original, there are no visible scrollbars, blinking caret, syntax coloring, crash recovery, or grapheme-cluster-aware cursor movements. Full-buffer undo and reflow are intended for small text files.

## Files

- `octavo.py` — complete runnable program: independent `Document`, `TextLayout`, and Tkinter `Octavo` frontend
- `test_octavo.py` — headless document/layout tests plus GUI smoke tests
- `AGENTS.md` — concise project map for future edits

## Verification

Last verified 2026-11-04: 16 tests passed (Python 3.13, `xvfb-run -a python -m unittest -v`). The GUI tests are smoke tests, not a visual equivalence guarantee.
