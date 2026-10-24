//! Screen capture with CoreGraphics, to a file (for the Python app) or to PNG bytes in memory
//! (for the MCP server), scaled down so screenshots stay cheap for models.

use std::ffi::{c_char, c_void, CString};
use std::ptr;

use crate::{CGPoint, CGRect, CGSize};

type CFTypeRef = *const c_void;
type CGImageRef = *const c_void;

const K_CG_WINDOW_LIST_OPTION_ON_SCREEN_ONLY: u32 = 1;
const K_CG_WINDOW_IMAGE_DEFAULT: u32 = 0;
const K_CG_WINDOW_IMAGE_NOMINAL_RESOLUTION: u32 = 1 << 4;
const K_CG_NULL_WINDOW_ID: u32 = 0;
const K_CF_URL_POSIX_PATH_STYLE: u32 = 0;
const K_CF_STRING_ENCODING_UTF8: u32 = 0x08000100;
const K_CG_IMAGE_ALPHA_PREMULTIPLIED_LAST: u32 = 1;
const K_CG_INTERPOLATION_HIGH: i32 = 3;

#[link(name = "CoreGraphics", kind = "framework")]
#[link(name = "CoreFoundation", kind = "framework")]
#[link(name = "ImageIO", kind = "framework")]
unsafe extern "C" {
    fn CGWindowListCreateImage(bounds: CGRect, list_options: u32, window_id: u32, image_options: u32) -> CGImageRef;
    fn CGMainDisplayID() -> u32;
    fn CGDisplayBounds(display: u32) -> CGRect;
    fn CGImageGetWidth(image: CGImageRef) -> usize;
    fn CGImageGetHeight(image: CGImageRef) -> usize;
    fn CGImageRelease(image: CGImageRef);
    fn CGColorSpaceCreateDeviceRGB() -> CFTypeRef;
    fn CGColorSpaceRelease(cs: CFTypeRef);
    fn CGBitmapContextCreate(data: *mut c_void, w: usize, h: usize, bits: usize, row_bytes: usize, cs: CFTypeRef, info: u32) -> CFTypeRef;
    fn CGBitmapContextCreateImage(ctx: CFTypeRef) -> CGImageRef;
    fn CGContextSetInterpolationQuality(ctx: CFTypeRef, q: i32);
    fn CGContextDrawImage(ctx: CFTypeRef, rect: CGRect, image: CGImageRef);
    fn CGContextRelease(ctx: CFTypeRef);

    fn CFStringCreateWithCString(alloc: CFTypeRef, c_str: *const c_char, encoding: u32) -> CFTypeRef;
    fn CFURLCreateWithFileSystemPath(alloc: CFTypeRef, path: CFTypeRef, style: u32, is_dir: bool) -> CFTypeRef;
    fn CFDataCreateMutable(alloc: CFTypeRef, capacity: isize) -> CFTypeRef;
    fn CFDataGetBytePtr(data: CFTypeRef) -> *const u8;
    fn CFDataGetLength(data: CFTypeRef) -> isize;
    fn CFRelease(cf: CFTypeRef);

    fn CGImageDestinationCreateWithURL(url: CFTypeRef, type_: CFTypeRef, count: usize, options: CFTypeRef) -> CFTypeRef;
    fn CGImageDestinationCreateWithData(data: CFTypeRef, type_: CFTypeRef, count: usize, options: CFTypeRef) -> CFTypeRef;
    fn CGImageDestinationAddImage(dest: CFTypeRef, image: CGImageRef, properties: CFTypeRef);
    fn CGImageDestinationFinalize(dest: CFTypeRef) -> bool;
    fn CFDataCreate(alloc: CFTypeRef, bytes: *const u8, len: isize) -> CFTypeRef;
    fn CGImageSourceCreateWithData(data: CFTypeRef, options: CFTypeRef) -> CFTypeRef;
    fn CGImageSourceCreateImageAtIndex(src: CFTypeRef, index: usize, options: CFTypeRef) -> CGImageRef;
}

