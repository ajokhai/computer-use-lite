pub mod input;
pub mod capture;
pub mod memory;

use std::ffi::{c_void, CStr, CString};
use std::os::raw::c_char;
use std::time::Instant;
use serde::{Deserialize, Serialize};

#[repr(C)]
#[derive(Copy, Clone, Debug, Default, Serialize, Deserialize)]
pub struct CGPoint {
    pub x: f64,
    pub y: f64,
}

#[repr(C)]
#[derive(Copy, Clone, Debug, Default, Serialize, Deserialize)]
pub struct CGSize {
    pub width: f64,
    pub height: f64,
}

#[repr(C)]
#[derive(Clone, Debug, Serialize, Deserialize)]
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

#[repr(C)]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AxTreeResult {
    pub app: String,
    pub duration_ms: f64,
    pub elements: Vec<NativeElement>,
}

type CFTypeRef = *const c_void;
type CFStringRef = *const c_void;
type CFArrayRef = *const c_void;
type AXUIElementRef = *const c_void;
type AXValueRef = *const c_void;
type AXError = i32;

const K_AX_VALUE_CGPOINT: u32 = 1;
const K_AX_VALUE_CGSIZE: u32 = 2;

#[link(name = "ApplicationServices", kind = "framework")]
#[link(name = "CoreFoundation", kind = "framework")]
#[link(name = "AppKit", kind = "framework")]
unsafe extern "C" {
    fn AXUIElementCreateApplication(pid: i32) -> AXUIElementRef;
    fn AXUIElementCopyAttributeValue(
        element: AXUIElementRef,
        attribute: CFStringRef,
        value: *mut CFTypeRef,
    ) -> AXError;
    fn AXValueGetValue(value: AXValueRef, the_type: u32, value_ptr: *mut c_void) -> bool;

    fn CFStringCreateWithCString(
        alloc: CFTypeRef,
        c_str: *const c_char,
        encoding: u32,
    ) -> CFStringRef;
    fn CFStringGetCString(
        the_string: CFStringRef,
        buffer: *mut c_char,
        buffer_size: isize,
        encoding: u32,
    ) -> bool;
    fn CFArrayGetCount(the_array: CFArrayRef) -> isize;
    fn CFArrayGetValueAtIndex(the_array: CFArrayRef, idx: isize) -> CFTypeRef;
    fn CFGetTypeID(cf: CFTypeRef) -> usize;
    fn CFStringGetTypeID() -> usize;
    fn CFRetain(cf: CFTypeRef) -> CFTypeRef;
    fn CFRelease(cf: CFTypeRef);
}

const K_CF_STRING_ENCODING_UTF8: u32 = 0x08000100;

unsafe fn make_cf_string(s: &str) -> CFStringRef {
    let c = std::ffi::CString::new(s).unwrap();
    CFStringCreateWithCString(std::ptr::null(), c.as_ptr(), K_CF_STRING_ENCODING_UTF8)
}

unsafe fn cf_to_string(cf: CFStringRef) -> String {
    if cf.is_null() {
        return String::new();
    }
    let mut buf = [0 as c_char; 1024];
    if CFStringGetCString(cf, buf.as_mut_ptr(), buf.len() as isize, K_CF_STRING_ENCODING_UTF8) {
        CStr::from_ptr(buf.as_ptr()).to_string_lossy().into_owned()
    } else {
        String::new()
    }
}

unsafe fn copy_attr(el: AXUIElementRef, attr: CFStringRef) -> Option<CFTypeRef> {
    let mut val: CFTypeRef = std::ptr::null();
    let err = AXUIElementCopyAttributeValue(el, attr, &mut val);
    if err == 0 && !val.is_null() {
        Some(val)
    } else {
        None
    }
}

#[repr(C)]
#[derive(Default, Copy, Clone)]
struct ProcessSerialNumber {
    high_long_of_psn: u32,
    low_long_of_psn: u32,
}

unsafe extern "C" {
    fn GetFrontProcess(psn: *mut ProcessSerialNumber) -> i32;
    fn GetProcessPID(psn: *const ProcessSerialNumber, pid: *mut i32) -> i32;
    fn CopyProcessName(psn: *const ProcessSerialNumber, name: *mut CFStringRef) -> i32;
}

