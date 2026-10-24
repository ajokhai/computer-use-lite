//! MCP server over stdio (newline-delimited JSON-RPC 2.0): the tools any AI uses to see and
//! drive the Mac. The calling model does the thinking; these are its eyes and hands.

use std::io::{BufRead, Write};
use std::time::{Duration, Instant};

use base64::Engine;
use serde_json::{json, Value};

use crate::decision::truncate_chars;
use crate::desktop::{Button, Desktop};
use crate::lessons::{self, Store};
use crate::skills::Skills;
use crate::NativeElement;

pub const SERVER_NAME: &str = "computer-use-lite";
const PROTOCOL_VERSIONS: &[&str] = &["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"];
const DEFAULT_SHOT_WIDTH: u32 = 1280;
const MAX_ELEMENTS: usize = 600; // MCP clients can take more than Jev's 255 choices
const MAX_LINE: usize = 4 * 1024 * 1024; // a request bigger than this is dropped, not buffered
const MAX_TEXT: usize = 20_000;

pub const INSTRUCTIONS: &str = "Tools to see and operate the user's whole Mac (any app, not just a browser).
Loop: look (list_screen_elements, and take_screenshot when the elements don't tell you enough), do one action, then look again to check it worked.
Prefer keyboard shortcuts (press_keys), select_menu, open_app and open_url over clicking. Prefer click_element (an id from list_screen_elements, or a description) over click_at.
All x/y coordinates, in every tool's input and output, are pixels of the latest take_screenshot image, so they line up with what you see.
Don't stop to ask the user except before spending money or permanently deleting something they didn't ask for; otherwise carry the task through. If a tool says the user stopped you (the mouse in a screen corner, or Escape pressed 3 times), stop and ask.
Windows behind others still work: list_screen_elements with app reads any app's windows without bringing them forward; clicking or typing into one of its elements brings that window forward first. focus_window brings a window (even a minimized one) to the front.
Use speak() for short spoken updates when the user may be listening rather than watching.
Call recall at the start of a task, and skills before a multi-step one. When a step fails, look again (list or screenshot), try a different way, and once something works, call remember (and save_skill for a reusable multi-step way).";

/// Tools that change something on screen; all refused while the emergency stop is active.
const ACTING: &[&str] = &[
    "click_element", "click_at", "type_text", "press_keys", "scroll", "drag", "move_mouse",
    "open_app", "open_url", "select_menu", "computer_task", "focus_window",
];

pub struct Server<D: Desktop> {
    desk: D,
    app: String,
    elements: Vec<NativeElement>,
    contexts: Vec<String>,
    /// Screen points per screenshot pixel; set from the default width until a screenshot is taken.
    scale: f64,
    /// Global position of the latest screenshot's display (the main display until one is taken).
    ox: f64,
    oy: f64,
    lessons: Store,
    skills: Skills,
    /// One line per tool call (for timing, diagnosis and learning); None in tests.
    log: Option<std::path::PathBuf>,
    /// The app writes the time here when the user presses Escape 3 times.
    stop_file: std::path::PathBuf,
}

/// How long after Escape ×3 acting tools are refused.
const STOP_SECS: f64 = 10.0;

const MAX_LOG_BYTES: u64 = 5 * 1024 * 1024; // then rotated to mcp.log.1, like the app's logs

/// UTC "YYYY-MM-DD HH:MM:SS" for a Unix time (no date library needed).
fn utc(secs: u64) -> String {
    let (days, rem) = ((secs / 86400) as i64, secs % 86400);
    // Howard Hinnant's civil-from-days.
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!("{y:04}-{m:02}-{d:02} {:02}:{:02}:{:02}", rem / 3600, rem % 3600 / 60, rem % 60)
}

/// Arguments for the log: strings cut to 60 characters (typed text may be private), no images.
fn short_args(args: &Value) -> String {
    match args {
        Value::String(s) => Value::String(truncate_chars(s, 60)).to_string(),
        Value::Object(m) => format!("{{{}}}", m.iter().map(|(k, v)| format!("{k}: {}", short_args(v))).collect::<Vec<_>>().join(", ")),
        Value::Array(a) => format!("[{}]", a.iter().take(10).map(short_args).collect::<Vec<_>>().join(", ")),
        v => v.to_string(),
    }
}

/// A tool's outcome: content blocks, and whether it failed.
struct Reply {
    content: Vec<Value>,
    is_error: bool,
}

impl Reply {
    fn text(s: impl Into<String>) -> Self {
        Reply { content: vec![json!({"type": "text", "text": s.into()})], is_error: false }
    }
    fn error(s: impl Into<String>) -> Self {
        Reply { content: vec![json!({"type": "text", "text": s.into()})], is_error: true }
    }
}

type ToolResult = Result<Reply, String>;

fn arg_str<'a>(args: &'a Value, key: &str) -> Option<&'a str> {
    args.get(key).and_then(Value::as_str).map(str::trim).filter(|s| !s.is_empty())
}

fn req_str<'a>(args: &'a Value, key: &str) -> Result<&'a str, String> {
    arg_str(args, key).ok_or_else(|| format!("missing required argument {key:?}"))
}

fn arg_f64(args: &Value, key: &str) -> Result<f64, String> {
    args.get(key).and_then(Value::as_f64).ok_or_else(|| format!("missing number argument {key:?}"))
}

fn arg_bool(args: &Value, key: &str) -> bool {
    args.get(key).and_then(Value::as_bool).unwrap_or(false)
}

