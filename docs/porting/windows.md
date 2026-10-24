# Porting Computer Use to Windows

This branch is a starting point for anyone who wants Computer Use on Windows. Nothing here works yet: this guide is the plan.
Pick a piece, comment on the PR to say you're on it, and push to the branch (or open a PR against it).

## What to port first

**Start with `cu-mcp`, the native MCP server.** It's the core: any AI app (Claude, Cursor, VS Code, Antigravity…) can drive the desktop through it with no API key.
All of its OS work goes through one Rust trait, `Desktop` in `crates/cu-native/src/desktop.rs`.
A Windows `Desktop` makes every MCP tool work. The menu bar app (Python, `cu/`) can come later.
The `windows` crate covers every API below.

## `Desktop` on Windows, method by method

| Method | macOS today | Windows |
|---|---|---|
| `elements`, `read_element` | Accessibility API (`ax.rs`) | **UI Automation** (`IUIAutomation`, a cache request for Name, ControlType, BoundingRectangle, HasKeyboardFocus). Walk the foreground window; keep the same output: role, label, centre point, focused flag, content before toolbars, text inputs always kept. Chromium and Electron expose page content through UIA. |
| `screenshot`, `main_display`, `on_screen` | ScreenCaptureKit / CGDisplay (`capture.rs`) | **Windows.Graphics.Capture** (fast, per monitor), or DXGI Desktop Duplication; GDI `BitBlt` as a fallback. Capture the foreground window's monitor, and handle per-monitor DPI (`SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)`) so coordinates match. |
| `click`, `move_mouse`, `drag`, `scroll`, `type_text`, `hotkey` | CGEvent (`input.rs`) | `SendInput`. Use `KEYEVENTF_UNICODE` for `type_text` (any language, emoji). Map ⌘ shortcuts to Ctrl. |
| `mouse_in_corner` (emergency stop) | cursor position | `GetCursorPos` against the virtual screen bounds. Keep this working: it's the user's emergency stop. |
| `open_app`, `open_url` | `open -a`, LaunchServices | Start menu shortcuts and `shell:AppsFolder` (UWP apps) by display name; `ShellExecuteW` for URLs. |
| `select_menu` | AX menu bar | UIA `MenuBar`/`MenuItem` patterns (`ExpandCollapse`, `Invoke`) |
| `windows`, `frontmost` | CGWindowList | `EnumWindows` + `GetForegroundWindow`, skipping cloaked and tool windows (`DwmGetWindowAttribute(DWMWA_CLOAKED)`) |
| `speak` | `say` | SAPI (`ISpVoice`) or `Windows.Media.SpeechSynthesis` |
| `permissions` | AX trust + Screen Recording | Mostly not needed, but UIPI blocks input into elevated windows: report when the foreground window is elevated and `cu-mcp` isn't. |
| `run_task` | runs the Python app's agent | unchanged once the Python side runs (below). |

Keep everything else in `cu-mcp` (tool schemas, matching, lessons, skills, logging) platform-neutral: it already is.
Paths: the lessons, skills and logs move from `~/Library/...` to `%APPDATA%\Computer Use\` and `%LOCALAPPDATA%\Computer Use\Logs\`. File locking moves from `flock` to `LockFileEx` (the `fs2` crate wraps both).

## Then the Python app

| Module | Replace with |
|---|---|
| `cu/menubar.py` (rumps) | a system tray icon: `pystray`, or the Win32 `Shell_NotifyIcon` API |
| `cu/prompt_bar.py`, `cu/ui.py`, `cu/onboarding.py`, `cu/keys_window.py`, `cu/shortcut_recorder.py` (AppKit) | Qt (PySide6) or WinUI 3. Keep the card look in `cu/ui.py` (rounded cards, pills, big numbers); Mica for the window background. |
| `cu/hotkey.py`, `cu/shortcut.py` (Carbon hot keys, modifier double-tap) | `RegisterHotKey`; double-tap of Ctrl through a low-level keyboard hook (`WH_KEYBOARD_LL`) |
| `cu/config.py` Keychain (`security`) | Windows Credential Manager (`keyring` package) |
| `cu/screen.py`, `cu/indicator.py` | call the Rust library (`cu_native.dll`), which the `Desktop` port provides |
| `cu/stt.py` Apple speech | already has Moonshine/Parakeet; Parakeet uses MLX (Apple only), so use the ONNX/Moonshine path |
| `cu/tts.py`, `cu/say.py` | Kokoro already works; system voices → SAPI |
| `cu/integration.py` | MCP config paths of Windows editors (`%APPDATA%\Code\User\mcp.json`, `%USERPROFILE%\.cursor\mcp.json`, …); start at login via the `Run` registry key |
| `cu/updater.py`, `packaging/` | an MSIX or Inno Setup installer, code-signed so SmartScreen doesn't block it |

## Checklist

- [ ] `Desktop` for Windows (UIA + Windows.Graphics.Capture + SendInput): every MCP tool works on Windows 11
- [ ] Per-monitor DPI and multiple displays
- [ ] `cargo test` passes on Windows; add a CI job (`windows-latest`)
- [ ] `cu-mcp.exe` connects from Claude, Cursor and VS Code on Windows
- [ ] The tweet benchmark (`scripts/bench.py`) passes through MCP on Windows
- [ ] Python app: tray, bar, shortcut, Credential Manager keys
- [ ] Packaging: signed installer plus the updater

## Ground rules

- Safety stays the same on every platform: the corner emergency stop, confirmations before sending, buying or deleting, and the clipboard never leaving the machine.
- Keep memory bounded (see `cu/memory.py`, `cu/skills.py`): no unbounded caches or logs.
- Test with fakes in CI (`tests/`, `crates/cu-native/src/mcp/tests.rs`); never drive a real desktop in CI.
