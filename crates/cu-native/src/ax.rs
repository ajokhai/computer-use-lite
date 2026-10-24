//! Accessibility (AX) and window-server access: the frontmost app's element tree, menus,
//! element text, and the on-screen window list. Every CF object is held in a [`CfRef`],
//! which releases it on drop, so nothing here can leak.

use std::ffi::{c_void, CStr, CString};
use std::os::raw::c_char;
use std::time::Instant;

use crate::{AxTreeResult, CGPoint, CGRect, CGSize, NativeElement};

pub type CFTypeRef = *const c_void;
type AXError = i32;

const K_AX_VALUE_CGPOINT: u32 = 1;
const K_AX_VALUE_CGSIZE: u32 = 2;
const K_CF_STRING_ENCODING_UTF8: u32 = 0x08000100;
const K_CF_NUMBER_SINT64: i32 = 4;

#[link(name = "ApplicationServices", kind = "framework")]
#[link(name = "CoreFoundation", kind = "framework")]
#[link(name = "CoreGraphics", kind = "framework")]
unsafe extern "C" {
    fn AXUIElementCreateApplication(pid: i32) -> CFTypeRef;
    fn AXUIElementCopyAttributeValue(element: CFTypeRef, attribute: CFTypeRef, value: *mut CFTypeRef) -> AXError;
    fn AXUIElementPerformAction(element: CFTypeRef, action: CFTypeRef) -> AXError;
    fn AXUIElementSetAttributeValue(element: CFTypeRef, attribute: CFTypeRef, value: CFTypeRef) -> AXError;
    static kCFBooleanTrue: CFTypeRef;
    static kCFBooleanFalse: CFTypeRef;
    fn AXValueGetValue(value: CFTypeRef, the_type: u32, value_ptr: *mut c_void) -> bool;
    fn AXIsProcessTrusted() -> bool;

    fn CFStringCreateWithCString(alloc: CFTypeRef, c_str: *const c_char, encoding: u32) -> CFTypeRef;
    fn CFStringGetCString(s: CFTypeRef, buffer: *mut c_char, size: isize, encoding: u32) -> bool;
    fn CFStringGetLength(s: CFTypeRef) -> isize;
    fn CFStringGetMaximumSizeForEncoding(length: isize, encoding: u32) -> isize;
    fn CFArrayGetCount(a: CFTypeRef) -> isize;
    fn CFArrayGetValueAtIndex(a: CFTypeRef, idx: isize) -> CFTypeRef;
    fn CFDictionaryGetValue(d: CFTypeRef, key: CFTypeRef) -> CFTypeRef;
    fn CFNumberGetValue(n: CFTypeRef, the_type: i32, out: *mut c_void) -> bool;
    fn CFBooleanGetValue(b: CFTypeRef) -> bool;
    fn CFGetTypeID(cf: CFTypeRef) -> usize;
    fn CFStringGetTypeID() -> usize;
    fn CFBooleanGetTypeID() -> usize;
    fn CFRetain(cf: CFTypeRef) -> CFTypeRef;
    fn CFRelease(cf: CFTypeRef);
    fn CFEqual(a: CFTypeRef, b: CFTypeRef) -> bool;

    fn CGGetActiveDisplayList(max: u32, displays: *mut u32, count: *mut u32) -> i32;
    fn CGDisplayBounds(display: u32) -> CGRect;
    fn CGWindowListCopyWindowInfo(option: u32, relative_to: u32) -> CFTypeRef;
    fn CGRectMakeWithDictionaryRepresentation(dict: CFTypeRef, rect: *mut CGRect) -> bool;
    static kCGWindowOwnerName: CFTypeRef;
    static kCGWindowOwnerPID: CFTypeRef;
    static kCGWindowName: CFTypeRef;
    static kCGWindowBounds: CFTypeRef;
    static kCGWindowLayer: CFTypeRef;
}

