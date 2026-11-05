# Agent instructions

This folder is the standalone Python translation of Octavo, not the original GitHub repository. Do not push to or edit `tdotlab/octavo` without an explicit new request. Keep the program self-contained with no third-party Python dependencies.

Entry point: `octavo.py:main`. `Document` owns file/editor state, search, and undo. `TextLayout` handles visual rows and tab-aware widths. `Octavo` renders to a Tk Canvas and handles input/dialogs. Tests: `test_octavo.py` (`python -m unittest -v`; use Xvfb for GUI tests on headless Linux). Keep this README and tests current for material changes.