fn button(args: &Value) -> Result<Button, String> {
    match arg_str(args, "button").unwrap_or("left").to_lowercase().as_str() {
        "left" => Ok(Button::Left),
        "right" => Ok(Button::Right),
        "middle" => Ok(Button::Middle),
        b => Err(format!("unknown button {b:?}; use left, right or middle")),
    }
}

fn role_name(role: &str) -> &str {
    role.strip_prefix("AX").unwrap_or(role)
}

/// Levenshtein similarity in [0, 1] over characters.
fn similarity(a: &str, b: &str) -> f64 {
    let (a, b): (Vec<char>, Vec<char>) = (a.chars().collect(), b.chars().collect());
    if a.is_empty() && b.is_empty() {
        return 1.0;
    }
    let mut prev: Vec<usize> = (0..=b.len()).collect();
    for (i, ca) in a.iter().enumerate() {
        let mut cur = vec![i + 1; b.len() + 1];
        for (j, cb) in b.iter().enumerate() {
            cur[j + 1] = (prev[j] + usize::from(ca != cb)).min(prev[j + 1] + 1).min(cur[j] + 1);
        }
        prev = cur;
    }
    1.0 - prev[b.len()] as f64 / a.len().max(b.len()) as f64
}

fn words(s: &str) -> Vec<String> {
    s.to_lowercase().split(|c: char| !c.is_alphanumeric()).filter(|w| !w.is_empty()).map(String::from).collect()
}

/// Role words a description might use ("the Send button", "search field").
const ROLE_HINTS: &[(&str, &[&str])] = &[
    ("button", &["AXButton", "AXPopUpButton", "AXMenuButton"]),
    ("field", &["AXTextField", "AXSearchField", "AXComboBox", "AXTextArea"]),
    ("box", &["AXTextField", "AXSearchField", "AXComboBox", "AXTextArea", "AXCheckBox"]),
    ("link", &["AXLink"]),
    ("tab", &["AXTab", "AXRadioButton"]),
    ("checkbox", &["AXCheckBox"]),
    ("menu", &["AXMenuItem", "AXMenuBarItem", "AXMenuButton", "AXPopUpButton"]),
    ("row", &["AXRow", "AXCell"]),
];

/// Role names a description can use as written in list_screen_elements ("TextArea", "Button"…).
const ROLE_NAMES: &[&str] = &[
    "button", "textarea", "textfield", "searchfield", "combobox", "checkbox", "radiobutton", "popupbutton",
    "menubutton", "link", "menuitem", "menubaritem", "tab", "slider", "cell", "row", "image", "statictext",
];

/// How well `desc` describes `el`, roughly 0..2.4.
pub fn match_score(desc: &str, el: &NativeElement) -> f64 {
    let d = desc.trim().to_lowercase();
    let label = el.label.to_lowercase();
    let dw = words(&d);
    let lw = words(&label);
    // Compare against the description minus role words, so "Send button" ≈ "Send".
    let core: Vec<&str> = dw.iter().map(String::as_str).filter(|w| !ROLE_HINTS.iter().any(|(h, _)| h == w)).collect();
    let core = core.join(" ");
    let mut score = similarity(&core, &lw.join(" ")).max(similarity(&d, &label));
    if !lw.is_empty() {
        let shared = lw.iter().filter(|w| dw.contains(w)).count() as f64;
        score = score.max(shared / lw.len().max(core.split(' ').count()) as f64);
    }
    if label.chars().count() >= 2 && (d.contains(&label) || (!core.is_empty() && label.contains(&core))) {
        score += 0.3;
    }
    // An exact name beats any partial or nearby-text match: a quoted name ('Button "Post"'),
    // or the whole description (minus role words) being the label.
    let quoted = d.split(['"', '\u{201c}', '\u{201d}']).nth(1).map(str::trim).filter(|q| !q.is_empty());
    if !label.is_empty() && (quoted == Some(label.as_str()) || label == core || label == d) {
        score += 1.0;
    }
    if ROLE_HINTS.iter().any(|(h, roles)| dw.iter().any(|w| w == h) && roles.contains(&el.role.as_str())) {
        score += 0.1;
    }
    // A role written as list_screen_elements shows it ('TextArea "Message input"') is a requirement:
    // the right role counts for a lot, and another role (a chat message quoting the words) against.
    let role = role_name(&el.role).to_lowercase();
    if let Some(named) = dw.iter().find(|w| ROLE_NAMES.contains(&w.as_str())) {
        score += if *named == role { 0.5 } else { -0.5 };
    }
    score
}

/// The best element for `desc`: Some((index, score, runner-up index)) if any scores ≥ 0.5.
pub fn best_match(desc: &str, els: &[NativeElement]) -> Option<(usize, f64, Option<usize>)> {
    let mut scored: Vec<(usize, f64)> = els.iter().enumerate().map(|(i, e)| (i, match_score(desc, e))).collect();
    scored.sort_by(|a, b| b.1.total_cmp(&a.1));
    let (best, s) = *scored.first()?;
    let close = scored.get(1).filter(|(_, s2)| s - s2 < 0.05).map(|&(i, _)| i);
    (s >= 0.5).then_some((best, s, close))
}

