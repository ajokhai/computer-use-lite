//! The MCP server against a fake desktop: protocol, every tool, and the safety rules.
//! Nothing here touches the real Mac.

use std::time::Duration;

use serde_json::{json, Value};

use super::*;
use crate::ax::WindowInfo;
use crate::capture::Shot;

#[derive(Default)]
struct Fake {
    app: String,
    els: Vec<NativeElement>,
    texts: Vec<String>,
    log: Vec<String>,
    corner: bool,
    hotkey_ok: bool,
    /// Elements to switch to after this many `elements()` calls (for wait_for_change).
    later: Option<(usize, Vec<NativeElement>)>,
    walks: usize,
    /// Where the captured display sits in global points (a second display can be negative).
    shot_origin: (f64, f64),
    /// Another app with windows behind the frontmost one, and whether it's being read.
    other: Option<(String, Vec<NativeElement>)>,
    reading_other: bool,
    /// Indexes of elements whose window is covered by another window of the same app.
    covered: Vec<usize>,
}

fn el(id: &str, role: &str, label: &str, x: i32, y: i32) -> NativeElement {
    NativeElement { id: id.into(), role: role.into(), label: label.into(), x, y, w: 40, h: 20, focused: false, win: 0 }
}

fn fake() -> Fake {
    Fake {
        app: "Mail".into(),
        els: vec![
            el("e0", "AXButton", "Send", 100, 50),
            el("e1", "AXTextField", "To:", 300, 80),
            el("e2", "AXTextArea", "Hi Bob, the report is attached.", 300, 300),
            el("e3", "AXButton", "Reply", 500, 200),
            el("e4", "AXStaticText", "Quarterly numbers", 500, 170),
        ],
        texts: vec!["Send".into(), "".into(), "Hi Bob, the report is attached. Full text here.".into(), "Reply".into(), "Quarterly numbers".into()],
        hotkey_ok: true,
        ..Default::default()
    }
}

