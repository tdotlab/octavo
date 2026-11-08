# Octavo — experimental language rewrites

Independent ports of the original [Octavo](https://github.com/tdotlab/octavo) text editor. These are experiments, not replacements for the Windows C++ program. The original `src/`, `tests/`, and build files are unchanged.

## Source revision

The reference for the initial ports is **upstream commit [`01ce815932855f1b6dadb34c81b3b940092408ae`](https://github.com/tdotlab/octavo/tree/01ce815932855f1b6dadb34c81b3b940092408ae)**, committed **2026-10-27 03:51 UTC** ("Rewrite README for public repository"), inspected **2026-11-04**. The upstream UI identifies itself as **Octavo v0.1**, but the SHA is the actual version marker: future v0.1 code may not be identical to this snapshot.

Each port's README records its own source revision and compatibility differences. New rewrites should pin a full SHA, not just a branch or informal release number.

| Rewrite | Location | Status |
| --- | --- | --- |
| Python / Tkinter | [`python/`](python/) | Runnable; 16 included tests passed under Python 3.13 and Xvfb on 2026-11-04 |
| Rust / egui | [`rust/`](rust/) | Source draft committed at owner's request; not yet compiled or runtime-tested |

The rewrites intentionally implement their **own text buffers and editing semantics**, rather than using a native multiline text widget. UI toolkits are used for windows, fonts, painting, menus and dialogs. They do not guarantee pixel-identical rendering or behavior across platforms.