#[repr(C)]
#[derive(Default, Copy, Clone)]
struct ProcessSerialNumber {
    high: u32,
    low: u32,
}

#[link(name = "ApplicationServices", kind = "framework")]
unsafe extern "C" {
    fn GetFrontProcess(psn: *mut ProcessSerialNumber) -> i32;
    fn GetProcessPID(psn: *const ProcessSerialNumber, pid: *mut i32) -> i32;
    fn CopyProcessName(psn: *const ProcessSerialNumber, name: *mut CFTypeRef) -> i32;
}

/// An owned (+1) Core Foundation reference, released on drop.
pub struct CfRef(CFTypeRef);

// AX elements and CF strings are immutable handles that may be used from any thread.
unsafe impl Send for CfRef {}

impl CfRef {
    /// Take ownership of a reference returned by a Create/Copy function. None if null.
    fn owned(p: CFTypeRef) -> Option<Self> {
        (!p.is_null()).then_some(CfRef(p))
    }

    /// Retain a borrowed (Get-rule) reference.
    fn retained(p: CFTypeRef) -> Option<Self> {
        (!p.is_null()).then(|| CfRef(unsafe { CFRetain(p) }))
    }

    fn ptr(&self) -> CFTypeRef {
        self.0
    }
}

impl Clone for CfRef {
    fn clone(&self) -> Self {
        CfRef(unsafe { CFRetain(self.0) })
    }
}

impl Drop for CfRef {
    fn drop(&mut self) {
        unsafe { CFRelease(self.0) }
    }
}

fn cf_string(s: &str) -> CfRef {
    let c = CString::new(s).unwrap_or_default();
    CfRef::owned(unsafe { CFStringCreateWithCString(std::ptr::null(), c.as_ptr(), K_CF_STRING_ENCODING_UTF8) })
        .expect("CFStringCreateWithCString")
}

/// Rust string from a CFString pointer; empty if it isn't one.
fn string_of(p: CFTypeRef) -> String {
    unsafe {
        if p.is_null() || CFGetTypeID(p) != CFStringGetTypeID() {
            return String::new();
        }
        let size = CFStringGetMaximumSizeForEncoding(CFStringGetLength(p), K_CF_STRING_ENCODING_UTF8) + 1;
        let mut buf = vec![0 as c_char; size.max(1) as usize];
        if CFStringGetCString(p, buf.as_mut_ptr(), buf.len() as isize, K_CF_STRING_ENCODING_UTF8) {
            CStr::from_ptr(buf.as_ptr()).to_string_lossy().into_owned()
        } else {
            String::new()
        }
    }
}

fn copy_attr(el: &CfRef, attr: &CfRef) -> Option<CfRef> {
    let mut val: CFTypeRef = std::ptr::null();
    let err = unsafe { AXUIElementCopyAttributeValue(el.ptr(), attr.ptr(), &mut val) };
    if err == 0 { CfRef::owned(val) } else { None }
}

fn children(el: &CfRef, k_children: &CfRef) -> Vec<CfRef> {
    let Some(arr) = copy_attr(el, k_children) else { return Vec::new() };
    let n = unsafe { CFArrayGetCount(arr.ptr()) }.max(0);
    (0..n).filter_map(|i| CfRef::retained(unsafe { CFArrayGetValueAtIndex(arr.ptr(), i) })).collect()
}

pub fn ax_trusted() -> bool {
    unsafe { AXIsProcessTrusted() }
}

pub fn get_frontmost_app() -> (i32, String) {
    unsafe {
        let mut psn = ProcessSerialNumber::default();
        let mut pid = 0;
        if GetFrontProcess(&mut psn) != 0 || GetProcessPID(&psn, &mut pid) != 0 {
            return (0, "Unknown".into());
        }
        let mut name: CFTypeRef = std::ptr::null();
        let name = if CopyProcessName(&psn, &mut name) == 0 {
            CfRef::owned(name).map(|n| string_of(n.ptr())).unwrap_or_default()
        } else {
            String::new()
        };
        (pid, if name.is_empty() { "App".into() } else { name })
    }
}