pub fn get_frontmost_app() -> (i32, String) {
    unsafe {
        let mut psn = ProcessSerialNumber::default();
        if GetFrontProcess(&mut psn) != 0 {
            return (0, String::from("Unknown"));
        }
        let mut pid: i32 = 0;
        if GetProcessPID(&psn, &mut pid) != 0 {
            return (0, String::from("Unknown"));
        }
        let mut name_cf: CFStringRef = std::ptr::null();
        let name = if CopyProcessName(&psn, &mut name_cf) == 0 && !name_cf.is_null() {
            let s = cf_to_string(name_cf);
            CFRelease(name_cf);
            s
        } else {
            String::from("App")
        };
        (pid, name)
    }
}

pub fn walk_ax_tree(max_elements: usize) -> AxTreeResult {
    let t0 = Instant::now();
    let (pid, app_name) = get_frontmost_app();
    if pid <= 0 {
        return AxTreeResult {
            app: app_name,
            duration_ms: t0.elapsed().as_secs_f64() * 1000.0,
            elements: Vec::new(),
        };
    }

    unsafe {
        let root = AXUIElementCreateApplication(pid);
        if root.is_null() {
            return AxTreeResult {
                app: app_name,
                duration_ms: t0.elapsed().as_secs_f64() * 1000.0,
                elements: Vec::new(),
            };
        }

        let k_children = make_cf_string("AXChildren");
        let k_role = make_cf_string("AXRole");
        let k_title = make_cf_string("AXTitle");
        let k_desc = make_cf_string("AXDescription");
        let k_val = make_cf_string("AXValue");
        let k_pos = make_cf_string("AXPosition");
        let k_size = make_cf_string("AXSize");
        let k_focused_ui = make_cf_string("AXFocusedUIElement");

        let focused_el = copy_attr(root, k_focused_ui);

        let mut elements = Vec::with_capacity(max_elements);
        let mut stack = vec![(root, 0usize)];

        while let Some((el, depth)) = stack.pop() {
            if elements.len() >= max_elements || depth > 24 {
                if el != root {
                    CFRelease(el);
                }
                continue;
            }

            let role_val = copy_attr(el, k_role);
            let role = role_val.map(|r| {
                let s = cf_to_string(r as CFStringRef);
                CFRelease(r);
                s
            }).unwrap_or_default();

            // Extract label
            let mut label = String::new();
            for attr in [k_title, k_desc, k_val] {
                if let Some(v) = copy_attr(el, attr) {
                    if CFGetTypeID(v) == CFStringGetTypeID() {
                        let s = cf_to_string(v as CFStringRef);
                        if !s.trim().is_empty() {
                            label = s.trim().to_string();
                            CFRelease(v);
                            break;
                        }
                    }
                    CFRelease(v);
                }
            }

            // Extract pos & size
            let mut pt = CGPoint::default();
            if let Some(pos_val) = copy_attr(el, k_pos) {
                AXValueGetValue(pos_val, K_AX_VALUE_CGPOINT, &mut pt as *mut _ as *mut c_void);
                CFRelease(pos_val);
            }

            let mut sz = CGSize::default();
            if let Some(size_val) = copy_attr(el, k_size) {
                AXValueGetValue(size_val, K_AX_VALUE_CGSIZE, &mut sz as *mut _ as *mut c_void);
                CFRelease(size_val);
            }

            // Extract focused
            let is_focused = if let Some(f_el) = focused_el {
                f_el == el
            } else {
                false
            };

            // Keep visible interactable elements
            let has_content = !label.is_empty() || matches!(role.as_str(), "AXButton" | "AXTextField" | "AXTextArea" | "AXCheckBox" | "AXRadioButton" | "AXPopUpButton" | "AXTab" | "AXMenuItem");
            if el != root && has_content && (sz.width > 2.0 && sz.height > 2.0) {
                let id = format!("e{}", elements.len());
                elements.push(NativeElement {
                    id,
                    role: role.clone(),
                    label,
                    x: (pt.x + sz.width / 2.0) as i32,
                    y: (pt.y + sz.height / 2.0) as i32,
                    w: sz.width as i32,
                    h: sz.height as i32,
                    focused: is_focused,
                });
            }

            // Collect children
            if let Some(children_val) = copy_attr(el, k_children) {
                let count = CFArrayGetCount(children_val as CFArrayRef);
                let prune_limit = if matches!(role.as_str(), "AXOutline" | "AXTable" | "AXList") && count > 12 {
                    12
                } else {
                    count as usize
                };

                let mut to_add = Vec::with_capacity(prune_limit);
                for i in 0..prune_limit as isize {
                    let child = CFArrayGetValueAtIndex(children_val as CFArrayRef, i);
                    if !child.is_null() {
                        CFRetain(child);
                        to_add.push((child, depth + 1));
                    }
                }
                for item in to_add.into_iter().rev() {
                    stack.push(item);
                }
                CFRelease(children_val);
            }

            if el != root {
                CFRelease(el);
            }
        }

        if let Some(f) = focused_el {
            CFRelease(f);
        }
        CFRelease(root);

        CFRelease(k_children);
        CFRelease(k_role);
        CFRelease(k_title);
        CFRelease(k_desc);
        CFRelease(k_val);
        CFRelease(k_pos);
        CFRelease(k_size);
        CFRelease(k_focused_ui);

        AxTreeResult {
            app: app_name,
            duration_ms: t0.elapsed().as_secs_f64() * 1000.0,
            elements,
        }
    }
}

