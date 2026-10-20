use cu_native::{get_frontmost_app, walk_ax_tree};

fn main() {
    println!(">>> Rust cu-native starting...");
    let (pid, app) = get_frontmost_app();
    println!(">>> Frontmost app: pid={}, name={}", pid, app);
    let res = walk_ax_tree(250);
    println!(
        "Rust AX Walker: App='{}', found {} elements in {:.2} ms!",
        res.app,
        res.elements.len(),
        res.duration_ms
    );
    for (i, el) in res.elements.iter().take(10).enumerate() {
        println!("  [{}] {} \"{}\" at ({}, {}) [{}x{}]", i, el.role, el.label, el.x, el.y, el.w, el.h);
    }

    if std::env::args().any(|a| a == "--json") {
        let json = serde_json::to_string(&res).unwrap();
        println!("{}", json);
    }
}
