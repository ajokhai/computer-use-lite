use std::ffi::c_void;
use std::thread;
use std::time::Duration;

pub use crate::CGPoint;

type CGEventRef = *mut c_void;
type CGEventSourceRef = *mut c_void;

const K_CG_HID_EVENT_TAP: u32 = 0;

const K_CG_EVENT_LEFT_MOUSE_DOWN: u32 = 1;
const K_CG_EVENT_LEFT_MOUSE_UP: u32 = 2;
const K_CG_EVENT_RIGHT_MOUSE_DOWN: u32 = 3;
const K_CG_EVENT_RIGHT_MOUSE_UP: u32 = 4;
const K_CG_EVENT_MOUSE_MOVED: u32 = 5;

const K_CG_MOUSE_BUTTON_LEFT: u32 = 0;
const K_CG_MOUSE_BUTTON_RIGHT: u32 = 1;
const K_CG_MOUSE_BUTTON_CENTER: u32 = 2;
const K_CG_EVENT_OTHER_MOUSE_DOWN: u32 = 25;
const K_CG_EVENT_OTHER_MOUSE_UP: u32 = 26;

pub const K_CG_EVENT_FLAG_MASK_COMMAND: u64 = 0x00100000;
pub const K_CG_EVENT_FLAG_MASK_SHIFT: u64 = 0x00020000;
pub const K_CG_EVENT_FLAG_MASK_CONTROL: u64 = 0x00040000;
pub const K_CG_EVENT_FLAG_MASK_ALTERNATE: u64 = 0x00080000; // Option

#[link(name = "CoreGraphics", kind = "framework")]
#[link(name = "CoreFoundation", kind = "framework")]
unsafe extern "C" {
    fn CGEventCreateMouseEvent(
        source: CGEventSourceRef,
        mouse_type: u32,
        position: CGPoint,
        button: u32,
    ) -> CGEventRef;
    fn CGEventCreateKeyboardEvent(
        source: CGEventSourceRef,
        virtual_key: u16,
        key_down: bool,
    ) -> CGEventRef;
    fn CGEventSetFlags(event: CGEventRef, flags: u64);
    fn CGEventPost(tap: u32, event: CGEventRef);
    fn CFRelease(cf: *const c_void);

    fn CGEventCreate(source: CGEventSourceRef) -> CGEventRef;
    fn CGEventGetLocation(event: CGEventRef) -> CGPoint;
    fn CGEventKeyboardSetUnicodeString(event: CGEventRef, length: usize, string: *const u16);
    fn CGEventSetIntegerValueField(event: CGEventRef, field: u32, value: i64);
}

const K_CG_MOUSE_EVENT_CLICK_STATE: u32 = 1;

pub fn get_mouse_location() -> CGPoint {
    unsafe {
        let ev = CGEventCreate(std::ptr::null_mut());
        if ev.is_null() {
            return CGPoint::default();
        }
        let loc = CGEventGetLocation(ev);
        CFRelease(ev as *const c_void);
        loc
    }
}

pub fn post_mouse_move(x: f64, y: f64) {
    unsafe {
        let pt = CGPoint { x, y };
        let ev = CGEventCreateMouseEvent(
            std::ptr::null_mut(),
            K_CG_EVENT_MOUSE_MOVED,
            pt,
            K_CG_MOUSE_BUTTON_LEFT,
        );
        if !ev.is_null() {
            CGEventPost(K_CG_HID_EVENT_TAP, ev);
            CFRelease(ev as *const c_void);
        }
    }
}

pub fn move_to_smooth(target_x: f64, target_y: f64, duration_ms: f64) {
    if duration_ms <= 0.0 {
        post_mouse_move(target_x, target_y);
        return;
    }

    let start = get_mouse_location();
    let dx = target_x - start.x;
    let dy = target_y - start.y;
    let dist = (dx * dx + dy * dy).sqrt();

    if dist < 2.0 {
        post_mouse_move(target_x, target_y);
        return;
    }

    let steps = (duration_ms / 8.0).clamp(5.0, 30.0) as usize;
    let step_delay = Duration::from_secs_f64((duration_ms / 1000.0) / steps as f64);

    for i in 1..=steps {
        let t = i as f64 / steps as f64;
        // easeOutQuad: t * (2 - t)
        let ease = t * (2.0 - t);
        let cur_x = start.x + dx * ease;
        let cur_y = start.y + dy * ease;
        post_mouse_move(cur_x, cur_y);
        thread::sleep(step_delay);
    }
    post_mouse_move(target_x, target_y);
}

