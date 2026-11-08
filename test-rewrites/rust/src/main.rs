//! Octavo v0.1, rebuilt with a Rust editing model and an egui-drawn viewport.
//! The editor owns its buffer, caret, layout, selections, undo, and find/replace.

use eframe::egui::{self, Align2, Color32, Event, FontId, Key, Modifiers, Pos2, Rect, Sense, Stroke, Vec2};
use octavo_rewrite::{Document, Layout, Row};
use std::path::PathBuf;

const MARGIN: f32 = 24.0;
const CHAR_WIDTH: f32 = 10.5;
const LINE_HEIGHT: f32 = 28.0;
const FONT_SIZE: f32 = 18.0;

#[derive(Clone, Copy, PartialEq, Eq)]
enum Dialog { Closed, Find, Replace }
#[derive(Clone, Copy)]
enum Pending { New, Open, Exit }

struct Octavo {
    doc: Document,
    preferred_col: Option<usize>,
    end_affinity: bool,
    top_offset: usize,
    last_width: usize,
    page_rows: usize,
    dragged: bool,
    drag_at: Pos2,
    dialog: Dialog,
    find_input: String,
    replace_input: String,
    pending: Option<Pending>,
    info: Option<String>,
    editor_focus: bool,
    scroll_lines: f32,
    closing: bool,
}

impl Default for Octavo {
    fn default() -> Self {
        Self { doc: Document::new(), preferred_col: None, end_affinity: false,
            top_offset: 0, last_width: 0, page_rows: 1, dragged: false,
            drag_at: Pos2::ZERO, dialog: Dialog::Closed, find_input: String::new(),
            replace_input: String::new(), pending: None, info: None,
            editor_focus: true, scroll_lines: 0.0, closing: false }
    }
}

