"""A system-wide key shortcut (e.g. ⌥Space) through Carbon's RegisterEventHotKey: the key is
taken before any app sees it, so it never types into the frontmost app, and it needs no
Accessibility permission. Callbacks arrive on the main thread via the app's run loop."""

import ctypes

from .shortcut import CMD, CTRL, OPT, SHIFT

_carbon = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Carbon.framework/Carbon")


def _fourcc(s: str) -> int:
    return int.from_bytes(s.encode(), "big")


class _HotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


_Handler = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
_carbon.InstallEventHandler.argtypes = [ctypes.c_void_p, _Handler, ctypes.c_uint32, ctypes.POINTER(_EventTypeSpec),
                                        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
_carbon.RegisterEventHotKey.argtypes = [ctypes.c_uint32, ctypes.c_uint32, _HotKeyID, ctypes.c_void_p, ctypes.c_uint32,
                                        ctypes.POINTER(ctypes.c_void_p)]
_carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]

_K_EVENT_CLASS_KEYBOARD = _fourcc("keyb")
_K_EVENT_HOTKEY_PRESSED = 5
_CARBON_MODS = {CMD: 1 << 8, SHIFT: 1 << 9, OPT: 1 << 11, CTRL: 1 << 12}

_callback = None
_handler = None  # kept alive: Carbon holds a raw pointer to it
_registered = None


def _on_hotkey(_next, _event, _data):
    if _callback is not None:
        _callback()
    return 0


def register(key_code: int, mods: int, callback) -> bool:
    """Make key_code + mods (shortcut.py flags) call `callback`. Replaces any previous one."""
    global _callback, _handler, _registered
    unregister()
    target = _carbon.GetApplicationEventTarget()
    if _handler is None:
        _handler = _Handler(_on_hotkey)
        spec = _EventTypeSpec(_K_EVENT_CLASS_KEYBOARD, _K_EVENT_HOTKEY_PRESSED)
        _carbon.InstallEventHandler(target, _handler, 1, ctypes.byref(spec), None, None)
    carbon_mods = sum(bit for flag, bit in _CARBON_MODS.items() if mods & flag)
    ref = ctypes.c_void_p()
    status = _carbon.RegisterEventHotKey(key_code, carbon_mods, _HotKeyID(_fourcc("CUse"), 1), target, 0, ctypes.byref(ref))
    if status != 0:
        return False  # taken by another app or the system
    _callback, _registered = callback, ref
    return True


def unregister():
    global _registered
    if _registered is not None:
        _carbon.UnregisterEventHotKey(_registered)
        _registered = None