#[no_mangle]
pub extern "C" fn cu_walk_ax_tree_json(max_elements: usize) -> *mut c_char {
    let res = walk_ax_tree(max_elements);
    let json = serde_json::to_string(&res).unwrap_or_else(|_| "{}".to_string());
    CString::new(json).unwrap().into_raw()
}

#[no_mangle]
pub extern "C" fn cu_free_string(s: *mut c_char) {
    if !s.is_null() {
        unsafe {
            let _ = CString::from_raw(s);
        }
    }
}

#[no_mangle]
pub extern "C" fn cu_click(x: f64, y: f64, button: u32, clicks: usize) {
    input::click_at(x, y, button, clicks);
}

#[no_mangle]
pub extern "C" fn cu_move_to(x: f64, y: f64, duration_ms: f64) {
    input::move_to_smooth(x, y, duration_ms);
}

#[no_mangle]
pub extern "C" fn cu_mouse_pos(out_x: *mut f64, out_y: *mut f64) {
    let p = input::get_mouse_location();
    if !out_x.is_null() {
        unsafe {
            *out_x = p.x;
        }
    }
    if !out_y.is_null() {
        unsafe {
            *out_y = p.y;
        }
    }
}

#[no_mangle]
pub extern "C" fn cu_key_press(key_code: u16, flags: u64) {
    input::key_press(key_code, flags);
}

#[no_mangle]
pub extern "C" fn cu_hotkey(keys_csv: *const c_char) {
    if keys_csv.is_null() {
        return;
    }
    let c_str = unsafe { CStr::from_ptr(keys_csv) };
    if let Ok(s) = c_str.to_str() {
        let keys: Vec<&str> = s.split(',').map(|k| k.trim()).collect();
        input::hotkey(&keys);
    }
}

#[no_mangle]
pub extern "C" fn cu_scroll(delta_y: i32, delta_x: i32) {
    input::scroll(delta_y, delta_x);
}

#[no_mangle]
pub extern "C" fn cu_drag(x1: f64, y1: f64, x2: f64, y2: f64, duration_ms: f64) {
    input::drag_to(x1, y1, x2, y2, duration_ms);
}

#[no_mangle]
pub extern "C" fn cu_capture_to_file(x: f64, y: f64, w: f64, h: f64, out_path: *const c_char) -> bool {
    if out_path.is_null() {
        return false;
    }
    let c_str = unsafe { CStr::from_ptr(out_path) };
    if let Ok(path) = c_str.to_str() {
        capture::capture_rect_to_png(x, y, w, h, path)
    } else {
        false
    }
}