impl Desktop for Fake {
    fn main_display(&self) -> (f64, f64) {
        (2560.0, 1600.0) // wider than the default screenshot: scale 2
    }
    fn on_screen(&self, x: f64, y: f64) -> bool {
        (-2560.0..2560.0).contains(&x) && (-1600.0..1600.0).contains(&y)
    }
    fn elements(&mut self, limit: usize) -> (String, Vec<NativeElement>) {
        self.walks += 1;
        if let Some((after, next)) = &self.later {
            if self.walks > *after {
                self.els = next.clone();
            }
        }
        if let (true, Some((name, els))) = (self.reading_other, &self.other) {
            return (name.clone(), els.iter().take(limit).cloned().collect());
        }
        (self.app.clone(), self.els.iter().take(limit).cloned().collect())
    }
    fn set_target(&mut self, app: Option<&str>) -> Result<String, String> {
        match app {
            None => self.reading_other = false,
            Some(a) if self.other.as_ref().is_some_and(|(n, _)| n.eq_ignore_ascii_case(a)) => self.reading_other = true,
            Some(a) => return Err(format!("no app named {a:?} has a window on screen")),
        }
        Ok(if self.reading_other { self.other.as_ref().unwrap().0.clone() } else { self.app.clone() })
    }
    fn bring_forward(&mut self, index: usize) -> Result<Option<String>, String> {
        if self.reading_other {
            // The other app comes to the front; the old frontmost app goes behind it.
            let (name, els) = self.other.take().unwrap();
            self.other = Some((std::mem::replace(&mut self.app, name.clone()), std::mem::replace(&mut self.els, els)));
            self.reading_other = false;
            self.log.push(format!("raise {name}"));
            return Ok(Some(format!("brought {name} forward")));
        }
        if let Some(pos) = self.covered.iter().position(|&i| i == index) {
            self.covered.remove(pos);
            self.log.push(format!("raise window of e{index}"));
            return Ok(Some("brought the window \"Draft\" forward".into()));
        }
        Ok(None)
    }
    fn focus_window(&mut self, app: &str, title: Option<&str>) -> Result<String, String> {
        self.log.push(format!("focus {app} {title:?}"));
        Ok(format!("{app} is in front"))
    }
    fn read_element(&mut self, index: usize) -> Option<String> {
        self.texts.get(index).cloned()
    }
    fn screenshot(&mut self, max_width: u32) -> Result<Shot, String> {
        let w = max_width.min(2560);
        Ok(Shot { png: vec![0x89, b'P', b'N', b'G'], width: w, height: w * 10 / 16, scale: 2560.0 / w as f64, ox: self.shot_origin.0, oy: self.shot_origin.1 })
    }
    fn click(&mut self, x: f64, y: f64, button: Button, clicks: usize) {
        self.log.push(format!("click {x},{y} {button:?} x{clicks}"));
    }
    fn move_mouse(&mut self, x: f64, y: f64) {
        self.log.push(format!("move {x},{y}"));
    }
    fn drag(&mut self, from: (f64, f64), to: (f64, f64)) {
        self.log.push(format!("drag {from:?} {to:?}"));
    }
    fn scroll(&mut self, x: f64, y: f64, dy: i32, dx: i32) {
        self.log.push(format!("scroll {x},{y} {dy} {dx}"));
    }
    fn type_text(&mut self, text: &str) {
        self.log.push(format!("type {text}"));
    }
    fn hotkey(&mut self, keys: &[&str]) -> bool {
        self.log.push(format!("keys {}", keys.join("+")));
        self.hotkey_ok
    }
    fn open_app(&mut self, name: &str) -> Result<String, String> {
        if name == "Nope" { Err("no app named \"Nope\"".into()) } else { Ok(format!("opened {name}")) }
    }
    fn open_url(&mut self, url: &str) -> Result<(), String> {
        self.log.push(format!("url {url}"));
        Ok(())
    }
    fn select_menu(&mut self, path: &[&str]) -> Result<String, String> {
        self.log.push(format!("menu {}", path.join("|")));
        Ok(path.join(" > "))
    }
    fn windows(&mut self) -> Vec<WindowInfo> {
        vec![WindowInfo { app: "Mail".into(), pid: 1, title: "Inbox".into(), x: 0, y: 0, w: 2000, h: 1200 }]
    }
    fn mouse_in_corner(&self) -> bool {
        self.corner
    }
    fn frontmost(&self) -> String {
        self.app.clone()
    }
    fn sleep(&mut self, _d: Duration) {}
    fn speak(&mut self, text: &str) -> Result<(), String> {
        self.log.push(format!("say {text}"));
        Ok(())
    }
    fn run_task(&mut self, task: &str, mode: &str, allow_risky: bool) -> Result<String, String> {
        Ok(format!("ran {task:?} mode={mode} risky={allow_risky}"))
    }
    fn permissions(&self) -> (bool, bool) {
        (true, false)
    }
}