impl Octavo {
    fn title(&self) -> String {
        let name = self.doc.path.as_ref().and_then(|p| p.file_name())
            .map_or_else(|| "Untitled.lemur".to_owned(), |s| s.to_string_lossy().to_string());
        format!("{}{} — Octavo v0.1 (Rust)", name, if self.doc.dirty() { "*" } else { "" })
    }
    fn reset_target(&mut self) { self.preferred_col = None; self.end_affinity = false; }
    fn layout(&self, width: usize) -> Layout { Layout::new(&self.doc.text, self.doc.wrap, width) }
    fn current_row(&self, layout: &Layout) -> usize {
        layout.row_of(self.doc.cursor, self.end_affinity)
    }
    fn visible(&mut self, layout: &Layout, reveal: bool) {
        let max_first = layout.rows.len().saturating_sub(self.page_rows);
        self.doc.first_row = self.doc.first_row.min(max_first);
        if self.doc.wrap { self.doc.horizontal = 0; }
        if reveal {
            let row = self.current_row(layout);
            if row < self.doc.first_row { self.doc.first_row = row; }
            if row >= self.doc.first_row + self.page_rows {
                self.doc.first_row = (row + 1).saturating_sub(self.page_rows);
            }
            if !self.doc.wrap {
                let x = Layout::span_width(&self.doc.text, layout.rows[row].start, self.doc.cursor);
                if x < self.doc.horizontal { self.doc.horizontal = x; }
                if x > self.doc.horizontal + layout.width {
                    self.doc.horizontal = x - layout.width;
                }
            }
        }
        self.doc.first_row = self.doc.first_row.min(max_first);
        self.top_offset = layout.rows[self.doc.first_row].start;
    }
    fn movement(&mut self, pos: usize, shift: bool) {
        self.doc.move_to(pos, shift); self.reset_target();
    }
    fn horizontal(&mut self, right: bool, shift: bool, word: bool) {
        self.doc.move_horizontal(right, shift, word); self.reset_target();
    }
    fn vertical(&mut self, layout: &Layout, down: bool, shift: bool, page: bool) {
        let row = self.current_row(layout);
        let col = *self.preferred_col.get_or_insert_with(||
            Layout::span_width(&self.doc.text, layout.rows[row].start, self.doc.cursor));
        let steps = if page { self.page_rows } else { 1 };
        let dest = if down { (row + steps).min(layout.rows.len() - 1) }
            else { row.saturating_sub(steps) };
        let pos = layout.offset_at(&self.doc.text, dest, col);
        self.doc.move_to(pos, shift);
        self.end_affinity = layout.rows[dest].soft && pos == layout.rows[dest].end;
    }
    fn boundary(&mut self, layout: &Layout, end: bool, shift: bool, document: bool) {
        let pos = if document { if end { self.doc.text.len() } else { 0 } }
            else {
                let row = layout.rows[self.current_row(layout)];
                if end { row.end } else { row.start }
            };
        let affinity = !document && end && layout.rows[self.current_row(layout)].soft;
        self.doc.move_to(pos, shift); self.reset_target();
        self.end_affinity = affinity;
    }
    fn selected_text(&self) -> String {
        let (a, b) = self.doc.selection();
        self.doc.text[a..b].iter().collect()
    }
    fn copy(&self) {
        if !self.doc.has_selection() { return; }
        if let Ok(mut clip) = arboard::Clipboard::new() {
            let _ = clip.set_text(self.selected_text().replace('\n', "\r\n"));
        }
    }
    fn cut(&mut self) {
        if self.doc.has_selection() { self.copy(); self.doc.insert("", false); self.reset_target(); }
    }
    fn paste(&mut self) {
        if let Ok(mut clip) = arboard::Clipboard::new() {
            if let Ok(text) = clip.get_text() { self.doc.insert(&text, false); self.reset_target(); }
        }
    }
    fn open_dialog(&mut self, replace: bool) {
        self.dialog = if replace { Dialog::Replace } else { Dialog::Find };
        self.find_input = self.doc.search.clone();
        self.replace_input = self.doc.replacement.clone();
        self.editor_focus = false;
    }
    fn find(&mut self, backwards: bool) {
        if self.doc.search.is_empty() { self.open_dialog(false); return; }
        if !self.doc.find(backwards) { self.info = Some(format!("No match found for: {}", self.doc.search)); }
        self.reset_target();
    }
    fn replace(&mut self) { self.doc.replace_match(); self.reset_target(); }
    fn replace_all(&mut self) {
        let n = self.doc.replace_all();
        self.info = Some(format!("{} occurrence{} matched.", n, if n == 1 { "" } else { "s" }));
        self.reset_target();
    }
    fn set_search_fields(&mut self) {
        self.doc.search = self.find_input.clone();
        self.doc.replacement = self.replace_input.clone();
    }
    fn save(&mut self, hylic: bool) -> bool {
        let path = if !hylic {
            self.doc.path.clone().or_else(|| rfd::FileDialog::new()
                .set_title("Save Octavo document").add_filter("Octavo text", &["lemur", "txt"])
                .set_file_name("Untitled.lemur").save_file())
        } else {
            let default_name = self.doc.path.as_ref().and_then(|p| p.file_stem())
                .map_or_else(|| "Untitled".to_owned(), |s| s.to_string_lossy().to_string());
            rfd::FileDialog::new().set_title("Save as hylic")
                .add_filter("Plain text", &["txt", "md", "log"])
                .add_filter("All files", &["*"])
                .set_file_name(&format!("{}.txt", default_name)).save_file()
        };
        let Some(path) = path else { return false; };
        match self.doc.write_path(&path) {
            Ok(()) => true,
            Err(e) => { self.info = Some(format!("Could not save {}: {e}", path.display())); false }
        }
    }
    fn request(&mut self, action: Pending, ctx: &egui::Context) {
        if self.doc.dirty() { self.pending = Some(action); }
        else { self.perform(action, ctx); }
    }
    fn perform(&mut self, action: Pending, ctx: &egui::Context) {
        match action {
            Pending::New => { self.doc.clear_document("", None); self.reset_target(); self.doc.first_row = 0; }
            Pending::Open => {
                if let Some(path) = rfd::FileDialog::new().set_title("Open text")
                    .add_filter("Text files", &["lemur", "txt", "md", "log"])
                    .add_filter("All files", &["*"]).pick_file() {
                    if let Err(e) = self.doc.open_path(&path) {
                        self.info = Some(format!("Could not open {}: {e}", path.display()));
                    } else { self.reset_target(); }
                }
            }
            Pending::Exit => { self.closing = true; ctx.send_viewport_cmd(egui::ViewportCommand::Close); },
        }
    }
    fn key_action(&mut self, key: Key, modifiers: Modifiers, layout: &Layout, ctx: &egui::Context) -> bool {
        let shift = modifiers.shift;
        let cmd = modifiers.command;
        let mut handled = true;
        if cmd {
            match key {
                Key::N => self.request(Pending::New, ctx),
                Key::O => self.request(Pending::Open, ctx),
                Key::S => { self.save(shift); },
                Key::Z => { if shift { self.doc.redo(); } else { self.doc.undo(); } self.reset_target(); },
                Key::Y => { self.doc.redo(); self.reset_target(); },
                Key::F => self.open_dialog(false),
                Key::H => self.open_dialog(true),
                Key::A => { self.doc.select_all(); self.reset_target(); },
                Key::C => self.copy(),
                Key::X => self.cut(),
                Key::V => {}, // Event::Paste carries the clipboard text; do not insert twice.
                Key::ArrowLeft => self.horizontal(false, shift, true),
                Key::ArrowRight => self.horizontal(true, shift, true),
                Key::Backspace => { self.doc.delete_word(false); self.reset_target(); },
                Key::Delete => { self.doc.delete_word(true); self.reset_target(); },
                Key::Home => self.boundary(layout, false, shift, true),
                Key::End => self.boundary(layout, true, shift, true),
                _ => handled = false,
            }
        } else if modifiers.alt && key == Key::Z {
            self.doc.wrap = !self.doc.wrap; self.doc.horizontal = 0;
            self.last_width = 0; self.reset_target();
        } else {
            match key {
                Key::ArrowLeft => self.horizontal(false, shift, false),
                Key::ArrowRight => self.horizontal(true, shift, false),
                Key::ArrowUp => self.vertical(layout, false, shift, false),
                Key::ArrowDown => self.vertical(layout, true, shift, false),
                Key::PageUp => self.vertical(layout, false, shift, true),
                Key::PageDown => self.vertical(layout, true, shift, true),
                Key::Home => self.boundary(layout, false, shift, false),
                Key::End => self.boundary(layout, true, shift, false),
                Key::Backspace => { self.doc.backspace(); self.reset_target(); },
                Key::Delete => { self.doc.delete_forward(); self.reset_target(); },
                Key::Enter => { self.doc.insert("\n", false); self.reset_target(); },
                Key::Tab => { self.doc.insert("\t", true); self.reset_target(); },
                Key::F3 => self.find(shift),
                _ => handled = false,
            }
        }
        handled
    }
    fn input(&mut self, ctx: &egui::Context, layout: &Layout) {
        if !self.editor_focus || self.dialog != Dialog::Closed || self.pending.is_some() { return; }
        let events = ctx.input(|i| i.events.clone());
        let mut changed = false;
        for ev in events {
            match ev {
                Event::Key { key, pressed: true, modifiers, .. } => {
                    changed |= self.key_action(key, modifiers, layout, ctx);
                }
                Event::Text(text) if !text.chars().any(|c| c.is_control()) => {
                    self.doc.insert(&text, true); self.reset_target(); changed = true;
                }
                Event::Paste(text) => { self.doc.insert(&text, false); self.reset_target(); changed = true; }
                Event::Copy => self.copy(),
                Event::Cut => { self.cut(); changed = true; },
                _ => {}
            }
        }
        if changed {
            let updated = self.layout(layout.width);
            self.visible(&updated, true);
        }
    }
    fn position(&self, layout: &Layout, rect: Rect, pos: Pos2) -> (usize, bool) {
        let y = ((pos.y - rect.top() - MARGIN) / LINE_HEIGHT).floor().max(0.0) as usize;
        let row = (self.doc.first_row + y).min(layout.rows.len() - 1);
        let col = ((pos.x - rect.left() - MARGIN) / CHAR_WIDTH + self.doc.horizontal as f32)
            .round().max(0.0) as usize;
        let offset = layout.offset_at(&self.doc.text, row, col);
        let affinity = layout.rows[row].soft && offset == layout.rows[row].end;
        (offset, affinity)
    }
    fn drag_to(&mut self, layout: &Layout, rect: Rect, pos: Pos2) {
        let (offset, affinity) = self.position(layout, rect, pos);
        self.doc.move_to(offset, true);
        self.end_affinity = affinity;
        self.preferred_col = None;
    }
    fn expanded(&self, start: usize, end: usize, initial_col: usize) -> String {
        let mut column = initial_col;
        let mut output = String::new();
        for &ch in &self.doc.text[start..end] {
            if ch == '\t' {
                let spaces = 4 - column % 4;
                output.extend(std::iter::repeat_n(' ', spaces));
                column += spaces;
            } else { output.push(ch); column += 1; }
        }
        output
    }
    fn paint_rows(&self, painter: &egui::Painter, layout: &Layout, rect: Rect) {
        let painter = painter.with_clip_rect(rect.shrink2(Vec2::new(MARGIN.min(rect.width() / 2.0), 0.0)));
        let font = FontId::monospace(FONT_SIZE);
        let (sel_start, sel_end) = self.doc.selection();
        let offset = self.doc.horizontal as f32 * CHAR_WIDTH;
        for i in self.doc.first_row..layout.rows.len() {
            let row: Row = layout.rows[i];
            let y = rect.top() + MARGIN + (i - self.doc.first_row) as f32 * LINE_HEIGHT;
            if y >= rect.bottom() { break; }
            let x = rect.left() + MARGIN - offset;
            let full = self.expanded(row.start, row.end, 0);
            painter.text(Pos2::new(x, y), Align2::LEFT_TOP, full, font.clone(), Color32::BLACK);
            let left = row.start.max(sel_start);
            let right = row.end.min(sel_end);
            if left < right {
                let col = Layout::span_width(&self.doc.text, row.start, left);
                let end_col = Layout::span_width(&self.doc.text, row.start, right);
                let x1 = x + col as f32 * CHAR_WIDTH;
                let x2 = x + end_col as f32 * CHAR_WIDTH;
                painter.rect_filled(Rect::from_min_max(Pos2::new(x1, y), Pos2::new(x2, y + LINE_HEIGHT)), 0.0, Color32::from_rgb(10, 100, 173));
                painter.text(Pos2::new(x1, y), Align2::LEFT_TOP,
                    self.expanded(left, right, col), font.clone(), Color32::WHITE);
            }
            if !row.soft && row.end < self.doc.text.len() && self.doc.text[row.end] == '\n'
                && sel_start <= row.end && row.end < sel_end {
                let x1 = x + Layout::span_width(&self.doc.text, row.start, row.end) as f32 * CHAR_WIDTH;
                painter.rect_filled(Rect::from_min_size(Pos2::new(x1, y), Vec2::new(CHAR_WIDTH, LINE_HEIGHT)), 0.0, Color32::from_rgb(10, 100, 173));
            }
        }
        let caret_row = self.current_row(layout);
        if caret_row >= self.doc.first_row {
            let row = layout.rows[caret_row];
            let x = rect.left() + MARGIN - offset
                + Layout::span_width(&self.doc.text, row.start, self.doc.cursor) as f32 * CHAR_WIDTH;
            let y = rect.top() + MARGIN + (caret_row - self.doc.first_row) as f32 * LINE_HEIGHT;
            if y < rect.bottom() {
                painter.line_segment([Pos2::new(x, y), Pos2::new(x, y + LINE_HEIGHT - 3.0)],
                    Stroke::new(1.6, Color32::BLACK));
            }
        }
    }
    fn menu(&mut self, ui: &mut egui::Ui, ctx: &egui::Context) {
        ui.menu_button("File", |ui| {
            if ui.button("New                 Ctrl+N").clicked() { self.request(Pending::New, ctx); ui.close_menu(); }
            if ui.button("Open...             Ctrl+O").clicked() { self.request(Pending::Open, ctx); ui.close_menu(); }
            ui.separator();
            if ui.button("Save                 Ctrl+S").clicked() { self.save(false); ui.close_menu(); }
            if ui.button("Save as hylic...    Ctrl+Shift+S").clicked() { self.save(true); ui.close_menu(); }
            ui.separator();
            if ui.button("Exit").clicked() { self.request(Pending::Exit, ctx); ui.close_menu(); }
        });
        ui.menu_button("Edit", |ui| {
            if ui.add_enabled(!self.doc.undo.is_empty(), egui::Button::new("Undo              Ctrl+Z")).clicked() { self.doc.undo(); self.reset_target(); ui.close_menu(); }
            if ui.add_enabled(!self.doc.redo.is_empty(), egui::Button::new("Redo              Ctrl+Y")).clicked() { self.doc.redo(); self.reset_target(); ui.close_menu(); }
            ui.separator();
            if ui.add_enabled(self.doc.has_selection(), egui::Button::new("Cut                 Ctrl+X")).clicked() { self.cut(); ui.close_menu(); }
            if ui.add_enabled(self.doc.has_selection(), egui::Button::new("Copy              Ctrl+C")).clicked() { self.copy(); ui.close_menu(); }
            if ui.button("Paste              Ctrl+V").clicked() { self.paste(); ui.close_menu(); }
            if ui.add_enabled(!self.doc.text.is_empty(), egui::Button::new("Select all         Ctrl+A")).clicked() { self.doc.select_all(); ui.close_menu(); }
            ui.separator();
            if ui.button("Find...             Ctrl+F").clicked() { self.open_dialog(false); ui.close_menu(); }
            if ui.button("Find Next         F3").clicked() { self.find(false); ui.close_menu(); }
            if ui.button("Find Previous   Shift+F3").clicked() { self.find(true); ui.close_menu(); }
            if ui.button("Replace...         Ctrl+H").clicked() { self.open_dialog(true); ui.close_menu(); }
        });
        ui.menu_button("View", |ui| {
            let mut wrap = self.doc.wrap;
            if ui.checkbox(&mut wrap, "Word Wrap          Alt+Z").changed() {
                self.doc.wrap = wrap; self.doc.horizontal = 0; self.last_width = 0;
                self.reset_target();
            }
        });
    }
    fn find_window(&mut self, ctx: &egui::Context) {
        if self.dialog == Dialog::Closed { return; }
        let replace = self.dialog == Dialog::Replace;
        let mut open = true;
        egui::Window::new(if replace { "Replace" } else { "Find" })
            .open(&mut open).resizable(false).collapsible(false).show(ctx, |ui| {
                ui.horizontal(|ui| { ui.label("Find:"); ui.text_edit_singleline(&mut self.find_input); });
                if replace {
                    ui.horizontal(|ui| { ui.label("Replace:"); ui.text_edit_singleline(&mut self.replace_input); });
                }
                ui.checkbox(&mut self.doc.match_case, "Match case");
                ui.horizontal(|ui| {
                    if ui.button("Find Next").clicked() { self.set_search_fields(); self.find(false); }
                    if ui.button("Find Previous").clicked() { self.set_search_fields(); self.find(true); }
                    if replace {
                        if ui.button("Replace").clicked() { self.set_search_fields(); self.replace(); }
                        if ui.button("Replace All").clicked() { self.set_search_fields(); self.replace_all(); }
                    }
                });
            });
        if !open { self.dialog = Dialog::Closed; self.editor_focus = true; }
    }
    fn pending_window(&mut self, ctx: &egui::Context) {
        let Some(action) = self.pending else { return; };
        egui::Window::new("Unsaved changes").collapsible(false).resizable(false)
            .show(ctx, |ui| {
                ui.label("Save changes to the current document?");
                ui.horizontal(|ui| {
                    if ui.button("Save").clicked() && self.save(false) {
                        self.pending = None; self.perform(action, ctx);
                    }
                    if ui.button("Discard").clicked() {
                        self.pending = None; self.perform(action, ctx);
                    }
                    if ui.button("Cancel").clicked() { self.pending = None; }
                });
            });
    }
    fn info_window(&mut self, ctx: &egui::Context) {
        let Some(message) = self.info.clone() else { return; };
        egui::Window::new("Octavo").resizable(false).collapsible(false).show(ctx, |ui| {
            ui.label(message);
            if ui.button("OK").clicked() { self.info = None; }
        });
    }
}

