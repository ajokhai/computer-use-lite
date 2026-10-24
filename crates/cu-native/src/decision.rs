//! Small text rules shared by the Python agent: which actions need the user's OK, and which
//! phrases from a task Jev may choose to type (Jev picks options; it does not write text).

pub const RISKY_WORDS: &[&str] = &[
    "send", "buy", "pay", "purchase", "order", "delete", "remove", "submit", "post",
    "publish", "transfer", "confirm", "checkout", "place order", "erase", "trash",
];

/// First `n` characters of `s` (never splits a UTF-8 sequence).
pub fn truncate_chars(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

pub fn is_risky_action(op: &str, label: &str, detail: &str) -> bool {
    let combined = format!("{} {} {}", op, label, detail).to_lowercase();
    RISKY_WORDS.iter().any(|word| combined.contains(word))
}

pub fn extract_text_candidates(task: &str, limit: usize) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();

    // 1. Quoted substrings ("...", “...”, '...')
    let chars: Vec<char> = task.chars().collect();
    let n = chars.len();
    let mut i = 0;
    while i < n {
        let c = chars[i];
        let quote_end = match c {
            '"' => Some('"'),
            '\'' => Some('\''),
            '\u{201c}' => Some('\u{201d}'),
            _ => None,
        };
        if let Some(end_char) = quote_end {
            let mut j = i + 1;
            while j < n && chars[j] != end_char {
                j += 1;
            }
            if j < n {
                let inner: String = chars[i + 1..j].iter().collect();
                let trimmed = inner.trim();
                if trimmed.len() >= 2 {
                    out.push(trimmed.to_string());
                }
                i = j + 1;
                continue;
            }
        }
        i += 1;
    }

    // 0. URLs and web addresses (highest priority for browser/navigation tasks)
    for w in task.split_whitespace() {
        let clean = w.trim_matches(|c: char| c == '"' || c == '\'' || c == ',' || c == ';' || c == '(' || c == ')');
        if clean.starts_with("http://") || clean.starts_with("https://") || clean.starts_with("www.") {
            out.push(clean.to_string());
        } else if clean.contains('.') && !clean.ends_with('.') && !clean.starts_with('.') {
            let tlds = [".com", ".org", ".net", ".io", ".ai", ".dev", ".co", ".edu", ".gov", ".xyz"];
            if tlds.iter().any(|tld| clean.to_lowercase().contains(tld)) {
                out.push(clean.to_string());
            }
        }
    }

    // 2. Verb prefixes
    let verbs = [
        "type", "write", "enter", "search for", "search", "look up", "find",
        "named", "called", "titled", "saying", "that says", "to say", "message",
        "subject", "note", "to", "for", "about", "calculate", "compute", "solve",
        "math", "evaluate", "with", "recipient", "contact", "chat with", "send to",
        "stating", "comment", "tweet", "open", "visit", "go to",
    ];

    // ASCII lowercasing keeps byte offsets identical to `task`, so slicing below is safe.
    let task_lower = task.to_ascii_lowercase();
    for verb in verbs {
        let pattern = format!("{} ", verb);
        let mut start_idx = 0;
        while let Some(pos) = task_lower[start_idx..].find(&pattern) {
            let actual_pos = start_idx + pos;
            let after_verb = &task[actual_pos + pattern.len()..];
            // Only look a little way ahead: a candidate over 200 chars is dropped anyway, and
            // scanning the whole rest of a long task at every match is quadratic.
            let mut window = after_verb.len().min(1024);
            while !after_verb.is_char_boundary(window) {
                window -= 1;
            }
            let ahead_lower = &task_lower[actual_pos + pattern.len()..actual_pos + pattern.len() + window];
            // Read until delimiter: '.', ';', '\n', or " and then ", " then "
            let end_delims = [".", ";", "\n", " and then ", " then "];
            let mut end_pos = window;
            for delim in end_delims {
                if let Some(dp) = ahead_lower.find(delim) {
                    if dp < end_pos {
                        end_pos = dp;
                    }
                }
            }
            let candidate = after_verb[..end_pos].trim_matches(|c: char| c.is_whitespace() || c == '.' || c == ':');
            if !candidate.is_empty() && candidate.chars().count() <= 200 {
                out.push(candidate.to_string());
            }
            start_idx = actual_pos + pattern.len();
        }
    }

    // 3. Math expressions
    let math_delims = ['+', '-', '*', '/', '='];
    let words: Vec<&str> = task.split_whitespace().collect();
    let mut curr_math: Vec<&str> = Vec::new();
    for w in &words {
        let has_math_char = w.chars().any(|c| math_delims.contains(&c) || c.is_ascii_digit());
        if has_math_char {
            curr_math.push(w);
        } else if !curr_math.is_empty() {
            let expr = curr_math.join(" ");
            if expr.chars().any(|c| math_delims.contains(&c)) && expr.chars().any(|c| c.is_ascii_digit()) {
                out.push(expr.trim_matches(|c: char| c == ',' || c == '.').to_string());
            }
            curr_math.clear();
        }
    }
    if !curr_math.is_empty() {
        let expr = curr_math.join(" ");
        if expr.chars().any(|c| math_delims.contains(&c)) && expr.chars().any(|c| c.is_ascii_digit()) {
            out.push(expr.trim_matches(|c: char| c == ',' || c == '.').to_string());
        }
    }

    // 4. Numbers & tokens
    for w in &words {
        let clean = w.trim_matches(|c: char| !c.is_alphanumeric() && c != '.' && c != '@');
        if clean.chars().all(|c| c.is_ascii_digit() || c == '.') && clean.chars().any(|c| c.is_ascii_digit()) {
            out.push(clean.to_string());
        } else if clean.contains('@') {
            out.push(clean.to_string());
        }
    }

    // 5. Clauses split by ',' or ';' or ':' (protecting '://' in URLs)
    let task_safe_proto = task.replace("://", "___PROTO___");
    for part in task_safe_proto.split(&[',', ';', ':'][..]) {
        let restored = part.replace("___PROTO___", "://");
        let p = restored.trim();
        if !p.is_empty() && p.len() <= 100 {
            out.push(p.to_string());
        }
    }

    // 6. Full task
    out.push(task.trim().to_string());

    // Deduplicate case-insensitively, keeping first occurrence
    let mut seen = std::collections::HashSet::new();
    let mut uniq = Vec::new();
    for item in out {
        let lower = item.to_lowercase();
        if !item.is_empty() && item.len() <= 500 && seen.insert(lower) {
            uniq.push(item);
        }
    }

    if uniq.len() > limit {
        uniq.truncate(limit);
    }
    uniq
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_text_candidates() {
        let task = "Calculate 50 * 20 in Calculator and write \"Invoice #1042\" to Alice at alice@work.com";
        let cands = extract_text_candidates(task, 50);
        assert!(cands.contains(&"Invoice #1042".to_string()));
        assert!(cands.iter().any(|c| c.contains("50 * 20")));
        assert!(cands.iter().any(|c| c.contains("alice@work.com")));
    }

    #[test]
    fn non_ascii_tasks_never_panic() {
        let cands = extract_text_candidates("İstanbul: type Grüße to Zoë then send", 50);
        assert!(cands.iter().any(|c| c.starts_with("Grüße")));
        assert_eq!(truncate_chars("héllo🎉", 6), "héllo🎉");
        assert_eq!(truncate_chars("héllo🎉", 2), "hé");
    }

    #[test]
    fn long_tasks_stay_fast() {
        let task = "type 'hello there' into Notes, ".repeat(2000);
        let t0 = std::time::Instant::now();
        let cands = extract_text_candidates(&task, 60);
        assert!(cands.len() <= 60);
        assert!(t0.elapsed().as_millis() < 500, "took {:?}", t0.elapsed());
    }

    #[test]
    fn risky_actions_need_confirmation() {
        assert!(is_risky_action("CLICK", "Send", ""));
        assert!(is_risky_action("CLICK", "Move to Trash", ""));
        assert!(!is_risky_action("CLICK", "New Note", ""));
    }
}