pub fn click_at(x: f64, y: f64, button: u32, clicks: usize) {
    // Glide to target
    move_to_smooth(x, y, 60.0);

    // button: 0 left, 1 right, 2 middle
    let (down_type, up_type, cg_button) = match button {
        1 => (K_CG_EVENT_RIGHT_MOUSE_DOWN, K_CG_EVENT_RIGHT_MOUSE_UP, K_CG_MOUSE_BUTTON_RIGHT),
        2 => (K_CG_EVENT_OTHER_MOUSE_DOWN, K_CG_EVENT_OTHER_MOUSE_UP, K_CG_MOUSE_BUTTON_CENTER),
        _ => (K_CG_EVENT_LEFT_MOUSE_DOWN, K_CG_EVENT_LEFT_MOUSE_UP, K_CG_MOUSE_BUTTON_LEFT),
    };

    let pt = CGPoint { x, y };

    for i in 0..clicks {
        if i > 0 {
            thread::sleep(Duration::from_millis(40));
        }
        // The click count tells apps the second click is a double-click, not two singles.
        for (kind, pause) in [(down_type, 25), (up_type, 0)] {
            unsafe {
                let ev = CGEventCreateMouseEvent(std::ptr::null_mut(), kind, pt, cg_button);
                if !ev.is_null() {
                    CGEventSetIntegerValueField(ev, K_CG_MOUSE_EVENT_CLICK_STATE, i as i64 + 1);
                    CGEventPost(K_CG_HID_EVENT_TAP, ev);
                    CFRelease(ev as *const c_void);
                }
            }
            thread::sleep(Duration::from_millis(pause));
        }
    }
}

pub fn key_press(key_code: u16, flags: u64) {
    unsafe {
        let down = CGEventCreateKeyboardEvent(std::ptr::null_mut(), key_code, true);
        if !down.is_null() {
            if flags != 0 {
                CGEventSetFlags(down, flags);
            }
            CGEventPost(K_CG_HID_EVENT_TAP, down);
            CFRelease(down as *const c_void);
        }

        thread::sleep(Duration::from_millis(15));

        let up = CGEventCreateKeyboardEvent(std::ptr::null_mut(), key_code, false);
        if !up.is_null() {
            if flags != 0 {
                CGEventSetFlags(up, flags);
            }
            CGEventPost(K_CG_HID_EVENT_TAP, up);
            CFRelease(up as *const c_void);
        }
    }
}

/// Virtual key code (US layout) and any implied modifier for a key name, or None if unknown.
/// Accepts pyautogui, Playwright and Gemini spellings ("enter", "ArrowDown", "PageDown", "F5").
/// Post one key down + up carrying `units` as its text, with no modifiers, so a held key
/// can't turn typing into a shortcut.
fn post_unicode(units: &[u16]) {
    unsafe {
        for down in [true, false] {
            let ev = CGEventCreateKeyboardEvent(std::ptr::null_mut(), 0, down);
            if !ev.is_null() {
                CGEventSetFlags(ev, 0);
                CGEventKeyboardSetUnicodeString(ev, units.len(), units.as_ptr());
                CGEventPost(K_CG_HID_EVENT_TAP, ev);
                CFRelease(ev as *const c_void);
            }
        }
    }
}

/// Type any Unicode text into the focused field, without using the clipboard.
/// Newlines are sent as Return.
pub fn type_unicode(text: &str) {
    const CHUNK: usize = 16; // UTF-16 units per event; apps drop longer strings
    for (i, line) in text.split('\n').enumerate() {
        if i > 0 {
            key_press(0x24, 0);
            thread::sleep(Duration::from_millis(8));
        }
        let mut buf: Vec<u16> = Vec::with_capacity(CHUNK + 2);
        for ch in line.chars() {
            let mut tmp = [0u16; 2];
            let units = ch.encode_utf16(&mut tmp);
            if buf.len() + units.len() > CHUNK {
                post_unicode(&buf);
                buf.clear();
                thread::sleep(Duration::from_millis(4));
            }
            buf.extend_from_slice(units); // never splits a surrogate pair across events
        }
        if !buf.is_empty() {
            post_unicode(&buf);
        }
    }
}

