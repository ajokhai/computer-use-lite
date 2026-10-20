use std::ffi::{c_void, CStr};
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
    fn AXUIElementPerformAction(element: AXUIElementRef, action: CFStringRef) -> AXError;
    fn AXValueGetValue(value: AXValueRef, the_type: u32, value_ptr: *mut c_void) -> bool;

    fn CFStringCreateWithCString(
        alloc: CFTypeRef,
        c_str: *const c_char,
        encoding: u32,
    ) -> CFStringRef;
    fn CFStringGetLength(the_string: CFStringRef) -> isize;
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
    fn CFBooleanGetValue(boolean: CFTypeRef) -> bool;
    fn CFRetain(cf: CFTypeRef) -> CFTypeRef;
    fn CFRelease(cf: CFTypeRef);

    // AppKit / NSWorkspace
    fn objc_getClass(name: *const c_char) -> *mut c_void;
    fn sel_registerName(name: *const c_char) -> *mut c_void;
    fn objc_msgSend(receiver: *mut c_void, op: *mut c_void, ...) -> *mut c_void;
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
        let k_focused = make_cf_string("AXFocused");
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
                // Push in reverse so first child is processed first
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
        CFRelease(k_focused);
        CFRelease(k_focused_ui);

        AxTreeResult {
            app: app_name,
            duration_ms: t0.elapsed().as_secs_f64() * 1000.0,
            elements,
        }
    }
}

fn main() {
    println!(">>> Rust cu-native starting...");
    let (pid, app) = get_frontmost_app();
    println!(">>> Frontmost app: pid={}, name={}", pid, app);
    let res = walk_ax_tree(250);
    println!(
        "Rust AX Walker: App='{}', found {} elements in {:.2} ms!",
        res.app,
        res.elements.len(),
        res.duration_ms
    );
    for (i, el) in res.elements.iter().take(10).enumerate() {
        println!("  [{}] {} \"{}\" at ({}, {}) [{}x{}]", i, el.role, el.label, el.x, el.y, el.w, el.h);
    }

    if std::env::args().any(|a| a == "--json") {
        let json = serde_json::to_string(&res).unwrap();
        println!("{}", json);
    }
}
