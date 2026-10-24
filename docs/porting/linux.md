# Porting Computer Use to Linux

This branch is a starting point for anyone who wants Computer Use on Linux. Nothing here works yet: this guide is the plan.
Pick a piece, comment on the PR to say you're on it, and push to the branch (or open a PR against it).

## What to port first

**Start with `cu-mcp`, the native MCP server.** It's the core: any AI app (Claude, Cursor, VS Code, Antigravity…) can drive the desktop through it with no API key.
All of its OS work goes through one Rust trait, `Desktop` in `crates/cu-native/src/desktop.rs`.
A Linux `Desktop` makes every MCP tool work. The menu bar app (Python, `cu/`) can come later.

## `Desktop` on Linux, method by method

| Method | macOS today | Linux |
|---|---|---|
| `elements`, `read_element` | Accessibility API (`ax.rs`) | **AT-SPI2** over D-Bus (`atspi` crate). Walk the active window's tree; keep the same output: role, label, centre point, focused flag, content before toolbars, text inputs always kept. |
| `screenshot`, `main_display`, `on_screen` | ScreenCaptureKit / CGDisplay (`capture.rs`) | **X11**: `xcb`/XShm. **Wayland**: the `org.freedesktop.portal.Screenshot` portal (asks the user once) or PipeWire via the ScreenCast portal. Return the frontmost window's display, as on macOS. |
| `click`, `move_mouse`, `drag`, `scroll`, `type_text`, `hotkey` | CGEvent (`input.rs`) | **X11**: XTest. **Wayland**: `libei` / the RemoteDesktop portal, or `uinput` (needs the user in the `input` group). `type_text` must handle any Unicode, not just keys on a US layout. |
| `mouse_in_corner` (emergency stop) | cursor position | the same, from X11 / the portal. Keep this working: it's the user's emergency stop. |
| `open_app`, `open_url` | `open -a`, LaunchServices | `.desktop` files (`gio launch` / `gtk-launch`), `xdg-open` for URLs. Match apps by `Name=`. |
| `select_menu` | AX menu bar | AT-SPI menus where the app exports them; otherwise return a clear error so the model uses `click_element`. |
| `windows`, `frontmost` | CGWindowList | X11: `_NET_CLIENT_LIST`, `_NET_ACTIVE_WINDOW`. Wayland: AT-SPI's active frame (compositors don't expose windows to clients). |
| `speak` | `say` | `spd-say` (speech-dispatcher), or `espeak-ng`. |
| `permissions` | AX trust + Screen Recording | Whether the AT-SPI bus is reachable (`org.a11y.Bus`), and whether screenshots work (portal granted / X11). |
| `run_task` | runs the Python app's agent | unchanged once the Python side runs (below). |

Keep everything else in `cu-mcp` (tool schemas, matching, lessons, skills, logging) platform-neutral: it already is.

## Then the Python app

| Module | Replace with |
|---|---|
| `cu/menubar.py` (rumps) | a tray icon: `pystray`, or GTK `AppIndicator3` |
| `cu/prompt_bar.py`, `cu/ui.py`, `cu/onboarding.py`, `cu/keys_window.py`, `cu/shortcut_recorder.py` (AppKit) | GTK 4 / libadwaita, or Qt. Keep the card look in `cu/ui.py` (rounded cards, pills, big numbers). |
| `cu/hotkey.py`, `cu/shortcut.py` (Carbon hot keys, modifier double-tap) | X11: `XGrabKey`. Wayland: the GlobalShortcuts portal. Double-tap of a modifier may need `evdev`. |
| `cu/config.py` Keychain (`security`) | Secret Service (`keyring` package or `secret-tool`) |
| `cu/screen.py`, `cu/indicator.py` | call the Rust library (`libcu_native.so`), which the `Desktop` port provides |
| `cu/stt.py` Apple speech | already has Moonshine/Parakeet; Parakeet uses MLX (Apple only), so use the ONNX/Moonshine path |
| `cu/tts.py`, `cu/say.py` | Kokoro already works; system voices → speech-dispatcher |
| `cu/integration.py` | MCP config paths of Linux editors (`~/.config/Code/User/mcp.json`, `~/.cursor/mcp.json`, …) and autostart via `~/.config/autostart/*.desktop` |
| `cu/updater.py`, `packaging/` | AppImage or Flatpak (Flatpak needs portal permissions for input and screenshots) |

## Checklist

- [ ] `Desktop` for X11 (AT-SPI + XTest + XShm): every MCP tool works on GNOME/X11
- [ ] `Desktop` for Wayland (AT-SPI + portals / libei)
- [ ] `cargo test` passes on Linux; add a CI job (`ubuntu-latest`, with `xvfb-run` for X11)
- [ ] `cu-mcp` connects from Claude Code, Cursor and VS Code on Linux
- [ ] The tweet benchmark (`scripts/bench.py`) passes through MCP on Linux
- [ ] Python app: tray, bar, shortcut, Secret Service keys
- [ ] Packaging: AppImage or Flatpak, plus the updater

## Ground rules

- Safety stays the same on every platform: the corner emergency stop, confirmations before sending, buying or deleting, and the clipboard never leaving the machine.
- Keep memory bounded (see `cu/memory.py`, `cu/skills.py`): no unbounded caches or logs.
- Test with fakes in CI (`tests/`, `crates/cu-native/src/mcp/tests.rs`); never drive a real desktop in CI.
