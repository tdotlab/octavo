# Rust Octavo port

- Source: Octavo commit 01ce815932855f1b6dadb34c81b3b940092408ae.
- `src/lib.rs` is the standard-library-only editing engine and its tests.
- `src/main.rs` uses egui/eframe for drawing and rfd/arboard for OS integration.
- Status: DRAFT / uncompiled. Published on main at the owner’s request.
  Do not represent as verified until `cargo test --lib`, `cargo check`,
  and a manual desktop smoke test succeed.
- Keep editing logic in lib.rs; do not switch to a native multiline editor.
