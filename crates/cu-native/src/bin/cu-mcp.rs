//! `cu-mcp`: the Computer Use MCP server. Add it to any MCP client (Claude, Cursor, VS Code,
//! Antigravity, Windsurf...) as a stdio server; it needs no API key.

use std::io::{stdin, stdout};

use cu_native::desktop::MacDesktop;
use cu_native::mcp::Server;

fn main() {
    if std::env::args().any(|a| a == "--version") {
        println!("cu-mcp {}", env!("CARGO_PKG_VERSION"));
        return;
    }
    // stdout carries the protocol; anything else goes to stderr.
    if let Err(e) = Server::new(MacDesktop::default()).serve(stdin().lock(), stdout().lock()) {
        eprintln!("cu-mcp: {e}");
        std::process::exit(1);
    }
}