/// Short nearby text for buttons, links and fields with short labels (e.g. which "Reply").
fn contexts(els: &[NativeElement]) -> Vec<String> {
    let texts: Vec<&NativeElement> = els
        .iter()
        .filter(|e| matches!(e.role.as_str(), "AXStaticText" | "AXHeading" | "AXTextArea") && e.label.chars().count() > 3)
        .collect();
    els.iter()
        .map(|e| {
            if !matches!(e.role.as_str(), "AXButton" | "AXLink" | "AXPopUpButton" | "AXTextField") || e.label.chars().count() >= 20 {
                return String::new();
            }
            texts
                .iter()
                .filter(|t| (0..=100).contains(&(e.y - t.y)) && (e.x - t.x).abs() < 450 && t.label != e.label)
                .min_by_key(|t| (e.y - t.y).pow(2) + (e.x - t.x).pow(2))
                .map(|t| truncate_chars(&t.label, 35))
                .unwrap_or_default()
        })
        .collect()
}

impl<D: Desktop> Server<D> {
    pub fn new(desk: D) -> Self {
        let (w, _) = desk.main_display();
        let scale = if w > DEFAULT_SHOT_WIDTH as f64 { w / DEFAULT_SHOT_WIDTH as f64 } else { 1.0 };
        Server { desk, app: String::new(), elements: Vec::new(), contexts: Vec::new(), scale, ox: 0.0, oy: 0.0,
                 lessons: Store::new(lessons::default_dir()), skills: Skills::new(lessons::default_dir()),
                 stop_file: lessons::default_dir().join("stop-request"),
                 log: std::env::var_os("HOME").map(|h| std::path::Path::new(&h).join("Library/Logs/Computer Use/mcp.log")) }
    }

    /// Keep lessons somewhere else (tests).
    pub fn with_lessons_dir(mut self, dir: std::path::PathBuf) -> Self {
        self.lessons = Store::new(dir.clone());
        self.stop_file = dir.join("stop-request");
        self.skills = Skills::new(dir);
        self
    }

    /// Whether the user pressed Escape 3 times in the last STOP_SECS (the app records when).
    fn stop_requested(&self) -> bool {
        let Some(t) = std::fs::read_to_string(&self.stop_file).ok().and_then(|s| s.trim().parse::<f64>().ok()) else {
            return false;
        };
        let age = lessons::now() - t;
        (-1.0..STOP_SECS).contains(&age)
    }

    /// Write the call log somewhere else, or nowhere (None).
    pub fn with_log(mut self, path: Option<std::path::PathBuf>) -> Self {
        self.log = path;
        self
    }