/// Set once the window server's capture has stalled in this process; later captures go
/// straight to `screencapture`.
static SLOW: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
/// How long to wait for the window server before using `screencapture` instead. On some macOS
/// versions the capture is proxied through ScreenCaptureKit and can sit on a 30 s timeout.
const CG_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(5);

unsafe fn cf_str(s: &str) -> CFTypeRef {
    let c = CString::new(s).unwrap_or_default();
    CFStringCreateWithCString(ptr::null(), c.as_ptr(), K_CF_STRING_ENCODING_UTF8)
}

/// Write `image` as PNG into `dest` (made with `make_dest(type)`), then release `dest`.
unsafe fn write_png(image: CGImageRef, make_dest: impl FnOnce(CFTypeRef) -> CFTypeRef) -> bool {
    let png = cf_str("public.png");
    let dest = make_dest(png);
    CFRelease(png);
    if dest.is_null() {
        return false;
    }
    CGImageDestinationAddImage(dest, image, ptr::null());
    let ok = CGImageDestinationFinalize(dest);
    CFRelease(dest);
    ok
}

/// Capture a rect in global points (the whole main display if `w` or `h` is 0) at full pixel resolution.
pub fn capture_rect_to_png(x: f64, y: f64, w: f64, h: f64, out_path: &str) -> bool {
    unsafe {
        let rect = if w <= 0.0 || h <= 0.0 {
            CGDisplayBounds(CGMainDisplayID())
        } else {
            CGRect { origin: CGPoint { x, y }, size: CGSize { width: w, height: h } }
        };
        let image = CGWindowListCreateImage(rect, K_CG_WINDOW_LIST_OPTION_ON_SCREEN_ONLY, K_CG_NULL_WINDOW_ID, K_CG_WINDOW_IMAGE_DEFAULT);
        if image.is_null() {
            return false;
        }
        let path = cf_str(out_path);
        let url = if path.is_null() { ptr::null() } else { CFURLCreateWithFileSystemPath(ptr::null(), path, K_CF_URL_POSIX_PATH_STYLE, false) };
        if !path.is_null() {
            CFRelease(path);
        }
        let ok = !url.is_null() && write_png(image, |t| CGImageDestinationCreateWithURL(url, t, 1, ptr::null()));
        if !url.is_null() {
            CFRelease(url);
        }
        CGImageRelease(image);
        ok
    }
}

/// A screenshot of the main display as PNG bytes.
pub struct Shot {
    pub png: Vec<u8>,
    pub width: u32,
    pub height: u32,
    /// Screen points per image pixel: multiply image coordinates by this to get points.
    pub scale: f64,
    /// The captured display's top-left corner in global points (other displays can be negative).
    pub ox: f64,
    pub oy: f64,
}

/// Downscale with high-quality interpolation; the caller releases the result.
unsafe fn scaled(image: CGImageRef, w: usize, h: usize) -> CGImageRef {
    let cs = CGColorSpaceCreateDeviceRGB();
    let ctx = CGBitmapContextCreate(ptr::null_mut(), w, h, 8, 0, cs, K_CG_IMAGE_ALPHA_PREMULTIPLIED_LAST);
    CGColorSpaceRelease(cs);
    if ctx.is_null() {
        return ptr::null();
    }
    CGContextSetInterpolationQuality(ctx, K_CG_INTERPOLATION_HIGH);
    CGContextDrawImage(ctx, CGRect { origin: CGPoint::default(), size: CGSize { width: w as f64, height: h as f64 } }, image);
    let out = CGBitmapContextCreateImage(ctx);
    CGContextRelease(ctx);
    out
}

/// Capture the main display at point resolution, no wider than `max_width` pixels.
/// None without Screen Recording permission or if capture fails.
pub fn capture_main(max_width: u32) -> Option<Shot> {
    capture_display(unsafe { CGDisplayBounds(CGMainDisplayID()) }, max_width)
}

