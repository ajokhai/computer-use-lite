//! Everything the MCP server does to the Mac goes through [`Desktop`], so the server can be
//! tested against a fake one. [`MacDesktop`] is the real implementation.

use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use crate::ax::{self, CfRef, WindowInfo};
use crate::capture::{self, Shot};
use crate::{input, NativeElement};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Button {
    Left,
    Right,
    Middle,
}

pub trait Desktop {
    /// Size of the main display in points.
    fn main_display(&self) -> (f64, f64);
    /// Whether (x, y) in global points is on any display.
    fn on_screen(&self, x: f64, y: f64) -> bool;
    /// The frontmost app and its actionable elements. Replaces the previous snapshot.
    fn elements(&mut self, limit: usize) -> (String, Vec<NativeElement>);
    /// Full text of element `index` from the latest snapshot.
    fn read_element(&mut self, index: usize) -> Option<String>;
    fn screenshot(&mut self, max_width: u32) -> Result<Shot, String>;
    fn click(&mut self, x: f64, y: f64, button: Button, clicks: usize);
    fn move_mouse(&mut self, x: f64, y: f64);
    fn drag(&mut self, from: (f64, f64), to: (f64, f64));
    fn scroll(&mut self, x: f64, y: f64, dy: i32, dx: i32);
    fn type_text(&mut self, text: &str);
    /// False, pressing nothing, if a key name is unknown.
    fn hotkey(&mut self, keys: &[&str]) -> bool;
    fn open_app(&mut self, name: &str) -> Result<String, String>;
    fn open_url(&mut self, url: &str) -> Result<(), String>;
    fn select_menu(&mut self, path: &[&str]) -> Result<String, String>;
    fn windows(&mut self) -> Vec<WindowInfo>;
    /// The user's emergency stop: the mouse pushed into a screen corner.
    fn mouse_in_corner(&self) -> bool;
    fn frontmost(&self) -> String;
    fn sleep(&mut self, d: Duration);
    fn speak(&mut self, text: &str) -> Result<(), String>;
    /// Run a whole task with the app's own agent (Jev/Gemini keys). Returns its log.
    fn run_task(&mut self, task: &str, mode: &str, allow_risky: bool) -> Result<String, String>;
    /// (Accessibility, Screen Recording) permission for this process.
    fn permissions(&self) -> (bool, bool);
}

/// The real Mac.
#[derive(Default)]
pub struct MacDesktop {
    /// Live references for the latest snapshot, index-aligned with its elements.
    refs: Vec<CfRef>,
}

#[link(name = "CoreGraphics", kind = "framework")]
unsafe extern "C" {
    fn CGPreflightScreenCaptureAccess() -> bool;
}

const TASK_TIMEOUT: Duration = Duration::from_secs(15 * 60);
const MAX_TASK_LOG: usize = 64 * 1024;

impl Desktop for MacDesktop {
    fn main_display(&self) -> (f64, f64) {
        ax::display_bounds().first().map_or((1440.0, 900.0), |r| (r.size.width, r.size.height))
    }

    fn on_screen(&self, x: f64, y: f64) -> bool {
        ax::on_screen(x, y, &ax::display_bounds())
    }

    fn elements(&mut self, limit: usize) -> (String, Vec<NativeElement>) {
        let snap = ax::walk(limit, true);
        self.refs = snap.refs;
        (snap.result.app, snap.result.elements)
    }

    fn read_element(&mut self, index: usize) -> Option<String> {
        self.refs.get(index).map(ax::read_text)
    }

    fn screenshot(&mut self, max_width: u32) -> Result<Shot, String> {
        capture::capture_display(ax::active_display(), max_width).ok_or_else(|| {
            "screen capture failed: grant Screen Recording to the app that started this server \
             (System Settings › Privacy & Security › Screen Recording)"
                .to_string()
        })
    }