impl eframe::App for Octavo {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        if ctx.input(|i| i.viewport().close_requested()) {
            if self.doc.dirty() && !self.closing {
                ctx.send_viewport_cmd(egui::ViewportCommand::CancelClose);
                if self.pending.is_none() { self.pending = Some(Pending::Exit); }
            }
        }
        ctx.send_viewport_cmd(egui::ViewportCommand::Title(self.title()));
        egui::TopBottomPanel::top("menus").show(ctx, |ui| {
            egui::menu::bar(ui, |ui| self.menu(ui, ctx));
        });
        egui::TopBottomPanel::bottom("status").exact_height(54.0).show(ctx, |ui| {
            ui.vertical_centered(|ui| {
                ui.label("Ctrl+F: Find. Ctrl+H: Replace. F3 / Shift+F3: next / previous. Ctrl+S: save. Ctrl+Z/Y: undo/redo.");
            });
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| { ui.label(self.doc.status()); });
        });
        egui::CentralPanel::default().frame(egui::Frame::NONE.fill(Color32::WHITE))
            .show(ctx, |ui| {
                let available = ui.available_size();
                let (rect, response) = ui.allocate_exact_size(available, Sense::click_and_drag());
                let width = ((rect.width() - 2.0 * MARGIN) / CHAR_WIDTH).floor().max(1.0) as usize;
                self.page_rows = ((rect.height() - MARGIN) / LINE_HEIGHT).floor().max(1.0) as usize;
                let mut layout = self.layout(width);
                if width != self.last_width {
                    self.doc.first_row = layout.row_of(self.top_offset, false);
                    self.last_width = width;
                }
                self.visible(&layout, false);
                if response.clicked() || response.drag_started() {
                    if let Some(pos) = response.interact_pointer_pos() {
                        if pos.y < rect.bottom() {
                            self.editor_focus = true;
                            let (at, affinity) = self.position(&layout, rect, pos);
                            self.movement(at, false);
                            self.end_affinity = affinity;
                            self.dragged = response.drag_started();
                            self.drag_at = pos;
                        }
                    }
                }
                if response.dragged() {
                    if let Some(pos) = ctx.input(|i| i.pointer.interact_pos()) {
                        self.dragged = true; self.drag_at = pos;
                        self.drag_to(&layout, rect, pos);
                        if pos.y > rect.bottom() {
                            self.doc.first_row = (self.doc.first_row + 1).min(layout.rows.len().saturating_sub(self.page_rows));
                        } else if pos.y < rect.top() {
                            self.doc.first_row = self.doc.first_row.saturating_sub(1);
                        }
                        self.drag_to(&layout, rect, pos);
                        ctx.request_repaint();
                    }
                }
                if response.drag_stopped() || !ctx.input(|i| i.pointer.primary_down()) { self.dragged = false; }
                if response.hovered() {
                    let scroll = ctx.input(|i| i.raw_scroll_delta);
                    if scroll != Vec2::ZERO {
                        if !self.doc.wrap && ctx.input(|i| i.modifiers.shift) {
                            if scroll.y < 0.0 { self.doc.horizontal = self.doc.horizontal.saturating_add(9); }
                            else { self.doc.horizontal = self.doc.horizontal.saturating_sub(9); }
                        } else {
                            self.scroll_lines += -scroll.y / LINE_HEIGHT;
                            let delta = self.scroll_lines.trunc() as isize;
                            self.scroll_lines -= delta as f32;
                            if delta < 0 { self.doc.first_row = self.doc.first_row.saturating_sub(delta.unsigned_abs()); }
                            else { self.doc.first_row = self.doc.first_row.saturating_add(delta as usize); }
                        }
                    }
                }
                self.input(ctx, &layout);
                layout = self.layout(width);
                self.visible(&layout, false);
                self.paint_rows(&ui.painter_at(rect), &layout, rect);
            });
        self.find_window(ctx);
        self.pending_window(ctx);
        self.info_window(ctx);
    }
}

fn main() -> eframe::Result<()> {
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default().with_inner_size([760.0, 440.0]),
        ..Default::default()
    };
    eframe::run_native("Octavo v0.1 (Rust)", options, Box::new(|_| Ok(Box::new(Octavo::default()))))
}