pub fn parse_key_code(name: &str) -> Option<(u16, u64)> {
    const SHIFT: u64 = K_CG_EVENT_FLAG_MASK_SHIFT;
    if name == " " {
        return Some((0x31, 0));
    }
    let lower = name.trim().to_lowercase().replace(['_', ' ', '-'], "");
    let key = if lower.is_empty() { name.trim() } else { lower.as_str() }; // "-" normalizes to ""
    let code = match key {
        "return" | "enter" => (0x24, 0),
        "tab" => (0x30, 0),
        "space" | "spacebar" => (0x31, 0),
        "delete" | "backspace" => (0x33, 0),
        "forwarddelete" | "del" => (0x75, 0),
        "escape" | "esc" => (0x35, 0),
        "up" | "arrowup" => (0x7E, 0),
        "down" | "arrowdown" => (0x7D, 0),
        "left" | "arrowleft" => (0x7B, 0),
        "right" | "arrowright" => (0x7C, 0),
        "home" => (0x73, 0),
        "end" => (0x77, 0),
        "pageup" | "pgup" => (0x74, 0),
        "pagedown" | "pgdn" => (0x79, 0),
        "capslock" => (0x39, 0),
        "f1" => (0x7A, 0), "f2" => (0x78, 0), "f3" => (0x63, 0), "f4" => (0x76, 0),
        "f5" => (0x60, 0), "f6" => (0x61, 0), "f7" => (0x62, 0), "f8" => (0x64, 0),
        "f9" => (0x65, 0), "f10" => (0x6D, 0), "f11" => (0x67, 0), "f12" => (0x6F, 0),
        "0" => (0x1D, 0), "1" => (0x12, 0), "2" => (0x13, 0), "3" => (0x14, 0), "4" => (0x15, 0),
        "5" => (0x17, 0), "6" => (0x16, 0), "7" => (0x1A, 0), "8" => (0x1C, 0), "9" => (0x19, 0),
        "a" => (0x00, 0), "b" => (0x0B, 0), "c" => (0x08, 0), "d" => (0x02, 0), "e" => (0x0E, 0),
        "f" => (0x03, 0), "g" => (0x05, 0), "h" => (0x04, 0), "i" => (0x22, 0), "j" => (0x26, 0),
        "k" => (0x28, 0), "l" => (0x25, 0), "m" => (0x2E, 0), "n" => (0x2D, 0), "o" => (0x1F, 0),
        "p" => (0x23, 0), "q" => (0x0C, 0), "r" => (0x0F, 0), "s" => (0x01, 0), "t" => (0x11, 0),
        "u" => (0x20, 0), "v" => (0x09, 0), "w" => (0x0D, 0), "x" => (0x07, 0), "y" => (0x10, 0),
        "z" => (0x06, 0),
        "=" | "equal" => (0x18, 0),
        "+" | "plus" => (0x18, SHIFT),
        "-" | "minus" => (0x1B, 0),
        "_" => (0x1B, SHIFT),
        "*" => (0x1C, SHIFT),
        "/" | "slash" => (0x2C, 0),
        "?" => (0x2C, SHIFT),
        "." | "period" => (0x2F, 0),
        "," | "comma" => (0x2B, 0),
        ";" | "semicolon" => (0x29, 0),
        "'" | "quote" => (0x27, 0),
        "`" | "grave" | "backquote" => (0x32, 0),
        "[" | "bracketleft" => (0x21, 0),
        "]" | "bracketright" => (0x1E, 0),
        "\\" | "backslash" => (0x2A, 0),
        _ => return None,
    };
    Some(code)
}

/// Modifier flag for a modifier key name, if it is one.
fn modifier_flag(name: &str) -> Option<u64> {
    match name.trim().to_lowercase().as_str() {
        "command" | "cmd" | "meta" | "super" | "win" => Some(K_CG_EVENT_FLAG_MASK_COMMAND),
        "shift" => Some(K_CG_EVENT_FLAG_MASK_SHIFT),
        "option" | "alt" | "opt" => Some(K_CG_EVENT_FLAG_MASK_ALTERNATE),
        "control" | "ctrl" => Some(K_CG_EVENT_FLAG_MASK_CONTROL),
        _ => None,
    }
}

/// Split a chord sent over FFI. Keys are separated by U+001F so "," can itself be a key;
/// plain commas are accepted for older callers.
pub fn split_chord(s: &str) -> Vec<&str> {
    if s.contains('\u{1f}') { s.split('\u{1f}').collect() } else { s.split(',').map(str::trim).collect() }
}

/// Resolve a chord to (key code, flags) without pressing anything. A chord of only
/// modifiers presses the last one; None if any key is unknown or there are two main keys.
pub fn resolve_chord(keys: &[&str]) -> Option<(u16, u64)> {
    let mut flags = 0u64;
    let mut main: Option<u16> = None;
    let mut last_modifier: Option<u16> = None;
    for k in keys {
        if let Some(f) = modifier_flag(k) {
            flags |= f;
            last_modifier = Some(match f {
                K_CG_EVENT_FLAG_MASK_COMMAND => 0x37,
                K_CG_EVENT_FLAG_MASK_SHIFT => 0x38,
                K_CG_EVENT_FLAG_MASK_ALTERNATE => 0x3A,
                _ => 0x3B,
            });
            continue;
        }
        let (code, extra) = parse_key_code(k)?;
        if main.is_some() {
            return None;
        }
        main = Some(code);
        flags |= extra;
    }
    main.or(last_modifier).map(|code| (code, flags))
}