/// Bounds of every active display in global coordinates (top-left origin, same space as AX).
pub fn display_bounds() -> Vec<CGRect> {
    let mut ids = [0u32; 16];
    let mut count = 0u32;
    unsafe {
        if CGGetActiveDisplayList(16, ids.as_mut_ptr(), &mut count) != 0 {
            return Vec::new();
        }
        ids[..count as usize].iter().map(|&d| CGDisplayBounds(d)).collect()
    }
}

pub fn on_screen(x: f64, y: f64, displays: &[CGRect]) -> bool {
    displays.is_empty()
        || displays.iter().any(|r| {
            x >= r.origin.x && x < r.origin.x + r.size.width && y >= r.origin.y && y < r.origin.y + r.size.height
        })
}

/// Roles worth acting on; everything else is walked through, not listed.
pub const ACTIONABLE: &[&str] = &[
    "AXButton", "AXTextField", "AXTextArea", "AXSearchField", "AXComboBox",
    "AXCheckBox", "AXRadioButton", "AXPopUpButton", "AXMenuButton", "AXLink",
    "AXMenuItem", "AXMenuBarItem", "AXTab", "AXSlider", "AXCell", "AXRow",
    "AXDisclosureTriangle", "AXIncrementor", "AXImage", "AXStaticText",
];
pub const MAX_LABEL_CHARS: usize = 80;
const WALK_TIMEOUT_MS: u128 = 2500;
/// Text inputs are kept even past the budget (a page's main box can sit below a long feed),
/// up to this many extra, replacing plain text at the end of the list.
const TEXT_INPUTS: &[&str] = &["AXTextField", "AXTextArea", "AXSearchField", "AXComboBox"];
const MAX_EXTRA_INPUTS: usize = 8;
/// Chrome around the content: walked after everything else, so a browser's tabs, bookmarks and
/// toolbar can't use up the element budget before the page itself is reached.
const DEFERRED: &[&str] = &["AXToolbar", "AXTabGroup", "AXMenuBar"];
/// Web page regions around the main content (ARIA landmarks), walked after it for the same reason:
/// on a site like X the navigation and sidebar would otherwise fill the budget before the timeline.
const DEFERRED_SUBROLES: &[&str] = &["AXLandmarkNavigation", "AXLandmarkComplementary", "AXLandmarkBanner", "AXLandmarkContentInfo"];
/// Chromium browsers build their page accessibility only for assistive apps that ask
/// (AXEnhancedUserInterface); Electron apps answer AXManualAccessibility instead.
const CHROMIUM: &[&str] = &["Google Chrome", "Chromium", "Microsoft Edge", "Brave Browser", "Arc", "Vivaldi", "Opera", "Dia", "Comet"];
const LABEL_ATTRS: [&str; 5] = ["AXTitle", "AXDescription", "AXValue", "AXPlaceholderValue", "AXHelp"];

/// Labels for the window buttons, which have no title (their help text is no use as a label).
fn window_button_label(subrole: &str) -> Option<&'static str> {
    Some(match subrole {
        "AXCloseButton" => "Close window",
        "AXMinimizeButton" => "Minimize window",
        "AXZoomButton" => "Zoom window",
        "AXFullScreenButton" => "Full screen",
        _ => return None,
    })
}