    fn click(&mut self, x: f64, y: f64, button: Button, clicks: usize) {
        let b = match button {
            Button::Left => 0,
            Button::Right => 1,
            Button::Middle => 2,
        };
        input::click_at(x, y, b, clicks);
    }

    fn move_mouse(&mut self, x: f64, y: f64) {
        input::move_to_smooth(x, y, 120.0);
    }

    fn drag(&mut self, from: (f64, f64), to: (f64, f64)) {
        input::drag_to(from.0, from.1, to.0, to.1, 300.0);
    }

    fn scroll(&mut self, x: f64, y: f64, dy: i32, dx: i32) {
        input::move_to_smooth(x, y, 60.0);
        input::scroll(dy, dx);
    }

    fn type_text(&mut self, text: &str) {
        input::type_unicode(text);
    }

    fn hotkey(&mut self, keys: &[&str]) -> bool {
        input::hotkey(keys)
    }

    fn open_app(&mut self, name: &str) -> Result<String, String> {
        let out = Command::new("/usr/bin/open").args(["-a", name]).output().map_err(|e| e.to_string())?;
        if !out.status.success() {
            return Err(format!("no app named {name:?}"));
        }
        // Wait (up to 3 s) for it to come to the front, so the next call sees its window.
        let deadline = Instant::now() + Duration::from_secs(3);
        let want = name.to_lowercase();
        while Instant::now() < deadline {
            let front = ax::get_frontmost_app().1;
            if front.to_lowercase().contains(&want) || want.contains(&front.to_lowercase()) {
                return Ok(format!("opened {front}"));
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        Ok(format!("opened {name}; frontmost is {}", ax::get_frontmost_app().1))
    }

    fn open_url(&mut self, url: &str) -> Result<(), String> {
        let out = Command::new("/usr/bin/open").arg(url).output().map_err(|e| e.to_string())?;
        if out.status.success() { Ok(()) } else { Err(String::from_utf8_lossy(&out.stderr).trim().to_string()) }
    }

    fn select_menu(&mut self, path: &[&str]) -> Result<String, String> {
        ax::select_menu(path)
    }

    fn windows(&mut self) -> Vec<WindowInfo> {
        ax::windows()
    }

    fn mouse_in_corner(&self) -> bool {
        let p = input::get_mouse_location();
        ax::display_bounds().iter().any(|r| {
            let (l, t) = (r.origin.x, r.origin.y);
            let (rt, b) = (l + r.size.width - 1.0, t + r.size.height - 1.0);
            let near = |a: f64, v: f64| (a - v).abs() <= 2.0;
            (near(p.x, l) || near(p.x, rt)) && (near(p.y, t) || near(p.y, b))
        })
    }

    fn frontmost(&self) -> String {
        ax::get_frontmost_app().1
    }

    fn sleep(&mut self, d: Duration) {
        std::thread::sleep(d);
    }

    fn speak(&mut self, text: &str) -> Result<(), String> {
        let voice = settings_voice();
        // Mac voices are spoken here; Gemini and Kokoro voices need the app's voice stack.
        if let Some(name) = voice.as_deref().and_then(|v| v.strip_prefix("mac:")).or(voice.as_deref().filter(|v| v.is_empty())) {
            return say(text, name);
        }
        match (voice, app_command()) {
            (Some(_), Some(mut cmd)) => {
                cmd.args(["--speak", text]);
                run_capped(cmd, Duration::from_secs(120)).map(|_| ())
            }
            _ => say(text, ""),
        }
    }

    fn run_task(&mut self, task: &str, mode: &str, allow_risky: bool) -> Result<String, String> {
        let mut cmd = app_command().ok_or(
            "the Computer Use app isn't installed, so there's no built-in agent to hand the task to; do it step by step with the other tools",
        )?;
        cmd.args(["--cli", "--mode", mode]);
        if allow_risky {
            cmd.arg("--yolo");
        }
        cmd.arg("--").arg(task);
        run_capped(cmd, TASK_TIMEOUT)
    }

    fn permissions(&self) -> (bool, bool) {
        (ax::ax_trusted(), unsafe { CGPreflightScreenCaptureAccess() })
    }
}

fn say(text: &str, voice: &str) -> Result<(), String> {
    let mut cmd = Command::new("/usr/bin/say");
    if !voice.is_empty() {
        cmd.args(["-v", voice]);
    }
    cmd.arg("--").arg(text);
    run_capped(cmd, Duration::from_secs(120)).map(|_| ())
}

/// The user's chosen speaking voice from the app's settings, e.g. "mac:Samantha" or "gemini:Kore".
fn settings_voice() -> Option<String> {
    let home = std::env::var_os("HOME")?;
    let path = Path::new(&home).join("Library/Application Support/Computer Use/settings.json");
    let v: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()?;
    v.get("speaking_voice")?.as_str().map(String::from)
}

/// How to start the Computer Use app's own executable (for voices and whole tasks):
/// `CU_APP`, the app bundle this binary ships in, or the development checkout.
pub fn app_command() -> Option<Command> {
    if let Some(p) = std::env::var_os("CU_APP").filter(|p| !p.is_empty()) {
        return Some(Command::new(p));
    }
    let exe = std::env::current_exe().ok()?.canonicalize().ok()?;
    let dir = exe.parent()?;
    for candidate in [dir.join("Computer Use"), dir.join("../MacOS/Computer Use")] {
        if candidate.is_file() {
            return Some(Command::new(candidate));
        }
    }
    // Development: <project>/crates/cu-native/target/<profile>/cu-mcp
    let project = dir.ancestors().nth(4)?;
    if !project.join("pyproject.toml").is_file() {
        return None;
    }
    let uv: PathBuf = std::env::var_os("HOME")
        .map(|h| Path::new(&h).join(".local/bin/uv"))
        .filter(|p| p.is_file())
        .unwrap_or_else(|| "uv".into());
    let mut cmd = Command::new(uv);
    cmd.arg("run").arg("--directory").arg(project).args(["python", "-m", "cu.app"]);
    Some(cmd)
}

/// Run a command with no stdin, killing it after `timeout`. Returns stdout+stderr,
/// keeping only the last `MAX_TASK_LOG` bytes so a chatty child can't grow our memory.
fn run_capped(mut cmd: Command, timeout: Duration) -> Result<String, String> {
    let mut child = cmd
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("couldn't start {:?}: {e}", cmd.get_program()))?;
    let readers: Vec<_> = [child.stdout.take().map(|s| Box::new(s) as Box<dyn Read + Send>), child.stderr.take().map(|s| Box::new(s) as Box<dyn Read + Send>)]
        .into_iter()
        .flatten()
        .map(|mut r| {
            std::thread::spawn(move || {
                let mut tail: Vec<u8> = Vec::new();
                let mut buf = [0u8; 8192];
                while let Ok(n) = r.read(&mut buf) {
                    if n == 0 {
                        break;
                    }
                    tail.extend_from_slice(&buf[..n]);
                    if tail.len() > MAX_TASK_LOG {
                        tail.drain(..tail.len() - MAX_TASK_LOG);
                    }
                }
                tail
            })
        })
        .collect();
    let deadline = Instant::now() + timeout;
    let status = loop {
        if let Some(s) = child.try_wait().map_err(|e| e.to_string())? {
            break Some(s);
        }
        if Instant::now() > deadline {
            let _ = child.kill();
            let _ = child.wait();
            break None;
        }
        std::thread::sleep(Duration::from_millis(100));
    };
    let mut out = String::new();
    for r in readers {
        out.push_str(&String::from_utf8_lossy(&r.join().unwrap_or_default()));
    }
    let out = out.trim().to_string();
    match status {
        None => Err(format!("timed out after {} s\n{out}", timeout.as_secs())),
        Some(s) if s.success() => Ok(out),
        Some(s) => Err(format!("exited with {s}\n{out}")),
    }
}