    fn log_call(&self, name: &str, args: &Value, outcome: &str, ms: u128) {
        use std::io::Write as _;
        let Some(path) = &self.log else { return };
        if let Some(dir) = path.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        if std::fs::metadata(path).is_ok_and(|m| m.len() > MAX_LOG_BYTES) {
            let _ = std::fs::rename(path, path.with_extension("log.1"));
        }
        let secs = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
            let _ = writeln!(f, "{} {name} {} {outcome} {ms}ms", utc(secs), short_args(args));
        }
    }

    /// The places a call is about: the given app/site name(s) plus the frontmost app.
    fn places(&self, args: &Value) -> Vec<String> {
        let mut places: Vec<String> = match args.get("where") {
            // Plain text, comma-separated (strict clients reject "string or list"); a list still works.
            Some(Value::String(w)) => w.split(',').map(str::trim).filter(|x| !x.is_empty()).map(String::from).collect(),
            Some(Value::Array(ws)) => ws.iter().filter_map(Value::as_str).map(String::from).collect(),
            _ => Vec::new(),
        };
        places.push(self.desk.frontmost());
        places
    }

    pub fn desktop(&self) -> &D {
        &self.desk
    }

    /// Screenshot pixels -> global screen points.
    fn to_points(&self, x: f64, y: f64) -> (f64, f64) {
        (self.ox + x * self.scale, self.oy + y * self.scale)
    }

    /// Global screen points -> screenshot pixels, for output.
    fn to_px(&self, x: i32, y: i32) -> (i64, i64) {
        (((x as f64 - self.ox) / self.scale).round() as i64, ((y as f64 - self.oy) / self.scale).round() as i64)
    }

    /// A width or height in points -> screenshot pixels.
    fn to_px_len(&self, v: i32) -> i64 {
        (v as f64 / self.scale).round() as i64
    }

    fn describe(&self, i: usize) -> String {
        let e = &self.elements[i];
        let (x, y) = self.to_px(e.x, e.y);
        let ctx = self.contexts.get(i).filter(|c| !c.is_empty()).map(|c| format!(" (near '{c}')")).unwrap_or_default();
        let focus = if e.focused { " [focused]" } else { "" };
        format!("{} {} \"{}\"{ctx}{focus} at ({x},{y})", e.id, role_name(&e.role), e.label)
    }

    fn refresh(&mut self, limit: usize) {
        let (app, els) = self.desk.elements(limit.clamp(1, MAX_ELEMENTS));
        self.contexts = contexts(&els);
        self.app = app;
        self.elements = els;
    }

    /// Bring element `i`'s window forward if it's covered or in another app, so a click lands on it.
    /// "brought … forward, " to put before the reply, or "" if it was already in front.
    fn forward(&mut self, i: usize) -> Result<String, String> {
        Ok(self.desk.bring_forward(i)?.map(|m| format!("{m}, ")).unwrap_or_default())
    }

    /// Find an element by id from the latest list, or by description on a fresh list.
    fn resolve(&mut self, element: &str) -> Result<(usize, String), String> {
        let element = element.trim();
        let is_id = element.len() > 1 && element.starts_with('e') && element[1..].chars().all(|c| c.is_ascii_digit());
        if is_id {
            if let Some(i) = self.elements.iter().position(|e| e.id == element) {
                return Ok((i, "by id".into()));
            }
            return Err(format!("no element {element} in the latest list; call list_screen_elements again (ids change when the screen does)"));
        }
        self.refresh(MAX_ELEMENTS);
        match best_match(element, &self.elements) {
            // Two elements with the same role and label (e.g. two "Message input" boxes): don't guess.
            Some((i, _, Some(j)))
                if self.elements[i].role == self.elements[j].role && self.elements[i].label == self.elements[j].label =>
            {
                let same: Vec<String> = (0..self.elements.len())
                    .filter(|&k| self.elements[k].role == self.elements[i].role && self.elements[k].label == self.elements[i].label)
                    .map(|k| self.describe(k))
                    .collect();
                Err(format!("ambiguous: {} — pass one of these ids", same.join("; ")))
            }
            Some((i, score, close)) => {
                let mut how = format!("matched {score:.2}");
                if let Some(j) = close {
                    how.push_str(&format!("; also similar: {}", self.describe(j)));
                }
                Ok((i, how))
            }
            None => Err(format!(
                "no element matches {element:?} in {}; call list_screen_elements for ids, or use click_at with screenshot coordinates",
                self.app
            )),
        }
    }

    fn tools() -> Value {
        let s = |props: Value, required: &[&str]| json!({"type": "object", "properties": props, "required": required});
        let element = json!({"type": "string", "description": "An id from list_screen_elements (e.g. \"e12\") or a description (e.g. \"Send button\")."});
        let xy = |what: &str| json!({"type": "number", "description": format!("{what}, in pixels of the latest screenshot")});
        json!([
            {"name": "take_screenshot", "description": "Screenshot of the display showing the frontmost app's window (it follows the app across displays). Every x/y in these tools is a pixel of the latest screenshot.",
             "inputSchema": s(json!({"max_width": {"type": "integer", "description": "Widest image to return, default 1280 (smaller is cheaper)."}}), &[])},
            {"name": "list_screen_elements", "description": "An app's buttons, fields, links, menus and text in all its windows, from the accessibility tree, as lines of: id role \"label\" at (x,y). The frontmost app unless app names another; reading doesn't bring it forward, and clicking or typing into one of its elements does.",
             "inputSchema": s(json!({"limit": {"type": "integer", "description": "At most this many elements (default 300, max 600)."},
                                     "app": {"type": "string", "description": "Read this app instead of the frontmost one, e.g. \"Notes\" (see list_windows)."}}), &[])},
            {"name": "click_element", "description": "Click an element by id or description.",
             "inputSchema": s(json!({"element": element, "double": {"type": "boolean"}, "button": {"type": "string", "enum": ["left", "right", "middle"]}}), &["element"])},
            {"name": "click_at", "description": "Click at screenshot pixel coordinates. Use when an app has no useful elements.",
             "inputSchema": s(json!({"x": xy("x"), "y": xy("y"), "double": {"type": "boolean"}, "button": {"type": "string", "enum": ["left", "right", "middle"]}}), &["x", "y"])},
            {"name": "type_text", "description": "Type text (any language, emoji, newlines) into the focused field, or click `element` first. Doesn't touch the clipboard.",
             "inputSchema": s(json!({"text": {"type": "string"}, "element": element, "press_enter": {"type": "boolean"}, "clear_first": {"type": "boolean", "description": "Select all and delete before typing."}}), &["text"])},
            {"name": "press_keys", "description": "Press a key or shortcut, e.g. [\"enter\"], [\"command\", \"t\"], [\"command\", \"shift\", \"4\"], [\"pagedown\"], [\"f5\"].",
             "inputSchema": s(json!({"keys": {"type": "array", "items": {"type": "string"}}}), &["keys"])},
            {"name": "scroll", "description": "Scroll over an element, or the middle of the main display.",
             "inputSchema": s(json!({"direction": {"type": "string", "enum": ["up", "down", "left", "right"]}, "element": element, "amount": {"type": "integer", "description": "Lines to scroll, default 5."}}), &["direction"])},
            {"name": "drag", "description": "Drag with the left button from one point to another (screenshot pixels).",
             "inputSchema": s(json!({"from_x": xy("start x"), "from_y": xy("start y"), "to_x": xy("end x"), "to_y": xy("end y")}), &["from_x", "from_y", "to_x", "to_y"])},
            {"name": "move_mouse", "description": "Move the pointer, e.g. to reveal a hover menu.",
             "inputSchema": s(json!({"x": xy("x"), "y": xy("y")}), &["x", "y"])},
            {"name": "open_app", "description": "Launch or bring forward a Mac app by name, e.g. \"Notes\", \"Safari\", \"System Settings\".",
             "inputSchema": s(json!({"name": {"type": "string"}}), &["name"])},
            {"name": "open_url", "description": "Open a URL (web page, mailto:, app link) with its default app.",
             "inputSchema": s(json!({"url": {"type": "string"}}), &["url"])},
            {"name": "select_menu", "description": "Choose a menu-bar item in the frontmost app by path, e.g. \"File > New Note\" or \"Format > Font > Bold\". Lists the options if one isn't found.",
             "inputSchema": s(json!({"path": {"type": "string"}}), &["path"])},
            {"name": "read_element", "description": "The full text of an element (a document, message or field), which list_screen_elements shortens.",
             "inputSchema": s(json!({"element": element}), &["element"])},
            {"name": "list_windows", "description": "Every on-screen window across all apps, front to back, with its app, title and bounds.",
             "inputSchema": s(json!({}), &[])},
            {"name": "focus_window", "description": "Bring an app's window to the front (un-minimizing it if needed): the one whose title contains title, else its main window.",
             "inputSchema": s(json!({"app": {"type": "string", "description": "App name, e.g. \"Notes\"."},
                                     "title": {"type": "string", "description": "Part of the window's title."}}), &["app"])},
            {"name": "wait_for_change", "description": "Wait until the frontmost app's elements change (a page loads, a dialog opens), up to a timeout.",
             "inputSchema": s(json!({"timeout_seconds": {"type": "number", "description": "Default 5, max 30."}}), &[])},
            {"name": "speak", "description": "Say a short sentence out loud to the user in their chosen voice.",
             "inputSchema": s(json!({"text": {"type": "string"}}), &["text"])},
            {"name": "computer_task", "description": "Hand a whole task to the Computer Use app's own agent (needs its Jev or vision model key) instead of stepping yourself. Risky steps (send/buy/delete) are refused unless allow_risky. Returns its log.",
             "inputSchema": s(json!({"task": {"type": "string"}, "mode": {"type": "string", "enum": ["auto", "hybrid", "jev", "vision"], "description": "auto: best available; hybrid: a vision model plans, Jev clicks; jev: Jev only; vision: a vision model does everything"}, "allow_risky": {"type": "boolean"}}), &["task"])},
            {"name": "recall", "description": "Before starting on an app or site, read what earlier runs learned there (what failed and what worked).",
             "inputSchema": s(json!({"where": {"type": "string", "description": "App name or site (e.g. \"x.com\"); separate several with commas. The frontmost app is always included."},
                                     "task": {"type": "string", "description": "What you're about to do, to rank lessons."},
                                     "limit": {"type": "integer", "description": "Default 6, max 20."}}), &[])},
            {"name": "remember", "description": "After recovering from a mistake or finding a non-obvious way to do something, save it: where, what happened, and the fix, so the next run starts smarter.",
             "inputSchema": s(json!({"where": {"type": "string", "description": "App name or site, e.g. \"x.com\" (default: the frontmost app)."},
                                     "what": {"type": "string", "description": "What happened or went wrong."},
                                     "fix": {"type": "string", "description": "What worked, or what to try instead."},
                                     "worked": {"type": "boolean", "description": "True once the fix is confirmed."},
                                     "task": {"type": "string"}}), &["what", "fix"])},
            {"name": "skills", "description": "Step-by-step skills for this app or site that worked before. Check before starting a multi-step task.",
             "inputSchema": s(json!({"task": {"type": "string", "description": "What you're about to do (leave out to list every skill for the place)."},
                                     "where": {"type": "string", "description": "App name or site; separate several with commas. The frontmost app is always included."},
                                     "limit": {"type": "integer", "description": "Default 2, max 5."}}), &[])},
            {"name": "save_skill", "description": "Save a reusable, step-by-step way of doing something in an app or site, once it has worked, so later runs (any AI) can follow it.",
             "inputSchema": s(json!({"name": {"type": "string", "description": "Short, e.g. \"Post on X\"."},
                                     "where": {"type": "string", "description": "App name or site, e.g. \"x.com\" (default: the frontmost app)."},
                                     "steps": {"type": "array", "items": {"type": "string"}, "description": "One action per step, at most 20."},
                                     "when": {"type": "string", "description": "When to use it."},
                                     "notes": {"type": "array", "items": {"type": "string"}}}), &["name", "steps"])},
            {"name": "check_permissions", "description": "Whether this server can read the screen (Screen Recording) and control apps (Accessibility), and what's frontmost.",
             "inputSchema": s(json!({}), &[])},
        ])
    }

    fn call(&mut self, name: &str, args: &Value) -> ToolResult {
        if ACTING.contains(&name) && self.desk.mouse_in_corner() {
            return Ok(Reply::error(
                "Stopped: the mouse is in a screen corner, which is the user's emergency stop. Don't continue until the user says to.",
            ));
        }
        if ACTING.contains(&name) && self.stop_requested() {
            return Ok(Reply::error("Stopped: the user pressed Escape 3 times to stop. Don't continue until the user says to."));
        }
        match name {
            "take_screenshot" => {
                let w = args.get("max_width").and_then(Value::as_u64).unwrap_or(DEFAULT_SHOT_WIDTH as u64).clamp(320, 2560) as u32;
                let shot = self.desk.screenshot(w)?;
                (self.scale, self.ox, self.oy) = (shot.scale, shot.ox, shot.oy);
                let data = base64::engine::general_purpose::STANDARD.encode(&shot.png);
                Ok(Reply {
                    content: vec![
                        json!({"type": "image", "data": data, "mimeType": "image/png"}),
                        json!({"type": "text", "text": format!("{}x{} screenshot of the display showing {}", shot.width, shot.height, self.desk.frontmost())}),
                    ],
                    is_error: false,
                })
            }
            "list_screen_elements" => {
                let limit = args.get("limit").and_then(Value::as_u64).unwrap_or(300) as usize;
                let target = arg_str(args, "app");
                if let Err(e) = self.desk.set_target(target) {
                    return Ok(Reply::error(e));
                }
                self.refresh(limit);
                let front = self.desk.frontmost();
                let mut out = if front == self.app {
                    format!("frontmost app: {} ({} elements)\n", self.app, self.elements.len())
                } else {
                    format!("app: {} ({} elements; not in front, so clicking or typing into one brings its window forward)\n", self.app, self.elements.len())
                };
                for i in 0..self.elements.len() {
                    out.push_str(&self.describe(i));
                    out.push('\n');
                }
                if self.elements.is_empty() {
                    out.push_str("no elements: the app may not support accessibility, or this server lacks Accessibility permission (see check_permissions); use take_screenshot and click_at\n");
                }
                Ok(Reply::text(out))
            }
            "click_element" => {
                let (i, how) = self.resolve(req_str(args, "element")?)?;
                let moved = self.forward(i)?;
                let e = self.elements[i].clone();
                self.desk.click(e.x as f64, e.y as f64, button(args)?, if arg_bool(args, "double") { 2 } else { 1 });
                Ok(Reply::text(format!("{moved}clicked {} ({how})", self.describe(i))))
            }
            "click_at" => {
                let (x, y) = self.to_points(arg_f64(args, "x")?, arg_f64(args, "y")?);
                if !self.desk.on_screen(x, y) {
                    return Ok(Reply::error("that point is off every screen; use coordinates from the latest take_screenshot"));
                }
                self.desk.click(x, y, button(args)?, if arg_bool(args, "double") { 2 } else { 1 });
                Ok(Reply::text(format!("clicked ({}, {})", arg_f64(args, "x")?, arg_f64(args, "y")?)))
            }
            "type_text" => {
                let text = args.get("text").and_then(Value::as_str).ok_or("missing required argument \"text\"")?;
                if text.chars().count() > MAX_TEXT {
                    return Ok(Reply::error(format!("text is over {MAX_TEXT} characters; type it in parts")));
                }
                let mut target = String::new();
                if let Some(el) = arg_str(args, "element") {
                    let (i, _) = self.resolve(el)?;
                    let moved = self.forward(i)?;
                    let e = self.elements[i].clone();
                    self.desk.click(e.x as f64, e.y as f64, Button::Left, 1);
                    self.desk.sleep(Duration::from_millis(150));
                    target = format!(" into {}{}", self.describe(i), if moved.is_empty() { String::new() } else { format!(" ({})", moved.trim_end_matches(", ")) });
                }
                if arg_bool(args, "clear_first") {
                    self.desk.hotkey(&["command", "a"]);
                    self.desk.hotkey(&["delete"]);
                }
                self.desk.type_text(text);
                if arg_bool(args, "press_enter") {
                    self.desk.hotkey(&["return"]);
                }
                Ok(Reply::text(format!("typed {} characters{target}", text.chars().count())))
            }
            "press_keys" => {
                let keys: Vec<&str> = args.get("keys").and_then(Value::as_array).ok_or("missing required argument \"keys\"")?.iter().filter_map(Value::as_str).collect();
                if keys.is_empty() {
                    return Ok(Reply::error("keys is empty"));
                }
                if !self.desk.hotkey(&keys) {
                    return Ok(Reply::error(format!(
                        "didn't press anything: {keys:?} has an unknown key or two non-modifier keys. Use names like return, tab, escape, delete, up, pagedown, home, f5, a-z, 0-9, punctuation, with command/shift/option/control."
                    )));
                }
                Ok(Reply::text(format!("pressed {}", keys.join("+"))))
            }
            "scroll" => {
                let dir = req_str(args, "direction")?.to_lowercase();
                let n = args.get("amount").and_then(Value::as_i64).unwrap_or(5).clamp(1, 100) as i32;
                let (dy, dx) = match dir.as_str() {
                    "up" => (n, 0),
                    "down" => (-n, 0),
                    "left" => (0, n),
                    "right" => (0, -n),
                    d => return Ok(Reply::error(format!("unknown direction {d:?}"))),
                };
                let (x, y) = match arg_str(args, "element") {
                    Some(el) => {
                        let (i, _) = self.resolve(el)?;
                        self.forward(i)?;
                        (self.elements[i].x as f64, self.elements[i].y as f64)
                    }
                    None => {
                        let (w, h) = self.desk.main_display();
                        (w / 2.0, h / 2.0)
                    }
                };
                self.desk.scroll(x, y, dy, dx);
                Ok(Reply::text(format!("scrolled {dir} {n}")))
            }
            "drag" => {
                let from = self.to_points(arg_f64(args, "from_x")?, arg_f64(args, "from_y")?);
                let to = self.to_points(arg_f64(args, "to_x")?, arg_f64(args, "to_y")?);
                if !self.desk.on_screen(from.0, from.1) || !self.desk.on_screen(to.0, to.1) {
                    return Ok(Reply::error("a point is off every screen; use coordinates from the latest take_screenshot"));
                }
                self.desk.drag(from, to);
                Ok(Reply::text("dragged"))
            }
            "move_mouse" => {
                let (x, y) = self.to_points(arg_f64(args, "x")?, arg_f64(args, "y")?);
                self.desk.move_mouse(x, y);
                Ok(Reply::text("moved"))
            }
            "open_app" => Ok(match self.desk.open_app(req_str(args, "name")?) {
                Ok(msg) => Reply::text(msg),
                Err(e) => Reply::error(e),
            }),
            "open_url" => {
                let url = req_str(args, "url")?;
                if url.starts_with('-') || !url.contains(':') {
                    return Ok(Reply::error("give a full URL with a scheme, e.g. https://example.com"));
                }
                Ok(match self.desk.open_url(url) {
                    Ok(()) => Reply::text(format!("opened {url}")),
                    Err(e) => Reply::error(format!("couldn't open {url}: {e}")),
                })
            }
            "select_menu" => {
                let path: Vec<&str> = req_str(args, "path")?.split(['>', '›', '→']).map(str::trim).filter(|s| !s.is_empty()).collect();
                Ok(match self.desk.select_menu(&path) {
                    Ok(msg) => Reply::text(format!("chose {msg}")),
                    Err(e) => Reply::error(e),
                })
            }
            "read_element" => {
                let (i, _) = self.resolve(req_str(args, "element")?)?;
                Ok(match self.desk.read_element(i) {
                    Some(t) if !t.is_empty() => Reply::text(t),
                    _ => Reply::text(format!("{} has no text", self.describe(i))),
                })
            }
            "list_windows" => {
                let wins = self.desk.windows();
                let mut out = format!("{} windows, front to back\n", wins.len());
                for w in wins {
                    let (x, y) = self.to_px(w.x, w.y);
                    let (ww, wh) = (self.to_px_len(w.w), self.to_px_len(w.h));
                    let title = if w.title.is_empty() { String::new() } else { format!(" \"{}\"", truncate_chars(&w.title, 80)) };
                    out.push_str(&format!("{}{title} at ({x},{y}) size {ww}x{wh}\n", w.app));
                }
                Ok(Reply::text(out))
            }
            "focus_window" => Ok(match self.desk.focus_window(req_str(args, "app")?, arg_str(args, "title")) {
                Ok(msg) => Reply::text(msg),
                Err(e) => Reply::error(e),
            }),
            "wait_for_change" => {
                let timeout = args.get("timeout_seconds").and_then(Value::as_f64).unwrap_or(5.0).clamp(0.2, 30.0);
                let sig = |s: &Self| {
                    (s.app.clone(), s.elements.iter().take(80).map(|e| (e.role.clone(), e.label.clone(), e.x, e.y)).collect::<Vec<_>>())
                };
                self.refresh(MAX_ELEMENTS);
                let before = sig(self);
                let t0 = Instant::now();
                while t0.elapsed().as_secs_f64() < timeout {
                    self.desk.sleep(Duration::from_millis(300));
                    self.refresh(MAX_ELEMENTS);
                    if sig(self) != before {
                        return Ok(Reply::text(format!(
                            "changed after {:.1} s; frontmost app: {} ({} elements; ids refreshed)",
                            t0.elapsed().as_secs_f64(), self.app, self.elements.len()
                        )));
                    }
                }
                Ok(Reply::text(format!("no change after {timeout:.1} s; frontmost app: {}", self.app)))
            }
            "speak" => {
                let text = req_str(args, "text")?;
                Ok(match self.desk.speak(&truncate_chars(text, 500)) {
                    Ok(()) => Reply::text("spoken"),
                    Err(e) => Reply::error(format!("couldn't speak: {e}")),
                })
            }
            "computer_task" => {
                let mode = match arg_str(args, "mode").unwrap_or("auto") {
                    "gemini" => "vision", // the old name
                    m => m,
                };
                if !["auto", "hybrid", "jev", "vision"].contains(&mode) {
                    return Ok(Reply::error(format!("unknown mode {mode:?}")));
                }
                Ok(match self.desk.run_task(req_str(args, "task")?, mode, arg_bool(args, "allow_risky")) {
                    Ok(log) => Reply::text(log),
                    Err(e) => Reply::error(e),
                })
            }
            "skills" => {
                let limit = args.get("limit").and_then(Value::as_u64).unwrap_or(2) as usize;
                let found = self.skills.find(arg_str(args, "task").unwrap_or(""), &self.places(args), limit)?;
                Ok(Reply::text(if found.is_empty() {
                    "No skills match. If you work out a reusable way, call save_skill.".to_string()
                } else {
                    found.join("\n\n")
                }))
            }
            "save_skill" => {
                let list = |k: &str| -> Vec<String> {
                    args.get(k).and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str).map(String::from).collect()
                };
                let front = self.desk.frontmost();
                let place = arg_str(args, "where").unwrap_or(&front).to_string();
                Ok(match self.skills.save(req_str(args, "name")?, &place, arg_str(args, "when").unwrap_or(""), &list("steps"), &list("notes")) {
                    Ok(msg) => Reply::text(msg),
                    Err(e) => Reply::error(e),
                })
            }
            "recall" => {
                let places = self.places(args);
                let limit = args.get("limit").and_then(Value::as_u64).unwrap_or(6) as usize;
                let found = self.lessons.recall(&places, arg_str(args, "task").unwrap_or(""), limit)?;
                Ok(Reply::text(if found.is_empty() {
                    format!("No lessons yet for {}. After you work something out, call remember.", places.join(", ").to_lowercase())
                } else {
                    found.join("\n")
                }))
            }
            "remember" => {
                let front = self.desk.frontmost();
                let place = arg_str(args, "where").unwrap_or(&front).to_string();
                let seen = self.lessons.remember(&place, arg_str(args, "task").unwrap_or(""), req_str(args, "what")?,
                                                 req_str(args, "fix")?, arg_bool(args, "worked"), "mcp client")?;
                Ok(Reply::text(format!("remembered for {} (seen {seen} time{})", place.to_lowercase(), if seen == 1 { "" } else { "s" })))
            }
            "check_permissions" => {
                let (ax, screen) = self.desk.permissions();
                let mark = |b: bool| if b { "yes" } else { "NO" };
                Ok(Reply::text(format!(
                    "Accessibility (control apps, read elements): {}\nScreen Recording (screenshots, window titles): {}\nfrontmost app: {}\n\
                     Permissions belong to the app that started this server (your editor or AI app): add it in System Settings › Privacy & Security.",
                    mark(ax), mark(screen), self.desk.frontmost()
                )))
            }
            _ => Err(format!("unknown tool {name:?}")),
        }
    }

    /// Handle one JSON-RPC message; returns the response to send, if any.
    pub fn handle(&mut self, msg: &Value) -> Option<Value> {
        let id = msg.get("id").cloned();
        let method = msg.get("method").and_then(Value::as_str);
        let Some(method) = method else {
            // A response to something we never sent, or junk: answer only if it had an id.
            return id.map(|id| error(id, -32600, "invalid request: no method"));
        };
        let id = id?; // notifications (initialized, cancelled, ...) get no reply
        let params = msg.get("params").cloned().unwrap_or(Value::Null);
        let result = match method {
            "initialize" => {
                let asked = params.get("protocolVersion").and_then(Value::as_str).unwrap_or("");
                let version = PROTOCOL_VERSIONS.iter().find(|v| **v == asked).unwrap_or(&PROTOCOL_VERSIONS[1]);
                json!({
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": false}},
                    "serverInfo": {"name": SERVER_NAME, "version": env!("CARGO_PKG_VERSION")},
                    "instructions": INSTRUCTIONS,
                })
            }
            "ping" => json!({}),
            "tools/list" => json!({"tools": Self::tools()}),
            "tools/call" => {
                let name = params.get("name").and_then(Value::as_str).unwrap_or("");
                let args = params.get("arguments").cloned().unwrap_or_else(|| json!({}));
                let t0 = Instant::now();
                let reply = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| self.call(name, &args)))
                    .unwrap_or_else(|_| Err(format!("internal error in {name}")));
                let outcome = match &reply {
                    Ok(r) if !r.is_error => "ok".to_string(),
                    Ok(r) => format!("error: {}", truncate_chars(r.content.first().and_then(|c| c["text"].as_str()).unwrap_or(""), 120)),
                    Err(e) => format!("error: {}", truncate_chars(e, 120)),
                };
                self.log_call(name, &args, &outcome.replace('\n', " "), t0.elapsed().as_millis());
                match reply {
                    Ok(r) => json!({"content": r.content, "isError": r.is_error}),
                    Err(e) if e.starts_with("unknown tool") => return Some(error(id, -32602, &e)),
                    Err(e) => json!({"content": [{"type": "text", "text": e}], "isError": true}),
                }
            }
            "resources/list" => json!({"resources": []}),
            "prompts/list" => json!({"prompts": []}),
            _ => return Some(error(id, -32601, &format!("method not found: {method}"))),
        };
        Some(json!({"jsonrpc": "2.0", "id": id, "result": result}))
    }

    /// Serve until stdin closes. One request at a time; memory stays bounded by the latest
    /// element list and the longest line accepted.
    pub fn serve(&mut self, input: impl BufRead, mut output: impl Write) -> std::io::Result<()> {
        let mut input = input;
        let mut line = Vec::new();
        loop {
            line.clear();
            let n = read_line_capped(&mut input, &mut line, MAX_LINE)?;
            if n == 0 {
                return Ok(());
            }
            if line.len() > MAX_LINE {
                writeln!(output, "{}", error(Value::Null, -32600, "request too large"))?;
                output.flush()?;
                continue;
            }
            let text = String::from_utf8_lossy(&line);
            if text.trim().is_empty() {
                continue;
            }
            let reply = match serde_json::from_str::<Value>(&text) {
                Ok(Value::Array(_)) => Some(error(Value::Null, -32600, "batches are not supported")),
                Ok(msg) => self.handle(&msg),
                Err(e) => Some(error(Value::Null, -32700, &format!("parse error: {e}"))),
            };
            if let Some(r) = reply {
                writeln!(output, "{r}")?;
                output.flush()?;
            }
        }
    }
}

fn error(id: Value, code: i64, message: &str) -> Value {
    json!({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}})
}

/// Read one line; bytes past `cap` are consumed but not stored (so `buf.len() == cap + 1`
/// marks an over-long line). Returns bytes read, 0 at EOF.
fn read_line_capped(input: &mut impl BufRead, buf: &mut Vec<u8>, cap: usize) -> std::io::Result<usize> {
    let mut total = 0;
    loop {
        let chunk = input.fill_buf()?;
        if chunk.is_empty() {
            return Ok(total);
        }
        let (take, done) = match chunk.iter().position(|&b| b == b'\n') {
            Some(i) => (i + 1, true),
            None => (chunk.len(), false),
        };
        let room = (cap + 1).saturating_sub(buf.len());
        buf.extend_from_slice(&chunk[..take.min(room)]);
        input.consume(take);
        total += take;
        if done {
            if buf.last() == Some(&b'\n') {
                buf.pop();
            }
            return Ok(total);
        }
    }
}

#[cfg(test)]
mod tests;