/// Electron and Chromium apps (VS Code, Slack, Discord, Chrome...) only build their accessibility
/// tree once a client asks for it. Ask once per process; other apps just ignore the attributes.
fn enable_app_accessibility(pid: i32, app: &str, root: &CfRef) {
    use std::sync::Mutex;
    static ASKED: Mutex<Vec<i32>> = Mutex::new(Vec::new());
    let mut asked = ASKED.lock().unwrap_or_else(|e| e.into_inner());
    if asked.contains(&pid) {
        return;
    }
    if asked.len() >= 256 {
        asked.clear(); // bounded; asking again is harmless
    }
    asked.push(pid);
    unsafe {
        AXUIElementSetAttributeValue(root.ptr(), cf_string("AXManualAccessibility").ptr(), kCFBooleanTrue);
        // Only for browsers: some other apps animate or reposition windows differently with it on.
        if CHROMIUM.contains(&app) {
            AXUIElementSetAttributeValue(root.ptr(), cf_string("AXEnhancedUserInterface").ptr(), kCFBooleanTrue);
        }
    }
}

/// The result of a walk, plus (optionally) a live reference to each element and to the window
/// it's in (None for the menu bar), index-aligned with `result.elements`, so later calls can
/// read or press them and bring their window forward first.
pub struct Snapshot {
    pub result: AxTreeResult,
    pub refs: Vec<CfRef>,
    pub windows: Vec<Option<CfRef>>,
    pub pid: i32,
}

fn label_of(el: &CfRef, keys: &[CfRef]) -> String {
    for k in keys {
        if let Some(v) = copy_attr(el, k) {
            let s = string_of(v.ptr());
            if !s.trim().is_empty() {
                return s;
            }
        }
    }
    String::new()
}

fn geometry(el: &CfRef, k_pos: &CfRef, k_size: &CfRef) -> Option<(CGPoint, CGSize)> {
    let mut pt = CGPoint::default();
    let mut sz = CGSize::default();
    let pos = copy_attr(el, k_pos)?;
    let size = copy_attr(el, k_size)?;
    unsafe {
        let ok = AXValueGetValue(pos.ptr(), K_AX_VALUE_CGPOINT, &mut pt as *mut _ as *mut c_void)
            && AXValueGetValue(size.ptr(), K_AX_VALUE_CGSIZE, &mut sz as *mut _ as *mut c_void);
        ok.then_some((pt, sz))
    }
}

/// Walk the frontmost app's windows, then its menu bar, and list on-screen actionable
/// elements (at most `max_elements`, within 2.5 s). A Chromium browser that shows no page yet
/// has just been asked to build it, so look once more after a moment.
pub fn walk(max_elements: usize, keep_refs: bool) -> Snapshot {
    walk_app(None, max_elements, keep_refs)
}

/// Like [`walk`], for any running app (pid, name), in front or not. Reading doesn't bring it forward.
pub fn walk_app(target: Option<(i32, String)>, max_elements: usize, keep_refs: bool) -> Snapshot {
    let (snap, saw_page) = walk_once(target.clone(), max_elements, keep_refs);
    if saw_page || !CHROMIUM.contains(&snap.result.app.as_str()) || snap.result.elements.len() >= max_elements {
        return snap;
    }
    std::thread::sleep(std::time::Duration::from_millis(500));
    walk_once(target, max_elements, keep_refs).0
}