/// Press a chord. Returns false and presses nothing if it can't be resolved.
pub fn hotkey(keys: &[&str]) -> bool {
    match resolve_chord(keys) {
        Some((code, flags)) => {
            key_press(code, flags);
            true
        }
        None => false,
    }
}

const K_CG_EVENT_LEFT_MOUSE_DRAGGED: u32 = 6;
const K_CG_SCROLL_EVENT_UNIT_LINE: u32 = 0;

unsafe extern "C" {
    fn CGEventCreateScrollWheelEvent2(
        source: CGEventSourceRef,
        units: u32,
        wheel_count: u32,
        wheel1: i32,
        wheel2: i32,
        wheel3: i32,
    ) -> CGEventRef;
}

pub fn scroll(delta_y: i32, delta_x: i32) {
    unsafe {
        let ev = CGEventCreateScrollWheelEvent2(
            std::ptr::null_mut(),
            K_CG_SCROLL_EVENT_UNIT_LINE,
            2,
            delta_y,
            delta_x,
            0,
        );
        if !ev.is_null() {
            CGEventPost(K_CG_HID_EVENT_TAP, ev);
            CFRelease(ev as *const c_void);
        }
    }
}

pub fn drag_to(x1: f64, y1: f64, x2: f64, y2: f64, duration_ms: f64) {
    click_at(x1, y1, 0, 0); // glide to start
    let pt_start = CGPoint { x: x1, y: y1 };
    unsafe {
        let down = CGEventCreateMouseEvent(
            std::ptr::null_mut(),
            K_CG_EVENT_LEFT_MOUSE_DOWN,
            pt_start,
            K_CG_MOUSE_BUTTON_LEFT,
        );
        if !down.is_null() {
            CGEventPost(K_CG_HID_EVENT_TAP, down);
            CFRelease(down as *const c_void);
        }
    }
    thread::sleep(Duration::from_millis(30));

    let steps = (duration_ms / 10.0).clamp(5.0, 30.0) as usize;
    let step_delay = Duration::from_secs_f64((duration_ms / 1000.0) / steps as f64);
    let dx = x2 - x1;
    let dy = y2 - y1;

    for i in 1..=steps {
        let t = i as f64 / steps as f64;
        let ease = t * (2.0 - t);
        let cur_x = x1 + dx * ease;
        let cur_y = y1 + dy * ease;
        let pt = CGPoint { x: cur_x, y: cur_y };
        unsafe {
            let ev = CGEventCreateMouseEvent(
                std::ptr::null_mut(),
                K_CG_EVENT_LEFT_MOUSE_DRAGGED,
                pt,
                K_CG_MOUSE_BUTTON_LEFT,
            );
            if !ev.is_null() {
                CGEventPost(K_CG_HID_EVENT_TAP, ev);
                CFRelease(ev as *const c_void);
            }
        }
        thread::sleep(step_delay);
    }

    let pt_end = CGPoint { x: x2, y: y2 };
    unsafe {
        let up = CGEventCreateMouseEvent(
            std::ptr::null_mut(),
            K_CG_EVENT_LEFT_MOUSE_UP,
            pt_end,
            K_CG_MOUSE_BUTTON_LEFT,
        );
        if !up.is_null() {
            CGEventPost(K_CG_HID_EVENT_TAP, up);
            CFRelease(up as *const c_void);
        }
    }
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unknown_keys_are_refused_not_turned_into_return() {
        assert_eq!(resolve_chord(&["command", "f13"]), None);
        assert_eq!(resolve_chord(&["mystery"]), None);
        assert_eq!(resolve_chord(&["a", "b"]), None);
    }

    #[test]
    fn gemini_and_playwright_key_names() {
        assert_eq!(resolve_chord(&["ArrowDown"]), Some((0x7D, 0)));
        assert_eq!(resolve_chord(&["PageDown"]), Some((0x79, 0)));
        assert_eq!(resolve_chord(&["page_up"]), Some((0x74, 0)));
        assert_eq!(resolve_chord(&["F5"]), Some((0x60, 0)));
        assert_eq!(resolve_chord(&["Meta", "c"]), Some((0x08, K_CG_EVENT_FLAG_MASK_COMMAND)));
        assert_eq!(resolve_chord(&["command", ","]), Some((0x2B, K_CG_EVENT_FLAG_MASK_COMMAND)));
        assert_eq!(resolve_chord(&["-"]), Some((0x1B, 0)));
        assert_eq!(resolve_chord(&["shift"]), Some((0x38, K_CG_EVENT_FLAG_MASK_SHIFT)));
    }

    #[test]
    fn chord_separator_allows_comma_key() {
        assert_eq!(split_chord("command\u{1f},"), vec!["command", ","]);
        assert_eq!(split_chord("command, t"), vec!["command", "t"]);
    }
}
