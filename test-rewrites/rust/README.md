# Octavo — Rust rewrite (local draft)

**Status: source draft, NOT compiled or runtime-tested.** Published at the
repository owner’s request; it is not verified to build or run.

This is an independent Rust rewrite of Octavo v0.1 at source commit
[`01ce815932855f1b6dadb34c81b3b940092408ae`](https://github.com/tdotlab/octavo/tree/01ce815932855f1b6dadb34c81b3b940092408ae),
committed 2026-10-27, examined 2026-11-04.

## Build / test

Requires a recent stable Rust toolchain and Cargo. Dependencies are `eframe`,
`rfd`, and `arboard`; Cargo downloads them from crates.io.

```sh
cargo test --lib
cargo check
cargo run --release
```

`src/lib.rs` holds the editor's text buffer, selection, undo/redo, case-aware
search/replace, word motions, tab-aware wrapping, and UTF-8/CRLF file handling.
It includes unit tests.

`src/main.rs` is a custom-drawn egui/eframe frontend (not a stock multiline
text widget), with keyboard/mouse input, menus, file dialogs, find/replace,
scrolling, status and unsaved-change prompts.

## Deliberate differences

The original is Win32/GDI using UTF-16 offsets and a proportional font.
This port uses Rust `char` offsets and a fixed-width character grid, so Unicode
supplementary characters are not split but typography and wrap positions differ.
The original has no external libraries; this desktop GUI uses Rust crates.

This draft needs compilation and GUI verification before it can be considered
a finished port. The companion Python rewrite has passed its existing test suite.
