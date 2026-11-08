//! Octavo's platform-independent editing engine.
//! All offsets are Unicode scalar-value indices (not UTF-8 byte offsets).
//! Like the original, edits use bounded full-document undo snapshots.

use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

#[derive(Clone, Debug)]
pub struct Snapshot {
    text: Vec<char>,
    cursor: usize,
    anchor: usize,
    first_row: usize,
    horizontal: usize,
}

#[derive(Debug)]
pub struct Document {
    pub text: Vec<char>,
    pub cursor: usize,
    pub anchor: usize,
    pub path: Option<PathBuf>,
    saved: Vec<char>,
    pub undo: Vec<Snapshot>,
    pub redo: Vec<Snapshot>,
    pub first_row: usize,
    pub horizontal: usize,
    pub search: String,
    pub replacement: String,
    pub match_case: bool,
    pub wrap: bool,
    typed_at: Option<Instant>,
    typed_cursor: usize,
    typed_len: usize,
}

impl Default for Document {
    fn default() -> Self {
        Self {
            text: Vec::new(), cursor: 0, anchor: 0, path: None,
            saved: Vec::new(), undo: Vec::new(), redo: Vec::new(),
            first_row: 0, horizontal: 0, search: String::new(),
            replacement: String::new(), match_case: true, wrap: true,
            typed_at: None, typed_cursor: 0, typed_len: 0,
        }
    }
}

impl Document {
    const HISTORY_LIMIT: usize = 100;

    pub fn new() -> Self { Self::default() }
    pub fn as_string(&self) -> String { self.text.iter().collect() }
    pub fn dirty(&self) -> bool { self.text != self.saved }
    pub fn selection(&self) -> (usize, usize) {
        (self.cursor.min(self.anchor), self.cursor.max(self.anchor))
    }
    pub fn has_selection(&self) -> bool { self.cursor != self.anchor }
    fn snapshot(&self) -> Snapshot {
        Snapshot { text: self.text.clone(), cursor: self.cursor, anchor: self.anchor,
            first_row: self.first_row, horizontal: self.horizontal }
    }
    fn stop_typing(&mut self) { self.typed_at = None; }
    fn push(stack: &mut Vec<Snapshot>, snapshot: Snapshot) {
        stack.push(snapshot);
        if stack.len() > Self::HISTORY_LIMIT { stack.remove(0); }
    }
    fn restore(&mut self, s: Snapshot) {
        self.text = s.text;
        self.cursor = s.cursor;
        self.anchor = s.anchor;
        self.first_row = s.first_row;
        self.horizontal = s.horizontal;
        self.stop_typing();
    }
    pub fn undo(&mut self) -> bool {
        let Some(s) = self.undo.pop() else { return false; };
        let now = self.snapshot();
        Self::push(&mut self.redo, now);
        self.restore(s);
        true
    }
    pub fn redo(&mut self) -> bool {
        let Some(s) = self.redo.pop() else { return false; };
        let now = self.snapshot();
        Self::push(&mut self.undo, now);
        self.restore(s);
        true
    }

    pub fn normalize(s: &str) -> String { s.replace("\r\n", "\n").replace('\r', "\n") }