fn call(s: &mut Server<Fake>, name: &str, args: Value) -> (String, bool, Value) {
    let r = s.handle(&json!({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": name, "arguments": args}})).unwrap();
    let res = r["result"].clone();
    let text = res["content"].as_array().unwrap().iter().filter_map(|c| c["text"].as_str()).collect::<Vec<_>>().join("\n");
    (text, res["isError"].as_bool().unwrap(), res)
}

fn server() -> Server<Fake> {
    let dir = std::env::temp_dir().join(format!("cu-mcp-test-{}-{:?}", std::process::id(), std::thread::current().id()));
    let _ = std::fs::remove_dir_all(&dir);
    Server::new(fake()).with_lessons_dir(dir).with_log(None)
}

#[test]
fn initialize_negotiates_version_and_lists_tools() {
    let mut s = server();
    let r = s.handle(&json!({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t"}}})).unwrap();
    assert_eq!(r["result"]["protocolVersion"], "2025-03-26");
    assert_eq!(r["result"]["serverInfo"]["name"], SERVER_NAME);
    assert!(r["result"]["capabilities"]["tools"].is_object());
    let r = s.handle(&json!({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})).unwrap();
    assert!(PROTOCOL_VERSIONS.contains(&r["result"]["protocolVersion"].as_str().unwrap()));

    assert!(s.handle(&json!({"jsonrpc": "2.0", "method": "notifications/initialized"})).is_none());
    let r = s.handle(&json!({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})).unwrap();
    let tools = r["result"]["tools"].as_array().unwrap();
    let names: Vec<&str> = tools.iter().map(|t| t["name"].as_str().unwrap()).collect();
    for want in ["take_screenshot", "list_screen_elements", "click_element", "click_at", "type_text", "press_keys",
                 "scroll", "drag", "move_mouse", "open_app", "open_url", "select_menu", "read_element",
                 "list_windows", "wait_for_change", "speak", "computer_task", "check_permissions", "recall", "remember", "skills", "save_skill"] {
        assert!(names.contains(&want), "missing {want}");
    }
    for t in tools {
        assert_eq!(t["inputSchema"]["type"], "object", "{}", t["name"]);
        // Every property has a plain type: strict clients (Gemini's function calling) reject anyOf.
        for (k, v) in t["inputSchema"]["properties"].as_object().unwrap() {
            assert!(v.get("type").is_some() && v.get("anyOf").is_none(), "{}.{k} needs a plain type", t["name"]);
        }
        for req in t["inputSchema"]["required"].as_array().unwrap() {
            assert!(t["inputSchema"]["properties"].get(req.as_str().unwrap()).is_some(), "{} requires undeclared {req}", t["name"]);
        }
        assert!(t["description"].as_str().unwrap().len() > 10);
    }
}

#[test]
fn protocol_errors() {
    let mut s = server();
    let r = s.handle(&json!({"jsonrpc": "2.0", "id": 3, "method": "nope"})).unwrap();
    assert_eq!(r["error"]["code"], -32601);
    let r = s.handle(&json!({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "nope"}})).unwrap();
    assert_eq!(r["error"]["code"], -32602);
    assert_eq!(s.handle(&json!({"jsonrpc": "2.0", "id": 5, "method": "ping"})).unwrap()["result"], json!({}));

    let input = b"not json\n\n[1,2]\n{\"jsonrpc\":\"2.0\",\"id\":9,\"method\":\"ping\"}\n";
    let mut out = Vec::new();
    s.serve(&input[..], &mut out).unwrap();
    let lines: Vec<Value> = String::from_utf8(out).unwrap().lines().map(|l| serde_json::from_str(l).unwrap()).collect();
    assert_eq!(lines.len(), 3);
    assert_eq!(lines[0]["error"]["code"], -32700);
    assert_eq!(lines[1]["error"]["code"], -32600);
    assert_eq!(lines[2]["id"], 9);
}

#[test]
fn oversized_requests_are_dropped_not_buffered() {
    let mut s = server();
    let mut input = vec![b'x'; MAX_LINE + 10];
    input.extend_from_slice(b"\n{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"ping\"}\n");
    let mut out = Vec::new();
    s.serve(&input[..], &mut out).unwrap();
    let out = String::from_utf8(out).unwrap();
    let lines: Vec<&str> = out.lines().collect();
    assert_eq!(lines.len(), 2);
    assert!(lines[0].contains("too large"));
    assert!(lines[1].contains("\"id\":1"));
}

#[test]
fn coordinates_are_screenshot_pixels_both_ways() {
    let mut s = server(); // 2560-point display, 1280-pixel default screenshot: scale 2
    let (text, err, _) = call(&mut s, "list_screen_elements", json!({}));
    assert!(!err);
    assert!(text.contains("e0 Button \"Send\" at (50,25)"), "{text}");
    call(&mut s, "click_at", json!({"x": 50, "y": 25}));
    assert_eq!(s.desk.log.last().unwrap(), "click 100,50 Left x1");

    let (_, _, res) = call(&mut s, "take_screenshot", json!({"max_width": 640}));
    assert_eq!(res["content"][0]["type"], "image");
    assert_eq!(res["content"][0]["mimeType"], "image/png");
    call(&mut s, "click_at", json!({"x": 10, "y": 10, "double": true, "button": "right"}));
    assert_eq!(s.desk.log.last().unwrap(), "click 40,40 Right x2");
}

#[test]
fn click_element_by_id_and_description() {
    let mut s = server();
    call(&mut s, "list_screen_elements", json!({}));
    let (text, err, _) = call(&mut s, "click_element", json!({"element": "e3"}));
    assert!(!err && text.contains("Reply"), "{text}");
    assert_eq!(s.desk.log.last().unwrap(), "click 500,200 Left x1");

    let (text, err, _) = call(&mut s, "click_element", json!({"element": "the Send button"}));
    assert!(!err, "{text}");
    assert_eq!(s.desk.log.last().unwrap(), "click 100,50 Left x1");

    let (text, err, _) = call(&mut s, "click_element", json!({"element": "e99"}));
    assert!(err && text.contains("list_screen_elements"), "{text}");
    let (text, err, _) = call(&mut s, "click_element", json!({"element": "Purchase subscription"}));
    assert!(err && text.contains("no element matches"), "{text}");
}

#[test]
fn context_disambiguates_short_labels() {
    let mut s = server();
    let (text, _, _) = call(&mut s, "list_screen_elements", json!({}));
    assert!(text.contains("Reply\" (near 'Quarterly numbers')"), "{text}");
}

#[test]
fn match_scores_rank_sensibly() {
    let els = fake().els;
    assert_eq!(best_match("Send", &els).unwrap().0, 0);
    assert_eq!(best_match("send button", &els).unwrap().0, 0);
    assert_eq!(best_match("To field", &els).unwrap().0, 1);
    assert_eq!(best_match("reply", &els).unwrap().0, 3);
    assert!(best_match("zzzz", &els).is_none());
    assert!(similarity("kitten", "sitting") > 0.5 && similarity("abc", "xyz") == 0.0);
}

#[test]
fn type_text_focuses_clears_and_submits() {
    let mut s = server();
    let (text, err, _) = call(&mut s, "type_text", json!({"text": "Grüße 🎉\nline two", "element": "To:", "clear_first": true, "press_enter": true}));
    assert!(!err, "{text}");
    assert_eq!(s.desk.log, vec!["click 300,80 Left x1", "keys command+a", "keys delete", "type Grüße 🎉\nline two", "keys return"]);
    let (text, err, _) = call(&mut s, "type_text", json!({"text": "x".repeat(MAX_TEXT + 1)}));
    assert!(err && text.contains("in parts"));
}

#[test]
fn unknown_keys_are_reported_not_guessed() {
    let mut s = server();
    s.desk.hotkey_ok = false;
    let (text, err, _) = call(&mut s, "press_keys", json!({"keys": ["command", "mystery"]}));
    assert!(err && text.contains("didn't press anything"), "{text}");
    let (_, err, _) = call(&mut s, "press_keys", json!({"keys": []}));
    assert!(err);
}

#[test]
fn emergency_stop_blocks_every_acting_tool() {
    let mut s = server();
    s.desk.corner = true;
    for (name, args) in [
        ("click_element", json!({"element": "Send"})),
        ("click_at", json!({"x": 1, "y": 1})),
        ("type_text", json!({"text": "hi"})),
        ("press_keys", json!({"keys": ["return"]})),
        ("scroll", json!({"direction": "down"})),
        ("drag", json!({"from_x": 1, "from_y": 1, "to_x": 2, "to_y": 2})),
        ("move_mouse", json!({"x": 1, "y": 1})),
        ("open_app", json!({"name": "Notes"})),
        ("open_url", json!({"url": "https://example.com"})),
        ("select_menu", json!({"path": "File > New"})),
        ("computer_task", json!({"task": "x"})),
    ] {
        let (text, err, _) = call(&mut s, name, args);
        assert!(err && text.contains("emergency stop"), "{name}: {text}");
    }
    assert!(s.desk.log.is_empty(), "{:?}", s.desk.log);
    // Looking is still allowed.
    let (_, err, _) = call(&mut s, "list_screen_elements", json!({}));
    assert!(!err);
}

#[test]
fn off_screen_points_are_refused() {
    let mut s = server();
    let (text, err, _) = call(&mut s, "click_at", json!({"x": 9000, "y": 10}));
    assert!(err && text.contains("off every screen"));
    assert!(s.desk.log.is_empty());
}

#[test]
fn menus_scroll_drag_urls_and_apps() {
    let mut s = server();
    let (_, err, _) = call(&mut s, "select_menu", json!({"path": "Format › Font > Bold"}));
    assert!(!err);
    assert_eq!(s.desk.log.last().unwrap(), "menu Format|Font|Bold");

    call(&mut s, "scroll", json!({"direction": "down", "amount": 3}));
    assert_eq!(s.desk.log.last().unwrap(), "scroll 1280,800 -3 0");
    call(&mut s, "drag", json!({"from_x": 10, "from_y": 20, "to_x": 30, "to_y": 40}));
    assert_eq!(s.desk.log.last().unwrap(), "drag (20.0, 40.0) (60.0, 80.0)");

    let (_, err, _) = call(&mut s, "open_url", json!({"url": "-a Terminal"}));
    assert!(err);
    let (_, err, _) = call(&mut s, "open_url", json!({"url": "https://example.com"}));
    assert!(!err);
    let (text, err, _) = call(&mut s, "open_app", json!({"name": "Nope"}));
    assert!(err && text.contains("no app"));
}

#[test]
fn read_element_returns_full_text() {
    let mut s = server();
    let (text, err, _) = call(&mut s, "read_element", json!({"element": "report is attached"}));
    assert!(!err);
    assert!(text.contains("Full text here"), "{text}");
}

#[test]
fn wait_for_change_notices_new_elements() {
    let mut s = server();
    s.desk.later = Some((3, vec![el("e0", "AXButton", "OK", 10, 10)]));
    let (text, err, _) = call(&mut s, "wait_for_change", json!({"timeout_seconds": 5}));
    assert!(!err && text.starts_with("changed"), "{text}");
    let mut s = server();
    let (text, _, _) = call(&mut s, "wait_for_change", json!({"timeout_seconds": 0.5}));
    assert!(text.starts_with("no change"), "{text}");
}

#[test]
fn windows_tasks_speech_permissions() {
    let mut s = server();
    let (text, _, _) = call(&mut s, "list_windows", json!({}));
    assert!(text.contains("Mail \"Inbox\" at (0,0) size 1000x600"), "{text}");
    let (text, err, _) = call(&mut s, "computer_task", json!({"task": "tidy desktop", "mode": "jev"}));
    assert!(!err && text.contains("mode=jev risky=false"), "{text}");
    let (_, err, _) = call(&mut s, "computer_task", json!({"task": "x", "mode": "wild"}));
    assert!(err);
    let (text, _, _) = call(&mut s, "computer_task", json!({"task": "x", "mode": "gemini"}));
    assert!(text.contains("mode=vision"), "old mode name maps to vision: {text}");
    call(&mut s, "speak", json!({"text": "on it"}));
    assert_eq!(s.desk.log.last().unwrap(), "say on it");
    let (text, _, _) = call(&mut s, "check_permissions", json!({}));
    assert!(text.contains("Screen Recording (screenshots, window titles): NO"), "{text}");
}

#[test]
fn missing_arguments_are_tool_errors_not_crashes() {
    let mut s = server();
    for name in ["click_element", "click_at", "type_text", "press_keys", "drag", "open_app", "select_menu", "read_element", "speak", "computer_task"] {
        let (text, err, _) = call(&mut s, name, json!({}));
        assert!(err, "{name}: {text}");
    }
}

#[test]
fn screenshots_of_another_display_map_through_its_origin() {
    let mut s = server();
    s.desk.shot_origin = (-2560.0, -200.0);
    call(&mut s, "take_screenshot", json!({"max_width": 1280}));
    call(&mut s, "click_at", json!({"x": 100, "y": 50}));
    assert_eq!(s.desk.log.last().unwrap(), "click -2360,-100 Left x1");
    let (text, _, _) = call(&mut s, "list_screen_elements", json!({}));
    assert!(text.contains("e0 Button \"Send\" at (1330,125)"), "{text}");
}

#[test]
fn an_exact_name_beats_nearby_text() {
    let els = vec![el("e10", "AXButton", "Next", 10, 10), el("e12", "AXButton", "Post", 50, 10)];
    assert_eq!(best_match("Button \"Post\" (near Next)", &els).unwrap().0, 1);
    assert_eq!(best_match("post", &els).unwrap().0, 1);
}

#[test]
fn lessons_round_trip_through_the_tools() {
    let mut s = server();
    let (text, _, _) = call(&mut s, "recall", json!({"where": "x.com"}));
    assert!(text.starts_with("No lessons yet for x.com, mail"), "{text}");
    let (text, err, _) = call(&mut s, "remember", json!({"where": "x.com", "what": "Post clicked Next", "fix": "click the button labelled Post", "worked": true}));
    assert!(!err && text == "remembered for x.com (seen 1 time)", "{text}");
    let (text, _, _) = call(&mut s, "recall", json!({"where": "notes, X.com", "task": "post a tweet"}));
    assert_eq!(text, "- On x.com: Post clicked Next → click the button labelled Post (worked)");
    let (_, err, _) = call(&mut s, "remember", json!({"what": "no fix"}));
    assert!(err);
}

#[test]
fn skills_round_trip_through_the_tools() {
    let mut s = server();
    let (text, _, _) = call(&mut s, "skills", json!({"task": "post a tweet"}));
    assert!(text.starts_with("No skills match"), "{text}");
    let (text, err, _) = call(&mut s, "save_skill", json!({"name": "Post on X", "where": "x.com", "steps": ["Click the compose box", "Type", "Click Post"]}));
    assert!(!err && text == "saved skill \"Post on X\" for x.com", "{text}");
    let (text, _, _) = call(&mut s, "skills", json!({"task": "post on x", "where": "x.com"}));
    assert!(text.starts_with("Skill \"Post on X\" (x.com):\n  1. Click the compose box"), "{text}");
    let (text, err, _) = call(&mut s, "skills", json!({"where": "x.com"}));
    assert!(!err && text.starts_with("Skill \"Post on X\""), "no task lists the place's skills: {text}");
    let (_, err, _) = call(&mut s, "save_skill", json!({"name": "Empty", "steps": []}));
    assert!(err);
}

#[test]
fn every_tool_call_is_logged_with_its_outcome_and_time() {
    let path = std::env::temp_dir().join(format!("cu-mcp-log-{}.log", std::process::id()));
    let _ = std::fs::remove_file(&path);
    let mut s = server().with_log(Some(path.clone()));
    call(&mut s, "type_text", json!({"text": "x".repeat(200)}));
    call(&mut s, "click_element", json!({"element": "e99"}));
    let log = std::fs::read_to_string(&path).unwrap();
    let lines: Vec<&str> = log.lines().collect();
    assert_eq!(lines.len(), 2, "{log}");
    assert!(lines[0].contains(" type_text {text: ") && lines[0].contains(" ok ") && lines[0].ends_with("ms"), "{}", lines[0]);
    assert!(lines[0].len() < 140, "typed text is shortened in the log");
    assert!(lines[1].contains(" click_element ") && lines[1].contains("error: no element e99"), "{}", lines[1]);
    assert_eq!(utc(0), "1970-01-01 00:00:00");
    assert_eq!(utc(1_792_812_345), "2026-10-24 03:25:45");
    let _ = std::fs::remove_file(&path);
}

#[test]
fn a_written_role_beats_text_that_quotes_the_label() {
    let els = vec![
        el("e0", "AXStaticText", "I typed into TextArea \"Message input\" earlier", 10, 10),
        el("e1", "AXTextArea", "Message input", 50, 300),
    ];
    assert_eq!(best_match("TextArea \"Message input\"", &els).unwrap().0, 1);
}

#[test]
fn two_identical_fields_are_reported_not_guessed() {
    let mut s = server();
    s.desk.els = vec![el("e0", "AXTextArea", "Message input", 100, 300), el("e1", "AXTextArea", "Message input", 900, 700)];
    let (text, err, _) = call(&mut s, "click_element", json!({"element": "TextArea \"Message input\""}));
    assert!(err && text.starts_with("ambiguous: e0 TextArea \"Message input\" at (50,150); e1"), "{text}");
    assert!(s.desk.log.is_empty());
    let (_, err, _) = call(&mut s, "click_element", json!({"element": "e1"}));
    assert!(!err, "an id picks one");
}

#[test]
fn escape_three_times_stops_acting_tools_for_a_while() {
    let dir = std::env::temp_dir().join(format!("cu-stop-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let mut s = Server::new(fake()).with_lessons_dir(dir.clone()).with_log(None);
    let stop = |age: f64| std::fs::write(dir.join("stop-request"), (crate::lessons::now() - age).to_string()).unwrap();
    stop(1.0);
    let (text, err, _) = call(&mut s, "click_at", json!({"x": 1, "y": 1}));
    assert!(err && text.contains("Escape 3 times"), "{text}");
    let (_, err, _) = call(&mut s, "check_permissions", json!({}));
    assert!(!err, "reading is still allowed");
    stop(STOP_SECS + 1.0);
    let (_, err, _) = call(&mut s, "click_at", json!({"x": 1, "y": 1}));
    assert!(!err, "the stop wears off");
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn a_click_on_a_covered_window_brings_it_forward_first() {
    let mut s = server();
    s.desk.covered = vec![3];
    call(&mut s, "list_screen_elements", json!({}));
    let (text, err, _) = call(&mut s, "click_element", json!({"element": "e3"}));
    assert!(!err && text.starts_with("brought the window \"Draft\" forward, clicked"), "{text}");
    assert_eq!(s.desk.log, vec!["raise window of e3", "click 500,200 Left x1"], "raised before the click");
    let (text, _, _) = call(&mut s, "click_element", json!({"element": "e0"}));
    assert!(text.starts_with("clicked"), "an element already in front isn't raised: {text}");
}

#[test]
fn another_apps_windows_can_be_read_and_used() {
    let mut s = server();
    s.desk.other = Some(("Notes".into(), vec![el("e0", "AXTextArea", "Shopping list", 900, 400), el("e1", "AXButton", "New Note", 800, 60)]));
    let (text, err, _) = call(&mut s, "list_screen_elements", json!({"app": "notes"}));
    assert!(!err && text.starts_with("app: Notes (2 elements; not in front"), "{text}");
    assert!(s.desk.log.is_empty(), "reading doesn't bring it forward");
    let (text, err, _) = call(&mut s, "type_text", json!({"text": "eggs", "element": "Shopping list"}));
    assert!(!err && text.contains("into e0 TextArea") && text.contains("brought Notes forward"), "{text}");
    assert_eq!(s.desk.log[..3], ["raise Notes", "click 900,400 Left x1", "type eggs"]);
    let (text, _, _) = call(&mut s, "list_screen_elements", json!({}));
    assert!(text.starts_with("frontmost app: Notes"), "{text}");
    let (text, err, _) = call(&mut s, "list_screen_elements", json!({"app": "Pages"}));
    assert!(err && text.contains("no app named"), "{text}");
    let (text, err, _) = call(&mut s, "focus_window", json!({"app": "Mail", "title": "Inbox"}));
    assert!(!err && text == "Mail is in front", "{text}");
}