fn walk_once(target: Option<(i32, String)>, max_elements: usize, keep_refs: bool) -> (Snapshot, bool) {
    let t0 = Instant::now();
    let (pid, app) = target.unwrap_or_else(get_frontmost_app);
    let empty = |app: String| Snapshot {
        result: AxTreeResult { app, duration_ms: t0.elapsed().as_secs_f64() * 1000.0, elements: Vec::new() },
        refs: Vec::new(),
        windows: Vec::new(),
        pid,
    };
    if pid <= 0 {
        return (empty(app), false);
    }
    let Some(root) = CfRef::owned(unsafe { AXUIElementCreateApplication(pid) }) else { return (empty(app), false) };
    enable_app_accessibility(pid, &app, &root);
    let mut saw_page = false;
    let k_subrole = cf_string("AXSubrole");

    let k_children = cf_string("AXChildren");
    let k_role = cf_string("AXRole");
    let k_pos = cf_string("AXPosition");
    let k_size = cf_string("AXSize");
    let label_keys: Vec<CfRef> = LABEL_ATTRS.iter().map(|k| cf_string(k)).collect();
    let displays = display_bounds();
    let focused = copy_attr(&root, &cf_string("AXFocusedUIElement"));

    // Roots in priority order: windows first so the menu bar can't starve them.
    let mut roots: Vec<CfRef> = Vec::new();
    if let Some(wins) = copy_attr(&root, &cf_string("AXWindows")) {
        let n = unsafe { CFArrayGetCount(wins.ptr()) }.max(0);
        roots.extend((0..n).filter_map(|i| CfRef::retained(unsafe { CFArrayGetValueAtIndex(wins.ptr(), i) })));
    }
    if roots.is_empty() {
        for k in ["AXMainWindow", "AXFocusedWindow"] {
            if let Some(w) = copy_attr(&root, &cf_string(k)) {
                if !roots.iter().any(|r| unsafe { CFEqual(r.ptr(), w.ptr()) }) {
                    roots.push(w);
                }
            }
        }
    }
    // Each element's window, by index into `roots`; the menu bar has none.
    let win_roots: Vec<Option<CfRef>> = roots.iter().map(|r| Some(r.clone())).chain([None]).collect();
    roots.extend(copy_attr(&root, &cf_string("AXMenuBar")));

    let mut elements = Vec::with_capacity(max_elements.min(512));
    let mut refs = Vec::new();
    let mut windows = Vec::new();
    let mut seen = std::collections::HashSet::new();
    // (element, depth, already deferred once, index of its root)
    let mut stack: Vec<(CfRef, usize, bool, usize)> = roots.into_iter().enumerate().rev().map(|(i, r)| (r, 0, false, i)).collect();
    let mut later: Vec<(CfRef, usize, bool, usize)> = Vec::new();
    let mut extra_inputs = 0usize;

    loop {
        let Some((el, depth, deferred, win)) = stack.pop() else {
            if later.is_empty() {
                break;
            }
            // Content is done: now the toolbars, tab strips and menu bar, in the order met.
            stack = later.drain(..).rev().collect();
            continue;
        };
        // Past the budget, keep looking only for text inputs (bounded by the timeout and MAX_EXTRA_INPUTS).
        let full = elements.len() >= max_elements;
        if (full && extra_inputs >= MAX_EXTRA_INPUTS) || depth > 40 || t0.elapsed().as_millis() > WALK_TIMEOUT_MS {
            continue; // `el` drops here; draining the stacks releases everything else
        }
        let role = copy_attr(&el, &k_role).map(|r| string_of(r.ptr())).unwrap_or_default();
        let wanted = !full || TEXT_INPUTS.contains(&role.as_str());
        saw_page |= role == "AXWebArea";
        let landmark = || {
            role == "AXGroup"
                && copy_attr(&el, &k_subrole).is_some_and(|r| DEFERRED_SUBROLES.contains(&string_of(r.ptr()).as_str()))
        };
        if !deferred && (DEFERRED.contains(&role.as_str()) || landmark()) {
            later.push((el, depth, true, win));
            continue;
        }

        if wanted && ACTIONABLE.contains(&role.as_str()) {
            let subrole = if role == "AXButton" { copy_attr(&el, &k_subrole).map(|r| string_of(r.ptr())).unwrap_or_default() } else { String::new() };
            let raw = match window_button_label(&subrole) {
                Some(l) => l.to_string(),
                None => label_of(&el, &label_keys),
            };
            let label = crate::decision::truncate_chars(&raw.split_whitespace().collect::<Vec<_>>().join(" "), MAX_LABEL_CHARS);
            // Static text and images are only useful when they say something.
            let useful = !matches!(role.as_str(), "AXStaticText" | "AXImage") || !label.is_empty();
            if let (true, Some((pt, sz))) = (useful, geometry(&el, &k_pos, &k_size)) {
                let (cx, cy) = (pt.x + sz.width / 2.0, pt.y + sz.height / 2.0);
                if sz.width > 2.0
                    && sz.height > 2.0
                    && on_screen(cx, cy, &displays)
                    && seen.insert((role.clone(), label.clone(), cx as i32, cy as i32))
                {
                    elements.push(NativeElement {
                        id: String::new(), // numbered once the list is final
                        role: role.clone(),
                        label,
                        x: cx as i32,
                        y: cy as i32,
                        w: sz.width as i32,
                        h: sz.height as i32,
                        focused: focused.as_ref().is_some_and(|f| unsafe { CFEqual(f.ptr(), el.ptr()) }),
                        win: if win_roots.get(win).is_some_and(Option::is_some) { win as i32 } else { -1 },
                    });
                    if keep_refs {
                        refs.push(el.clone());
                        windows.push(win_roots.get(win).cloned().flatten());
                    }
                    if full {
                        extra_inputs += 1;
                    }
                }
            }
        }

        let mut kids = children(&el, &k_children);
        // Prune long lists/tables so sidebar lists don't starve the main pane.
        if matches!(role.as_str(), "AXOutline" | "AXTable" | "AXList") {
            kids.truncate(12);
        }
        stack.extend(kids.into_iter().rev().map(|c| (c, depth + 1, deferred, win)));
    }

    // Make room for inputs found past the budget: drop plain text and images from the end first,
    // then any non-input, so the list stays within max_elements.
    for plain_only in [true, false] {
        let mut i = elements.len();
        while elements.len() > max_elements && i > 0 {
            i -= 1;
            let role = elements[i].role.as_str();
            let droppable = !TEXT_INPUTS.contains(&role) && (!plain_only || matches!(role, "AXStaticText" | "AXImage"));
            if droppable {
                elements.remove(i);
                if keep_refs {
                    refs.remove(i);
                    windows.remove(i);
                }
            }
        }
    }
    for (i, e) in elements.iter_mut().enumerate() {
        e.id = format!("e{i}");
    }

    let snap = Snapshot {
        result: AxTreeResult { app, duration_ms: t0.elapsed().as_secs_f64() * 1000.0, elements },
        refs,
        windows,
        pid,
    };
    (snap, saw_page)
}

