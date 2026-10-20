use std::ffi::{c_char, c_void, CString};
use std::ptr;

type CFTypeRef = *const c_void;
type CFStringRef = *const c_void;
type CFURLRef = *const c_void;
type CFDictionaryRef = *const c_void;
type CGImageRef = *const c_void;
type CGImageDestinationRef = *mut c_void;

#[repr(C)]
#[derive(Copy, Clone, Debug, Default)]
pub struct CGPoint {
    pub x: f64,
    pub y: f64,
}

#[repr(C)]
#[derive(Copy, Clone, Debug, Default)]
pub struct CGSize {
    pub width: f64,
    pub height: f64,
}

#[repr(C)]
#[derive(Copy, Clone, Debug, Default)]
pub struct CGRect {
    pub origin: CGPoint,
    pub size: CGSize,
}

const K_CG_WINDOW_LIST_OPTION_ALL: u32 = 0;
const K_CG_WINDOW_IMAGE_DEFAULT: u32 = 0;
const K_CG_NULL_WINDOW_ID: u32 = 0;
const K_CF_URL_POSIX_PATH_STYLE: u32 = 0;
const K_CF_STRING_ENCODING_UTF8: u32 = 0x08000100;

#[link(name = "CoreGraphics", kind = "framework")]
#[link(name = "CoreFoundation", kind = "framework")]
#[link(name = "ImageIO", kind = "framework")]
unsafe extern "C" {
    fn CGWindowListCreateImage(
        screen_bounds: CGRect,
        list_options: u32,
        window_id: u32,
        image_options: u32,
    ) -> CGImageRef;
    fn CGMainDisplayID() -> u32;
    fn CGDisplayBounds(display: u32) -> CGRect;
    fn CGImageRelease(image: CGImageRef);

    fn CFStringCreateWithCString(alloc: CFTypeRef, c_str: *const c_char, encoding: u32) -> CFStringRef;
    fn CFURLCreateWithFileSystemPath(alloc: CFTypeRef, file_path: CFStringRef, path_style: u32, is_dir: bool) -> CFURLRef;
    fn CFRelease(cf: CFTypeRef);

    fn CGImageDestinationCreateWithURL(
        url: CFURLRef,
        type_: CFStringRef,
        count: usize,
        options: CFDictionaryRef,
    ) -> CGImageDestinationRef;
    fn CGImageDestinationAddImage(dest: CGImageDestinationRef, image: CGImageRef, properties: CFDictionaryRef);
    fn CGImageDestinationFinalize(dest: CGImageDestinationRef) -> bool;
}

pub fn capture_rect_to_png(x: f64, y: f64, w: f64, h: f64, out_path: &str) -> bool {
    unsafe {
        let rect = if w <= 0.0 || h <= 0.0 {
            let disp = CGMainDisplayID();
            CGDisplayBounds(disp)
        } else {
            CGRect {
                origin: CGPoint { x, y },
                size: CGSize { width: w, height: h },
            }
        };

        let image = CGWindowListCreateImage(
            rect,
            K_CG_WINDOW_LIST_OPTION_ALL,
            K_CG_NULL_WINDOW_ID,
            K_CG_WINDOW_IMAGE_DEFAULT,
        );
        if image.is_null() {
            return false;
        }

        let c_path = match CString::new(out_path) {
            Ok(p) => p,
            Err(_) => {
                CGImageRelease(image);
                return false;
            }
        };

        let path_cf = CFStringCreateWithCString(ptr::null(), c_path.as_ptr(), K_CF_STRING_ENCODING_UTF8);
        if path_cf.is_null() {
            CGImageRelease(image);
            return false;
        }

        let url = CFURLCreateWithFileSystemPath(ptr::null(), path_cf, K_CF_URL_POSIX_PATH_STYLE, false);
        CFRelease(path_cf);
        if url.is_null() {
            CGImageRelease(image);
            return false;
        }

        let c_type = CString::new("public.png").unwrap();
        let type_cf = CFStringCreateWithCString(ptr::null(), c_type.as_ptr(), K_CF_STRING_ENCODING_UTF8);

        let dest = CGImageDestinationCreateWithURL(url, type_cf, 1, ptr::null());
        CFRelease(url);
        CFRelease(type_cf);

        if dest.is_null() {
            CGImageRelease(image);
            return false;
        }

        CGImageDestinationAddImage(dest, image, ptr::null());
        let ok = CGImageDestinationFinalize(dest);
        CFRelease(dest as CFTypeRef);
        CGImageRelease(image);

        ok
    }
}
