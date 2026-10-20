use std::ffi::c_void;
use std::thread;
use std::time::Duration;

type CGEventRef = *mut c_void;
type CGEventSourceRef = *mut c_void;

#[repr(C)]
#[derive(Copy, Clone, Debug, Default)]
pub struct CGPoint {
    pub x: f64,
    pub y: f64,
}

const K_CG_HID_EVENT_TAP: u32 = 0;

const K_CG_EVENT_LEFT_MOUSE_DOWN: u32 = 1;
const K_CG_EVENT_LEFT_MOUSE_UP: u32 = 2;
const K_CG_EVENT_RIGHT_MOUSE_DOWN: u32 = 3;
const K_CG_EVENT_RIGHT_MOUSE_UP: u32 = 4;
const K_CG_EVENT_MOUSE_MOVED: u32 = 5;

const K_CG_MOUSE_BUTTON_LEFT: u32 = 0;
const K_CG_MOUSE_BUTTON_RIGHT: u32 = 1;

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
}

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

    let (down_type, up_type, cg_button) = if button == 1 {
        (K_CG_EVENT_RIGHT_MOUSE_DOWN, K_CG_EVENT_RIGHT_MOUSE_UP, K_CG_MOUSE_BUTTON_RIGHT)
    } else {
        (K_CG_EVENT_LEFT_MOUSE_DOWN, K_CG_EVENT_LEFT_MOUSE_UP, K_CG_MOUSE_BUTTON_LEFT)
    };

    let pt = CGPoint { x, y };

    for i in 0..clicks {
        if i > 0 {
            thread::sleep(Duration::from_millis(40));
        }
        unsafe {
            let down = CGEventCreateMouseEvent(std::ptr::null_mut(), down_type, pt, cg_button);
            if !down.is_null() {
                CGEventPost(K_CG_HID_EVENT_TAP, down);
                CFRelease(down as *const c_void);
            }
            thread::sleep(Duration::from_millis(25));
            let up = CGEventCreateMouseEvent(std::ptr::null_mut(), up_type, pt, cg_button);
            if !up.is_null() {
                CGEventPost(K_CG_HID_EVENT_TAP, up);
                CFRelease(up as *const c_void);
            }
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

pub fn parse_key_code(name: &str) -> (u16, u64) {
    let lower = name.to_lowercase();
    match lower.as_str() {
        "return" | "enter" => (0x24, 0),
        "tab" => (0x30, 0),
        "space" => (0x31, 0),
        "delete" | "backspace" => (0x33, 0),
        "escape" | "esc" => (0x35, 0),
        "command" | "cmd" => (0x37, 0),
        "shift" => (0x38, 0),
        "option" | "alt" => (0x3A, 0),
        "control" | "ctrl" => (0x3B, 0),
        "up" => (0x7E, 0),
        "down" => (0x7D, 0),
        "left" => (0x7B, 0),
        "right" => (0x7C, 0),
        "0" => (0x1D, 0),
        "1" => (0x12, 0),
        "2" => (0x13, 0),
        "3" => (0x14, 0),
        "4" => (0x15, 0),
        "5" => (0x17, 0),
        "6" => (0x16, 0),
        "7" => (0x1A, 0),
        "8" => (0x1C, 0),
        "9" => (0x19, 0),
        "=" => (0x18, 0),
        "+" => (0x18, K_CG_EVENT_FLAG_MASK_SHIFT),
        "-" => (0x1B, 0),
        "*" => (0x1C, K_CG_EVENT_FLAG_MASK_SHIFT),
        "/" => (0x2C, 0),
        "." => (0x2F, 0),
        "a" => (0x00, 0),
        "b" => (0x0B, 0),
        "c" => (0x08, 0),
        "d" => (0x02, 0),
        "e" => (0x0E, 0),
        "f" => (0x03, 0),
        "g" => (0x05, 0),
        "h" => (0x04, 0),
        "i" => (0x22, 0),
        "j" => (0x26, 0),
        "k" => (0x28, 0),
        "l" => (0x25, 0),
        "m" => (0x2E, 0),
        "n" => (0x2D, 0),
        "o" => (0x1F, 0),
        "p" => (0x23, 0),
        "q" => (0x0C, 0),
        "r" => (0x0F, 0),
        "s" => (0x01, 0),
        "t" => (0x11, 0),
        "u" => (0x20, 0),
        "v" => (0x09, 0),
        "w" => (0x0D, 0),
        "x" => (0x07, 0),
        "y" => (0x10, 0),
        "z" => (0x06, 0),
        _ => (0x24, 0), // fallback return
    }
}

pub fn hotkey(keys: &[&str]) {
    let mut flags = 0u64;
    let mut main_key = 0x24u16;

    for k in keys {
        let low = k.to_lowercase();
        match low.as_str() {
            "command" | "cmd" => flags |= K_CG_EVENT_FLAG_MASK_COMMAND,
            "shift" => flags |= K_CG_EVENT_FLAG_MASK_SHIFT,
            "option" | "alt" => flags |= K_CG_EVENT_FLAG_MASK_ALTERNATE,
            "control" | "ctrl" => flags |= K_CG_EVENT_FLAG_MASK_CONTROL,
            _ => {
                let (code, extra_flags) = parse_key_code(k);
                main_key = code;
                flags |= extra_flags;
            }
        }
    }

    key_press(main_key, flags);
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