/// A running app with a window on screen, by name: an exact match (ignoring case) first, then
/// the first app whose name contains it. Front to back, so the nearer of two matches wins.
pub fn find_app(name: &str) -> Option<(i32, String)> {
    let want = name.trim().to_lowercase();
    let wins = windows();
    wins.iter()
        .find(|w| w.app.to_lowercase() == want)
        .or_else(|| wins.iter().find(|w| w.app.to_lowercase().contains(&want)))
        .map(|w| (w.pid as i32, w.app.clone()))
}

/// Whether an element's window has to come forward before it can be clicked or typed into:
/// its app isn't frontmost, or the window isn't the app's front window, or it's minimized.
pub fn needs_raise(pid: i32, window: Option<&CfRef>) -> bool {
    if get_frontmost_app().0 != pid {
        return true;
    }
    let Some(window) = window else { return false };
    if bool_attr(window, &cf_string("AXMinimized")) == Some(true) {
        return true;
    }
    let Some(app) = CfRef::owned(unsafe { AXUIElementCreateApplication(pid) }) else { return false };
    let front = copy_attr(&app, &cf_string("AXFocusedWindow")).or_else(|| copy_attr(&app, &cf_string("AXMainWindow")));
    !front.is_some_and(|f| unsafe { CFEqual(f.ptr(), window.ptr()) })
}

