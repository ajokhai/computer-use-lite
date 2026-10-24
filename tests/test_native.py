"""The Rust library through the same ctypes bindings the app uses.

Nothing here moves the mouse or presses a key that exists: hotkey tests only send names
the library must refuse. Run with `uv run pytest` after `cargo build --release`.
"""

import ctypes
import resource

import pytest

from cu import screen
from cu.screen import _load_native_lib, native_json

lib = _load_native_lib()
pytestmark = pytest.mark.skipif(lib is None, reason="build crates/cu-native first (cargo build --release)")


def _rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6  # bytes on macOS


def test_unknown_keys_are_refused_instead_of_pressing_return():
    for chord in ("mystery", "command\x1ff13", "a\x1fb", ""):
        assert lib.cu_hotkey(chord.encode()) is False, chord


def test_strings_returned_by_rust_are_freed():
    # 400 distinct quoted phrases of ~400 chars: each call returns ~160 KB of JSON.
    task = " ".join(f"'{i:04d}{'x' * 400}'" for i in range(400)).encode()
    for _ in range(20):
        native_json("cu_extract_text_candidates", task, 400)
    before = _rss_mb()
    for _ in range(300):
        native_json("cu_extract_text_candidates", task, 400)
    # Leaking would add ~48 MB; allow allocator noise.
    assert _rss_mb() - before < 30


def test_string_functions_return_owned_pointers():
    # c_char_p restypes copy and drop the pointer, which is what leaked. Every one must be c_void_p.
    for name in ("cu_walk_ax_tree_json", "cu_extract_text_candidates"):
        assert getattr(lib, name).restype is ctypes.c_void_p, name


def test_ax_walk_returns_valid_elements():
    # Reads (never acts on) whatever app is frontmost. Needs Accessibility permission to find anything.
    app, els = screen.ax_elements(limit=50)
    assert isinstance(app, str) and app
    assert len(els) <= 50
    for e in els:
        assert e.role in screen.ACTIONABLE
        assert len(e.label) <= 80
        assert e.w > 2 and e.h > 2