/// Capture one display (its bounds in global points) at point resolution, no wider than `max_width`.
/// Uses the window server, or `screencapture` if that stalls (then for the rest of the process)
/// or if CU_SCREENCAPTURE is set.
pub fn capture_display(bounds: CGRect, max_width: u32) -> Option<Shot> {
    use std::sync::atomic::Ordering;
    if !SLOW.load(Ordering::Relaxed) && std::env::var_os("CU_SCREENCAPTURE").is_none() {
        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let _ = tx.send(unsafe {
                encode(
                    CGWindowListCreateImage(bounds, K_CG_WINDOW_LIST_OPTION_ON_SCREEN_ONLY, K_CG_NULL_WINDOW_ID, K_CG_WINDOW_IMAGE_NOMINAL_RESOLUTION),
                    bounds,
                    max_width,
                )
            });
        });
        match rx.recv_timeout(CG_TIMEOUT) {
            Ok(shot) => return shot,
            // The stalled thread finishes on its own; its result is dropped.
            Err(_) => SLOW.store(true, Ordering::Relaxed),
        }
    }
    capture_with_tool(bounds, max_width)
}

/// The same capture through Apple's `screencapture` tool (full pixel resolution, scaled here).
fn capture_with_tool(bounds: CGRect, max_width: u32) -> Option<Shot> {
    let path = std::env::temp_dir().join(format!("cu-mcp-shot-{}.png", std::process::id()));
    let (o, s) = (bounds.origin, bounds.size);
    let ok = std::process::Command::new("/usr/sbin/screencapture")
        .args(["-x", "-t", "png", &format!("-R{},{},{},{}", o.x, o.y, s.width, s.height)])
        .arg(&path)
        .status()
        .is_ok_and(|st| st.success());
    let bytes = if ok { std::fs::read(&path).ok() } else { None };
    let _ = std::fs::remove_file(&path);
    let bytes = bytes?;
    unsafe {
        let data = CFDataCreate(ptr::null(), bytes.as_ptr(), bytes.len() as isize);
        if data.is_null() {
            return None;
        }
        let src = CGImageSourceCreateWithData(data, ptr::null());
        CFRelease(data);
        if src.is_null() {
            return None;
        }
        let image = CGImageSourceCreateImageAtIndex(src, 0, ptr::null());
        CFRelease(src);
        // At point resolution or narrower, like the window server path.
        let width = if max_width > 0 { max_width.min(s.width as u32) } else { s.width as u32 };
        encode(image, bounds, width)
    }
}

/// Downscale `image` to at most `max_width` and encode it as PNG; releases `image`.
unsafe fn encode(mut image: CGImageRef, bounds: CGRect, max_width: u32) -> Option<Shot> {
    unsafe {
        if image.is_null() {
            return None;
        }
        let (w, h) = (CGImageGetWidth(image), CGImageGetHeight(image));
        if w > max_width as usize && max_width > 0 {
            let nh = ((h as f64) * max_width as f64 / w as f64).round().max(1.0) as usize;
            let small = scaled(image, max_width as usize, nh);
            if !small.is_null() {
                CGImageRelease(image);
                image = small;
            }
        }
        let (w, h) = (CGImageGetWidth(image), CGImageGetHeight(image));
        let data = CFDataCreateMutable(ptr::null(), 0);
        let ok = !data.is_null() && write_png(image, |t| CGImageDestinationCreateWithData(data, t, 1, ptr::null()));
        CGImageRelease(image);
        if !ok {
            if !data.is_null() {
                CFRelease(data);
            }
            return None;
        }
        let png = std::slice::from_raw_parts(CFDataGetBytePtr(data), CFDataGetLength(data) as usize).to_vec();
        CFRelease(data);
        Some(Shot { png, width: w as u32, height: h as u32, scale: bounds.size.width / w as f64, ox: bounds.origin.x, oy: bounds.origin.y })
    }
}
