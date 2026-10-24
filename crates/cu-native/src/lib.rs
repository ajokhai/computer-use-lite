//! Native macOS layer for Computer Use: accessibility, input, capture, and the MCP server
//! (`cu-mcp`) that lets any AI drive the Mac. The C functions at the bottom are what the
//! Python app loads through ctypes.

pub mod ax;
pub mod capture;
pub mod decision;
pub mod desktop;
pub mod input;
pub mod mcp;

use std::ffi::{CStr, CString};
use std::os::raw::c_char;

use serde::{Deserialize, Serialize};

#[repr(C)]
#[derive(Copy, Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct CGPoint {
    pub x: f64,
    pub y: f64,
}

#[repr(C)]
#[derive(Copy, Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct CGSize {
    pub width: f64,
    pub height: f64,
}

#[repr(C)]
#[derive(Copy, Clone, Debug, Default, PartialEq)]
pub struct CGRect {
    pub origin: CGPoint,
    pub size: CGSize,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct NativeElement {
    pub id: String,
    pub role: String,
    pub label: String,
    pub x: i32,
    pub y: i32,
    pub w: i32,
    pub h: i32,
    pub focused: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AxTreeResult {
    pub app: String,
    pub duration_ms: f64,
    pub elements: Vec<NativeElement>,
}

pub use ax::get_frontmost_app;

pub fn walk_ax_tree(max_elements: usize) -> AxTreeResult {
    ax::walk(max_elements, false).result
}

// --- C interface ------------------------------------------------------------------------

/// Run an FFI body, turning a panic into `fallback` instead of aborting the host process.
fn ffi_guard<T>(fallback: T, f: impl FnOnce() -> T) -> T {
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(f)).unwrap_or(fallback)
}

/// Serialize to a C string the caller frees with `cu_free_string`; null on failure or panic.
fn json_out<S: Serialize>(f: impl FnOnce() -> S) -> *mut c_char {
    ffi_guard(std::ptr::null_mut(), || {
        serde_json::to_string(&f())
            .ok()
            .and_then(|j| CString::new(j).ok())
            .map_or(std::ptr::null_mut(), CString::into_raw)
    })
}

unsafe fn c_str<'a>(p: *const c_char) -> Option<&'a str> {
    if p.is_null() { None } else { CStr::from_ptr(p).to_str().ok() }
}

/// Every `*mut c_char` returned by this library must be released with this.
///
/// # Safety
/// `s` must be null or a pointer returned by this library, freed only once.
#[no_mangle]
pub unsafe extern "C" fn cu_free_string(s: *mut c_char) {
    if !s.is_null() {
        let _ = CString::from_raw(s);
    }
}

#[no_mangle]
pub extern "C" fn cu_walk_ax_tree_json(max_elements: usize) -> *mut c_char {
    json_out(|| walk_ax_tree(max_elements))
}

#[no_mangle]
pub extern "C" fn cu_click(x: f64, y: f64, button: u32, clicks: usize) {
    ffi_guard((), || input::click_at(x, y, button, clicks));
}

#[no_mangle]
pub extern "C" fn cu_move_to(x: f64, y: f64, duration_ms: f64) {
    ffi_guard((), || input::move_to_smooth(x, y, duration_ms));
}

/// # Safety
/// `out_x` and `out_y` must each be null or point to a writable f64.
#[no_mangle]
pub unsafe extern "C" fn cu_mouse_pos(out_x: *mut f64, out_y: *mut f64) {
    let p = input::get_mouse_location();
    if !out_x.is_null() {
        *out_x = p.x;
    }
    if !out_y.is_null() {
        *out_y = p.y;
    }
}

#[no_mangle]
pub extern "C" fn cu_key_press(key_code: u16, flags: u64) {
    ffi_guard((), || input::key_press(key_code, flags));
}

/// Press a chord such as "command\x1fshift\x1f4". Returns false, pressing nothing,
/// if any key name is unknown, so the caller can fall back instead of sending a wrong key.
///
/// # Safety
/// `keys_csv` must be null or a valid NUL-terminated string.
#[no_mangle]
pub unsafe extern "C" fn cu_hotkey(keys_csv: *const c_char) -> bool {
    let Some(s) = c_str(keys_csv) else { return false };
    ffi_guard(false, || input::hotkey(&input::split_chord(s)))
}

#[no_mangle]
pub extern "C" fn cu_scroll(delta_y: i32, delta_x: i32) {
    ffi_guard((), || input::scroll(delta_y, delta_x));
}

#[no_mangle]
pub extern "C" fn cu_drag(x1: f64, y1: f64, x2: f64, y2: f64, duration_ms: f64) {
    ffi_guard((), || input::drag_to(x1, y1, x2, y2, duration_ms));
}

/// # Safety
/// `out_path` must be null or a valid NUL-terminated string.
#[no_mangle]
pub unsafe extern "C" fn cu_capture_to_file(x: f64, y: f64, w: f64, h: f64, out_path: *const c_char) -> bool {
    let Some(path) = c_str(out_path) else { return false };
    ffi_guard(false, || capture::capture_rect_to_png(x, y, w, h, path))
}

/// # Safety
/// `task` must be null or a valid NUL-terminated string.
#[no_mangle]
pub unsafe extern "C" fn cu_extract_text_candidates(task: *const c_char, limit: usize) -> *mut c_char {
    json_out(|| decision::extract_text_candidates(c_str(task).unwrap_or(""), limit))
}
