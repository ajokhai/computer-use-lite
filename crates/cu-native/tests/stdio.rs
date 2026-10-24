//! The real `cu-mcp` binary over stdio, using only read-only tools (it never acts on the Mac).

use std::io::{BufRead, BufReader, Write};
use std::process::{Command, Stdio};

use serde_json::{json, Value};

#[test]
fn real_server_answers_over_stdio() {
    let mut child = Command::new(env!("CARGO_BIN_EXE_cu-mcp"))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .expect("start cu-mcp");
    let mut stdin = child.stdin.take().unwrap();
    let mut out = BufReader::new(child.stdout.take().unwrap());
    let mut ask = |id: i64, method: &str, params: Value| -> Value {
        writeln!(stdin, "{}", json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params})).unwrap();
        let mut line = String::new();
        out.read_line(&mut line).unwrap();
        let v: Value = serde_json::from_str(&line).expect("one JSON reply per line");
        assert_eq!(v["id"], id);
        v
    };

    let init = ask(1, "initialize", json!({"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}));
    assert_eq!(init["result"]["serverInfo"]["name"], "computer-use-lite");
    let tools = ask(2, "tools/list", json!({}));
    assert!(tools["result"]["tools"].as_array().unwrap().len() >= 18);

    for (i, tool) in ["check_permissions", "list_screen_elements", "list_windows"].iter().enumerate() {
        let r = ask(10 + i as i64, "tools/call", json!({"name": tool, "arguments": {}}));
        let text = r["result"]["content"][0]["text"].as_str().unwrap_or_default();
        assert!(!text.is_empty(), "{tool} returned nothing: {r}");
        eprintln!("--- {tool}\n{}", text.lines().take(6).collect::<Vec<_>>().join("\n"));
    }

    drop(stdin); // EOF: the server exits cleanly
    assert!(child.wait().unwrap().success());
}