    /// Replace [start, end) as one edit. `typed` merges consecutive adjacent typing.
    pub fn change(&mut self, start: usize, end: usize, text: &str, typed: bool) -> bool {
        assert!(start <= end && end <= self.text.len());
        let replacement: Vec<char> = Self::normalize(text).chars().collect();
        if self.text[start..end] == replacement[..] {
            self.cursor = start + replacement.len();
            self.anchor = self.cursor;
            self.stop_typing();
            return false;
        }
        let now = Instant::now();
        let contiguous = typed && start == end && self.cursor == self.anchor
            && start == self.cursor && !self.undo.is_empty()
            && self.typed_cursor == self.cursor && self.typed_len == self.text.len()
            && self.typed_at.is_some_and(|t| now.duration_since(t) <= Duration::from_secs(2));
        if !contiguous {
            let prior = self.snapshot();
            Self::push(&mut self.undo, prior);
        }
        self.redo.clear();
        self.text.splice(start..end, replacement.iter().copied());
        self.cursor = start + replacement.len();
        self.anchor = self.cursor;
        if typed {
            self.typed_at = Some(now);
            self.typed_cursor = self.cursor;
            self.typed_len = self.text.len();
        } else { self.stop_typing(); }
        true
    }
    pub fn insert(&mut self, text: &str, typed: bool) -> bool {
        let (a, b) = self.selection();
        self.change(a, b, text, typed && a == b && text != "\n")
    }
    pub fn backspace(&mut self) -> bool {
        let (a, b) = self.selection();
        if a != b { self.change(a, b, "", false) }
        else if a > 0 { self.change(a - 1, a, "", false) }
        else { self.stop_typing(); false }
    }
    pub fn delete_forward(&mut self) -> bool {
        let (a, b) = self.selection();
        if a != b { self.change(a, b, "", false) }
        else if a < self.text.len() { self.change(a, a + 1, "", false) }
        else { self.stop_typing(); false }
    }
    fn word_class(ch: char) -> u8 {
        if ch == ' ' || ch == '\t' { 0 }
        else if ch == '_' || ch.is_alphanumeric() { 1 }
        else { 2 }
    }
    pub fn next_word(&self, mut pos: usize) -> usize {
        if pos >= self.text.len() { return self.text.len(); }
        if self.text[pos] == '\n' { return pos + 1; }
        let class = Self::word_class(self.text[pos]);
        if class != 0 {
            while pos < self.text.len() && self.text[pos] != '\n'
                && Self::word_class(self.text[pos]) == class { pos += 1; }
        }
        while pos < self.text.len() && Self::word_class(self.text[pos]) == 0 { pos += 1; }
        pos
    }
    pub fn previous_word(&self, mut pos: usize) -> usize {
        if pos == 0 { return 0; }
        if self.text[pos - 1] == '\n' { return pos - 1; }
        while pos > 0 && Self::word_class(self.text[pos - 1]) == 0 { pos -= 1; }
        if pos == 0 || self.text[pos - 1] == '\n' { return pos; }
        let class = Self::word_class(self.text[pos - 1]);
        while pos > 0 && self.text[pos - 1] != '\n'
            && Self::word_class(self.text[pos - 1]) == class { pos -= 1; }
        pos
    }
    pub fn delete_word(&mut self, forward: bool) -> bool {
        let (a, b) = self.selection();
        if a != b { return self.change(a, b, "", false); }
        if forward { self.change(a, self.next_word(a), "", false) }
        else { self.change(self.previous_word(a), a, "", false) }
    }
    pub fn move_to(&mut self, pos: usize, extend: bool) {
        self.cursor = pos.min(self.text.len());
        if !extend { self.anchor = self.cursor; }
        self.stop_typing();
    }
    pub fn move_horizontal(&mut self, right: bool, extend: bool, word: bool) {
        let target = if word {
            if right { self.next_word(self.cursor) } else { self.previous_word(self.cursor) }
        } else if self.has_selection() && !extend {
            let (a, b) = self.selection(); if right { b } else { a }
        } else if right { (self.cursor + 1).min(self.text.len()) }
        else { self.cursor.saturating_sub(1) };
        self.move_to(target, extend);
    }
    pub fn select_all(&mut self) { self.anchor = 0; self.cursor = self.text.len(); }
    pub fn status(&self) -> String {
        let before = &self.text[..self.cursor];
        let line = before.iter().filter(|&&c| c == '\n').count() + 1;
        let col = before.iter().rev().take_while(|&&c| c != '\n').count() + 1;
        let mut value = format!("Ln {line}, Col {col}");
        if self.has_selection() {
            let (a, b) = self.selection();
            value.push_str(&format!("  ({} selected)", b - a));
        }
        value
    }
    pub fn clear_document(&mut self, text: &str, path: Option<PathBuf>) {
        self.text = Self::normalize(text).chars().collect();
        self.saved = self.text.clone();
        self.cursor = 0; self.anchor = 0;
        self.first_row = 0; self.horizontal = 0;
        self.path = path;
        self.undo.clear(); self.redo.clear(); self.stop_typing();
    }
    pub fn read_path(path: &Path) -> io::Result<String> {
        let bytes = fs::read(path)?;
        let raw = String::from_utf8(bytes).map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;
        let without_bom = raw.strip_prefix('\u{feff}').unwrap_or(&raw);
        if without_bom.contains('\0') {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "embedded NUL is not plain text"));
        }
        Ok(Self::normalize(without_bom))
    }
    pub fn open_path(&mut self, path: &Path) -> io::Result<()> {
        let text = Self::read_path(path)?;
        self.clear_document(&text, Some(path.to_path_buf()));
        Ok(())
    }
    pub fn write_path(&mut self, path: &Path) -> io::Result<()> {
        static NEXT: AtomicU64 = AtomicU64::new(1);
        let output = self.as_string().replace('\n', "\r\n");
        let parent = path.parent().unwrap_or_else(|| Path::new("."));
        let mut temp_path = None;
        let mut stream = None;
        for _ in 0..16 {
            let candidate = parent.join(format!(".octavo-{}-{}.tmp", std::process::id(), NEXT.fetch_add(1, Ordering::Relaxed)));
            match OpenOptions::new().write(true).create_new(true).open(&candidate) {
                Ok(file) => { temp_path = Some(candidate); stream = Some(file); break; }
                Err(e) if e.kind() == io::ErrorKind::AlreadyExists => continue,
                Err(e) => return Err(e),
            }
        }
        let tmp = temp_path.ok_or_else(|| io::Error::new(io::ErrorKind::AlreadyExists, "cannot allocate temporary file"))?;
        let result = (|| {
            let mut file = stream.expect("temporary file was created");
            file.write_all(output.as_bytes())?;
            file.flush()?;
            file.sync_all()?;
            drop(file);
            fs::rename(&tmp, path)
        })();
        if result.is_err() { let _ = fs::remove_file(&tmp); }
        result?;
        self.path = Some(path.to_path_buf());
        self.saved = self.text.clone();
        Ok(())
    }

    fn chars_equal(a: char, b: char, match_case: bool) -> bool {
        a == b || (!match_case && a.to_lowercase().eq(b.to_lowercase()))
    }
    fn matches(&self) -> Vec<(usize, usize)> {
        let needle: Vec<char> = self.search.chars().collect();
        if needle.is_empty() || needle.len() > self.text.len() { return Vec::new(); }
        let mut out = Vec::new();
        let mut at = 0;
        while at + needle.len() <= self.text.len() {
            if self.text[at..at + needle.len()].iter().zip(&needle)
                .all(|(&a, &b)| Self::chars_equal(a, b, self.match_case)) {
                out.push((at, at + needle.len()));
                at += needle.len();
            } else { at += 1; }
        }
        out
    }
    pub fn find(&mut self, backward: bool) -> bool {
        let matches = self.matches();
        if matches.is_empty() { return false; }
        let (a, b) = self.selection();
        let hit = if backward {
            matches.iter().rev().find(|(_, end)| *end <= a).or_else(|| matches.last())
        } else {
            matches.iter().find(|(start, _)| *start >= b).or_else(|| matches.first())
        };
        let Some(&(start, end)) = hit else { return false; };
        self.anchor = start; self.cursor = end;
        self.stop_typing();
        true
    }
    pub fn selection_matches_search(&self) -> bool {
        if !self.has_selection() || self.search.is_empty() { return false; }
        let (a, b) = self.selection();
        let needle: Vec<char> = self.search.chars().collect();
        b - a == needle.len() && self.text[a..b].iter().zip(needle)
            .all(|(&c, q)| Self::chars_equal(c, q, self.match_case))
    }
    /// First Replace selects a match if none selected, second changes that selection.
    pub fn replace_match(&mut self) -> bool {
        if !self.selection_matches_search() { self.find(false); return false; }
        let (a, b) = self.selection();
        let replacement = self.replacement.clone();
        self.change(a, b, &replacement, false)
    }
    /// Replace All scans only the original input, and is one undoable edit.
    pub fn replace_all(&mut self) -> usize {
        let matches = self.matches();
        if matches.is_empty() { return 0; }
        let mut new = String::new();
        let mut from = 0;
        let mut caret = 0;
        for &(start, end) in &matches {
            new.extend(self.text[from..start].iter());
            new.push_str(&self.replacement);
            caret = new.chars().count();
            from = end;
        }
        new.extend(self.text[from..].iter());
        if new != self.as_string() {
            let end = self.text.len();
            self.change(0, end, &new, false);
            self.move_to(caret, false);
        }
        matches.len()
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Row { pub start: usize, pub end: usize, pub soft: bool }

#[derive(Debug)]
pub struct Layout {
    pub rows: Vec<Row>,
    pub width: usize,
}

impl Layout {
    /// Plain text uses one grid cell per Unicode scalar and four-column tab stops.
    pub fn new(text: &[char], wrap: bool, width: usize) -> Self {
        let width = width.max(1);
        let mut rows = Vec::new();
        let mut start = 0;
        loop {
            let end = text[start..].iter().position(|&c| c == '\n').map_or(text.len(), |i| start + i);
            if wrap { Self::wrap_line(text, start, end, width, &mut rows); }
            else { rows.push(Row { start, end, soft: false }); }
            if end == text.len() { break; }
            start = end + 1;
        }
        Self { rows, width }
    }
    pub fn span_width(text: &[char], start: usize, end: usize) -> usize {
        let mut width = 0;
        for &ch in &text[start..end] {
            width += if ch == '\t' { 4 - (width % 4) } else { 1 };
        }
        width
    }
    fn wrap_line(text: &[char], start: usize, end: usize, width: usize, rows: &mut Vec<Row>) {
        let mut at = start;
        loop {
            let mut fit = at;
            let mut cells = 0;
            while fit < end {
                let step = if text[fit] == '\t' { 4 - (cells % 4) } else { 1 };
                if cells + step > width { break; }
                cells += step; fit += 1;
            }
            if fit == end { rows.push(Row { start: at, end, soft: false }); break; }
            let cut = if fit == at { at + 1 } else { fit };
            let blank = |c: char| c == ' ' || c == '\t';
            let mut split = cut;
            if cut < end && blank(text[cut]) {
                while split < end && blank(text[split]) { split += 1; }
            } else {
                for i in (at..cut).rev() {
                    if blank(text[i]) { split = i + 1; break; }
                }
            }
            if split >= end { rows.push(Row { start: at, end, soft: false }); break; }
            rows.push(Row { start: at, end: split, soft: true });
            at = split;
        }
    }
    pub fn row_of(&self, offset: usize, end_affinity: bool) -> usize {
        let mut row = self.rows.partition_point(|r| r.end < offset);
        row = row.min(self.rows.len().saturating_sub(1));
        if self.rows[row].soft && offset == self.rows[row].end && !end_affinity {
            row = (row + 1).min(self.rows.len() - 1);
        }
        row
    }
    pub fn offset_at(&self, text: &[char], row: usize, x: usize) -> usize {
        let line = self.rows[row];
        let mut px = 0;
        for i in line.start..line.end {
            let width = if text[i] == '\t' { 4 - (px % 4) } else { 1 };
            if x < px + (width + 1) / 2 { return i; }
            px += width;
        }
        line.end
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn undo_typing_and_newlines() {
        let mut d = Document::new();
        for c in "hello".chars() { d.insert(&c.to_string(), true); }
        assert_eq!(d.undo.len(), 1);
        assert!(d.undo()); assert_eq!(d.as_string(), "");
        assert!(d.redo()); assert_eq!(d.as_string(), "hello");
        d.insert("\n", false); d.insert("x", true);
        assert!(d.undo()); assert_eq!(d.as_string(), "hello\n");
    }
    #[test] fn delete_word_and_selection() {
        let mut d = Document::new(); d.clear_document("hello  world\nnext", None);
        assert_eq!(d.next_word(0), 7);
        d.move_to(7, false); assert!(d.delete_word(false));
        assert_eq!(d.as_string(), "world\nnext");
        d.anchor = 0; d.cursor = 5; d.insert("X", false);
        assert_eq!(d.as_string(), "X\nnext");
    }
    #[test] fn search_wrap_and_replace_all() {
        let mut d = Document::new(); d.clear_document("Red red RED", None);
        d.search = "Red".into(); assert!(d.find(false));
        assert_eq!(d.selection(), (0, 3));
        d.match_case = false; assert!(d.find(false));
        assert_eq!(d.selection(), (4, 7));
        d.replacement = "green".into(); assert_eq!(d.replace_all(), 3);
        assert_eq!(d.as_string(), "green green green");
        assert!(d.undo()); assert_eq!(d.as_string(), "Red red RED");
    }
    #[test] fn replace_all_nonrecursive() {
        let mut d = Document::new(); d.clear_document("aaaa", None);
        d.search = "aa".into(); d.replacement = "aaa".into();
        assert_eq!(d.replace_all(), 2); assert_eq!(d.as_string(), "aaaaaa");
    }
    #[test] fn wrap_rows_and_empty_lines() {
        let s: Vec<char> = "alpha beta gamma delta ".repeat(30).chars().collect();
        let l = Layout::new(&s, true, 12);
        assert!(l.rows.len() > 5);
        for pair in l.rows.windows(2) {
            assert_eq!(pair[1].start, pair[0].end + usize::from(!pair[0].soft));
        }
        assert_eq!(l.rows.last().unwrap().end, s.len());
        let s: Vec<char> = "a\n\nb\n".chars().collect();
        let l = Layout::new(&s, true, 12);
        assert_eq!(l.rows.len(), 4); assert_eq!(l.rows[1].start, l.rows[1].end);
    }
    #[test] fn tabs_affinity_and_unicode() {
        let s: Vec<char> = "😀".repeat(100).chars().collect();
        let l = Layout::new(&s, true, 8);
        assert_eq!(l.rows[0].end, 8);
        assert_eq!(l.row_of(8, false), 1);
        assert_eq!(l.row_of(8, true), 0);
        let s: Vec<char> = "a\tb".chars().collect();
        assert_eq!(Layout::span_width(&s, 0, 3), 5);
    }
    #[test] fn io_roundtrip_and_invalid_utf8() {
        let mut d = Document::new();
        let path = std::env::temp_dir().join(format!("octavo-rust-test-{}.lemur", std::process::id()));
        d.insert("a\n😀\n", false); d.write_path(&path).unwrap();
        assert_eq!(fs::read(&path).unwrap(), "a\r\n😀\r\n".as_bytes());
        assert!(!d.dirty()); d.open_path(&path).unwrap();
        assert_eq!(d.as_string(), "a\n😀\n");
        fs::write(&path, [0xff]).unwrap(); assert!(d.open_path(&path).is_err());
        assert_eq!(d.as_string(), "a\n😀\n");
        fs::remove_file(path).unwrap();
    }
}