/// Bring a window (or just its app) to the front: un-minimize, raise, activate, then wait
/// (up to 1.5 s) for it to be frontmost. False if the app never came forward.
pub fn raise(pid: i32, window: Option<&CfRef>) -> bool {
    unsafe {
        if let Some(w) = window {
            AXUIElementSetAttributeValue(w.ptr(), cf_string("AXMinimized").ptr(), kCFBooleanFalse);
            AXUIElementPerformAction(w.ptr(), cf_string("AXRaise").ptr());
            AXUIElementSetAttributeValue(w.ptr(), cf_string("AXMain").ptr(), kCFBooleanTrue);
        }
        if let Some(app) = CfRef::owned(AXUIElementCreateApplication(pid)) {
            AXUIElementSetAttributeValue(app.ptr(), cf_string("AXFrontmost").ptr(), kCFBooleanTrue);
        }
    }
    let deadline = Instant::now() + std::time::Duration::from_millis(1500);
    while get_frontmost_app().0 != pid {
        if Instant::now() > deadline {
            return false;
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    std::thread::sleep(std::time::Duration::from_millis(200)); // let the window finish coming forward
    true
}

/// Whether two references are the same accessibility element.
pub fn same(a: &CfRef, b: &CfRef) -> bool {
    unsafe { CFEqual(a.ptr(), b.ptr()) }
}

/// An app's windows (including minimized ones) with their titles, via Accessibility.
pub fn app_windows(pid: i32) -> Vec<(CfRef, String)> {
    let Some(app) = CfRef::owned(unsafe { AXUIElementCreateApplication(pid) }) else { return Vec::new() };
    let Some(wins) = copy_attr(&app, &cf_string("AXWindows")) else { return Vec::new() };
    let k_title = cf_string("AXTitle");
    let n = unsafe { CFArrayGetCount(wins.ptr()) }.max(0);
    (0..n)
        .filter_map(|i| CfRef::retained(unsafe { CFArrayGetValueAtIndex(wins.ptr(), i) }))
        .map(|w| {
            let title = copy_attr(&w, &k_title).map(|t| string_of(t.ptr())).unwrap_or_default();
            (w, title)
        })
        .collect()
}

/// Full text of an element: its value, else its title or description (capped at 20k chars).
pub fn read_text(el: &CfRef) -> String {
    let keys: Vec<CfRef> = ["AXValue", "AXSelectedText", "AXTitle", "AXDescription", "AXHelp"].iter().map(|k| cf_string(k)).collect();
    crate::decision::truncate_chars(&label_of(el, &keys), 20_000)
}

/// Perform AXPress on an element.
pub fn press(el: &CfRef) -> bool {
    unsafe { AXUIElementPerformAction(el.ptr(), cf_string("AXPress").ptr()) == 0 }
}

fn bool_attr(el: &CfRef, key: &CfRef) -> Option<bool> {
    let v = copy_attr(el, key)?;
    unsafe { (CFGetTypeID(v.ptr()) == CFBooleanGetTypeID()).then(|| CFBooleanGetValue(v.ptr())) }
}

/// Choose a menu item in the frontmost app by its path, e.g. ["File", "New Note"].
/// Matching ignores case and a trailing "…"/"...". Returns what was chosen.
pub fn select_menu(path: &[&str]) -> Result<String, String> {
    if path.is_empty() {
        return Err("empty menu path".into());
    }
    let (pid, app) = get_frontmost_app();
    let root = CfRef::owned(unsafe { AXUIElementCreateApplication(pid) }).ok_or("no frontmost app")?;
    let k_children = cf_string("AXChildren");
    let k_title = cf_string("AXTitle");
    let k_role = cf_string("AXRole");
    let k_enabled = cf_string("AXEnabled");
    let mut current = copy_attr(&root, &cf_string("AXMenuBar")).ok_or_else(|| format!("{app} has no menu bar"))?;
    let norm = |s: &str| s.trim().trim_end_matches('…').trim_end_matches("...").trim().to_lowercase();

    for (i, want) in path.iter().enumerate() {
        // A menu bar item or menu item holds one AXMenu whose children are the items.
        let mut items = children(&current, &k_children);
        if items.len() == 1 && copy_attr(&items[0], &k_role).is_some_and(|r| string_of(r.ptr()) == "AXMenu") {
            items = children(&items[0], &k_children);
        }
        let titles: Vec<String> = items.iter().map(|it| copy_attr(it, &k_title).map(|t| string_of(t.ptr())).unwrap_or_default()).collect();
        let Some(pos) = titles.iter().position(|t| norm(t) == norm(want)) else {
            let have: Vec<&str> = titles.iter().map(String::as_str).filter(|t| !t.is_empty()).collect();
            return Err(format!("no menu item {want:?} in {app}; available: {}", have.join(", ")));
        };
        if i + 1 == path.len() {
            if bool_attr(&items[pos], &k_enabled) == Some(false) {
                return Err(format!("menu item {:?} is disabled right now", titles[pos]));
            }
            if !press(&items[pos]) {
                return Err(format!("could not choose {:?}", titles[pos]));
            }
            return Ok(format!("{app}: {}", path.join(" > ")));
        }
        current = items.swap_remove(pos);
    }
    unreachable!()
}

/// The display showing the frontmost app's front window (the main display if none is found).
pub fn active_display() -> CGRect {
    let displays = display_bounds();
    let main = displays.first().copied().unwrap_or_default();
    let (pid, _) = get_frontmost_app();
    windows()
        .into_iter()
        .find(|w| w.pid == pid as i64)
        .and_then(|w| {
            let (cx, cy) = (w.x as f64 + w.w as f64 / 2.0, w.y as f64 + w.h as f64 / 2.0);
            displays.iter().copied().find(|r| on_screen(cx, cy, std::slice::from_ref(r)))
        })
        .unwrap_or(main)
}

/// One on-screen window from the window server.
#[derive(Clone, Debug, serde::Serialize)]
pub struct WindowInfo {
    pub app: String,
    pub pid: i64,
    pub title: String,
    pub x: i32,
    pub y: i32,
    pub w: i32,
    pub h: i32,
}

/// Normal (layer 0) on-screen windows, front to back. Titles need Screen Recording permission.
pub fn windows() -> Vec<WindowInfo> {
    const ON_SCREEN_ONLY: u32 = 1 << 0;
    const EXCLUDE_DESKTOP: u32 = 1 << 4;
    let Some(list) = CfRef::owned(unsafe { CGWindowListCopyWindowInfo(ON_SCREEN_ONLY | EXCLUDE_DESKTOP, 0) }) else {
        return Vec::new();
    };
    let num = |d: CFTypeRef, k: CFTypeRef| -> i64 {
        let mut v: i64 = 0;
        unsafe {
            let n = CFDictionaryGetValue(d, k);
            if !n.is_null() {
                CFNumberGetValue(n, K_CF_NUMBER_SINT64, &mut v as *mut _ as *mut c_void);
            }
        }
        v
    };
    let n = unsafe { CFArrayGetCount(list.ptr()) }.max(0);
    let mut out = Vec::new();
    for i in 0..n {
        unsafe {
            let d = CFArrayGetValueAtIndex(list.ptr(), i);
            if d.is_null() || num(d, kCGWindowLayer) != 0 {
                continue;
            }
            let mut r = CGRect::default();
            let b = CFDictionaryGetValue(d, kCGWindowBounds);
            if b.is_null() || !CGRectMakeWithDictionaryRepresentation(b, &mut r) || r.size.width < 40.0 || r.size.height < 40.0 {
                continue;
            }
            out.push(WindowInfo {
                app: string_of(CFDictionaryGetValue(d, kCGWindowOwnerName)),
                pid: num(d, kCGWindowOwnerPID),
                title: string_of(CFDictionaryGetValue(d, kCGWindowName)),
                x: r.origin.x as i32,
                y: r.origin.y as i32,
                w: r.size.width as i32,
                h: r.size.height as i32,
            });
        }
    }
    out
}
