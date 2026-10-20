use serde::{Deserialize, Serialize};
use crate::NativeElement;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct SafetyDecision {
    pub allowed: bool,
    pub violation: Option<String>,
    pub reason: String,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct VerificationResult {
    pub verified: bool,
    pub reason: String,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct AgentPolicyDecision {
    pub should_escalate_or_pause: bool,
    pub is_looping: bool,
    pub reason: String,
}

pub const RISKY_WORDS: &[&str] = &[
    "send", "buy", "pay", "purchase", "order", "delete", "remove", "submit", "post",
    "publish", "transfer", "confirm", "checkout", "place order", "erase", "trash",
];

pub fn check_typing_safety(
    text: &str,
    el_role: Option<&str>,
    el_label: Option<&str>,
    app: &str,
    els_labels: &[String],
) -> SafetyDecision {
    let app_lower = app.to_lowercase();
    let is_comm = ["messages", "mail", "slack", "discord", "telegram", "teams"]
        .iter()
        .any(|c| app_lower.contains(c))
        || els_labels.iter().any(|lbl| {
            let l = lbl.to_lowercase();
            l.contains("to:") || l.contains("recipient")
        });

    if !is_comm {
        return SafetyDecision {
            allowed: true,
            violation: None,
            reason: "Non-communication app or context".to_string(),
        };
    }

    let text_trimmed = text.trim();
    let text_lower = text_trimmed.to_lowercase();
    let words: Vec<&str> = text_trimmed.split_whitespace().collect();
    let is_short = words.len() <= 3;

    // Agnostic recipient identifier detection:
    // 1. Email address containing '@'
    // 2. Phone number starting with '+' or numeric dial pattern
    // 3. Social/chat handle starting with '@'
    // 4. Honorific prefixes (Mr., Ms., Dr., Prof., etc.)
    // 5. Capitalized person name / proper noun (1 to 3 words)
    let has_honorific = ["mr.", "mr", "ms.", "ms", "mrs.", "mrs", "dr.", "dr", "prof.", "prof"]
        .iter()
        .any(|h| text_lower.starts_with(h));

    let is_contact_handle = text_trimmed.contains('@')
        || text_trimmed.starts_with('+')
        || (text_trimmed.starts_with('0') && text_trimmed.chars().all(|c| c.is_ascii_digit() || c == '-' || c == ' '));

    let is_capitalized_name = is_short && !words.is_empty() && words.iter().all(|w| {
        let first = w.chars().next().unwrap_or(' ');
        first.is_uppercase() || w.starts_with('+') || w.starts_with('@')
    });

    let is_recipient_name = is_contact_handle || has_honorific || is_capitalized_name;

    let label_lower = el_label.unwrap_or("").to_lowercase();
    let role = el_role.unwrap_or("");

    let is_recipient_field = ["to:", "to", "recipient", "address", "search"]
        .iter()
        .any(|r| label_lower.contains(r))
        || role == "AXComboBox";

    let is_body_field = role == "AXTextArea"
        || ["message", "text message", "body", "chat"]
            .iter()
            .any(|b| label_lower.contains(b));

    if is_recipient_name && is_body_field {
        let msg = format!(
            "Communication Safety Guard: Refusing to type contact name '{}' into message body '{}'. Recipient must only be typed into the 'To:' field!",
            text_trimmed,
            el_label.unwrap_or("")
        );
        return SafetyDecision {
            allowed: false,
            violation: Some(msg.clone()),
            reason: msg,
        };
    }

    if !is_recipient_name && is_recipient_field && words.len() > 4 {
        let snippet = if text_trimmed.len() > 30 {
            format!("{}...", &text_trimmed[..30])
        } else {
            text_trimmed.to_string()
        };
        let msg = format!(
            "Communication Safety Guard: Refusing to type message text '{}' into recipient field '{}'.",
            snippet,
            el_label.unwrap_or("")
        );
        return SafetyDecision {
            allowed: false,
            violation: Some(msg.clone()),
            reason: msg,
        };
    }

    SafetyDecision {
        allowed: true,
        violation: None,
        reason: "Typing safety check passed".to_string(),
    }
}

pub fn evaluate_document_write_safety(
    task: &str,
    el_role: Option<&str>,
    el_label: Option<&str>,
) -> SafetyDecision {
    if let (Some(role), Some(label)) = (el_role, el_label) {
        if role == "AXTextArea" && label.trim().len() > 8 {
            let task_lower = task.to_lowercase();
            let is_create = ["create", "new note", "new doc", "write a new", "new file", "new document"]
                .iter()
                .any(|w| task_lower.contains(w));
            let is_explicit = task_lower.contains(&label.to_lowercase());

            if is_create && !is_explicit {
                let snippet = if label.trim().len() > 30 {
                    format!("{}...", &label.trim()[..30])
                } else {
                    label.trim().to_string()
                };
                let msg = format!(
                    "Safety Guard: Refusing to type/paste into existing document '{}' when task asked to create a new item. Ensure a blank new document is open.",
                    snippet
                );
                return SafetyDecision {
                    allowed: false,
                    violation: Some(msg.clone()),
                    reason: msg,
                };
            }
        }
    }

    SafetyDecision {
        allowed: true,
        violation: None,
        reason: "Document write safety passed".to_string(),
    }
}

pub fn is_risky_action(op: &str, label: &str, detail: &str) -> bool {
    let combined = format!("{} {} {}", op, label, detail).to_lowercase();
    RISKY_WORDS.iter().any(|word| combined.contains(word))
}

#[derive(Clone, Debug, Default)]
pub struct ObjectiveCriteria {
    pub requires_dispatch: bool,
    pub requires_creation: bool,
    pub requires_media: bool,
    pub requires_computation: bool,
    pub target_entities: Vec<String>,
}

impl ObjectiveCriteria {
    pub fn parse(task: &str) -> Self {
        let task_lower = task.to_lowercase();
        let words: Vec<&str> = task_lower.split_whitespace().collect();

        // 1. Dispatch / send / share intent
        let dispatch_verbs = [
            "send", "share", "mail", "email", "message", "text", "post", "submit",
            "publish", "forward", "dispatch", "tweet",
        ];
        let requires_dispatch = dispatch_verbs.iter().any(|v| {
            words.iter().any(|w| {
                let clean = w.trim_matches(|c: char| !c.is_alphabetic());
                clean == *v || clean.starts_with(v)
            })
        });

        // 2. Creation / writing intent
        let creation_verbs = [
            "create", "write", "compose", "draft", "make", "new", "add", "record",
        ];
        let requires_creation = creation_verbs.iter().any(|v| {
            words.iter().any(|w| {
                let clean = w.trim_matches(|c: char| !c.is_alphabetic());
                clean == *v || clean.starts_with(v)
            })
        });

        // 3. Media / capture / attachment intent
        let media_nouns = [
            "screenshot", "capture", "image", "photo", "picture", "attach", "attachment", "card",
        ];
        let requires_media = media_nouns.iter().any(|m| task_lower.contains(m));

        // 4. Computation / calculation intent
        let math_terms = ["calculate", "computation", "compute", "math", "evaluate", "solve", "sum", "multiply", "divide"];
        let has_math_operator = ['+', '*', '/', '=']
            .iter()
            .any(|op| task.contains(*op));
        let requires_computation = math_terms.iter().any(|t| task_lower.contains(t))
            || (has_math_operator && task.chars().any(|c| c.is_ascii_digit()));

        // 5. Target entities (quoted strings in task)
        let mut target_entities = Vec::new();
        let chars: Vec<char> = task.chars().collect();
        let n = chars.len();
        let mut i = 0;
        while i < n {
            if chars[i] == '"' || chars[i] == '\'' || chars[i] == '\u{201c}' {
                let end_quote = match chars[i] {
                    '"' => '"',
                    '\'' => '\'',
                    '\u{201c}' => '\u{201d}',
                    _ => '"',
                };
                let mut j = i + 1;
                while j < n && chars[j] != end_quote {
                    j += 1;
                }
                if j < n {
                    let entity: String = chars[i + 1..j].iter().collect();
                    let trimmed = entity.trim();
                    if trimmed.len() >= 2 {
                        target_entities.push(trimmed.to_string());
                    }
                    i = j + 1;
                    continue;
                }
            }
            i += 1;
        }

        Self {
            requires_dispatch,
            requires_creation,
            requires_media,
            requires_computation,
            target_entities,
        }
    }
}

pub fn verify_task_completion(
    task: &str,
    _app: &str,
    elements: &[NativeElement],
    history: &[String],
) -> VerificationResult {
    if history.is_empty() {
        return VerificationResult {
            verified: false,
            reason: "No actions have been executed yet for this objective.".to_string(),
        };
    }

    // Check if every action failed
    if history.iter().all(|h| h.trim_start().to_uppercase().starts_with("FAILED")) {
        return VerificationResult {
            verified: false,
            reason: "All attempted actions for this task failed.".to_string(),
        };
    }

    // Check for unresolved on-screen error alerts across any app
    for el in elements {
        if el.role == "AXAlert"
            || (el.role == "AXDialog"
                && ["error", "failed", "could not", "invalid"]
                    .iter()
                    .any(|w| el.label.to_lowercase().contains(w)))
        {
            return VerificationResult {
                verified: false,
                reason: format!("An unresolved error dialog is active on screen: '{}'.", el.label),
            };
        }
    }

    let criteria = ObjectiveCriteria::parse(task);

    // 1. Dispatch & Send verification
    if criteria.requires_dispatch {
        // Are modal dispatch / share sheets or recipient selectors still open?
        let sheet_open = elements.iter().any(|e| {
            let l = e.label.to_lowercase();
            (l.contains("to:") || l.contains("add recipient") || l.contains("recipient"))
                || e.role == "AXSheet"
                || e.role == "AXPopover"
        });
        if sheet_open {
            return VerificationResult {
                verified: false,
                reason: "A compose, sharing, or recipient dispatch sheet is still active on screen and has not been submitted or closed.".to_string(),
            };
        }

        let has_dispatch = history.iter().any(|h| {
            let hl = h.to_lowercase();
            ["send", "return", "enter", "delivered", "shared", "submit", "dispatch", "post", "publish", "command+return", "command,return"]
                .iter()
                .any(|s| hl.contains(s))
        });
        if !has_dispatch {
            return VerificationResult {
                verified: false,
                reason: "Objective requested dispatch/sharing, but no send, submit, or return action was executed to complete it.".to_string(),
            };
        }
    }

    // 2. Media / Attachment verification
    if criteria.requires_media {
        let has_media = history.iter().any(|h| {
            let hl = h.to_lowercase();
            hl.contains("screenshot") || hl.contains("paste") || hl.contains("attachment") || hl.contains("image") || hl.contains("photo")
        });
        if !has_media {
            return VerificationResult {
                verified: false,
                reason: "Objective requested capturing or attaching media, but no screenshot or attachment action was found in execution history.".to_string(),
            };
        }
    }

    // 3. Computation verification
    if criteria.requires_computation {
        let has_calc = history.iter().any(|h| {
            let hl = h.to_lowercase();
            (hl.contains("calc") && !hl.contains("open"))
                || hl.contains("click")
                || hl.contains("equals")
                || hl.contains("keypad")
                || hl.contains('+')
                || hl.contains('*')
                || hl.contains('/')
                || hl.contains('-')
        });
        if !has_calc {
            return VerificationResult {
                verified: false,
                reason: "Objective requested computation, but no calculation or keypad actions were performed.".to_string(),
            };
        }
    }

    // 4. Creation / Input verification (when not pure dispatch)
    if criteria.requires_creation && !criteria.requires_dispatch {
        let has_input = history.iter().any(|h| {
            let hl = h.to_lowercase();
            hl.contains("type")
                || hl.contains("paste")
                || hl.contains("new_item")
                || hl.contains("create")
                || hl.contains("write")
        });
        if !has_input {
            return VerificationResult {
                verified: false,
                reason: "Objective requested creating or writing content, but no text input or document creation action was executed.".to_string(),
            };
        }
    }

    // 5. Target Entity Grounding verification
    for entity in &criteria.target_entities {
        let ent_lower = entity.to_lowercase();
        let entity_in_history = history.iter().any(|h| h.to_lowercase().contains(&ent_lower));
        let entity_on_screen = elements.iter().any(|e| e.label.to_lowercase().contains(&ent_lower));
        if !entity_in_history && !entity_on_screen {
            return VerificationResult {
                verified: false,
                reason: format!("Requested target entity '{}' was not found in actions or on-screen elements.", entity),
            };
        }
    }

    VerificationResult {
        verified: true,
        reason: "Task verified: all objective conditions satisfied".to_string(),
    }
}

pub fn evaluate_agent_loop_or_stuck(
    op: &str,
    misses: usize,
    recent: &[String],
) -> AgentPolicyDecision {
    let is_blocked = op == "BLOCKED";
    let too_many_misses = misses >= 3;
    let is_looping = recent.len() >= 3 && recent.windows(2).all(|w| w[0] == w[1]);

    let should_escalate = is_blocked || too_many_misses || is_looping;
    let reason = if is_blocked {
        "Agent reported BLOCKED".to_string()
    } else if too_many_misses {
        format!("Hit {} consecutive misses without progress", misses)
    } else if is_looping {
        format!("Detected action loop: '{}' repeated consecutively", recent[0])
    } else {
        "Normal operation".to_string()
    };

    AgentPolicyDecision {
        should_escalate_or_pause: should_escalate,
        is_looping,
        reason,
    }
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

    // 2. Verb prefixes
    let verbs = [
        "type", "write", "enter", "search for", "search", "look up", "find",
        "named", "called", "titled", "saying", "that says", "to say", "message",
        "subject", "note", "to", "for", "about", "calculate", "compute", "solve",
        "math", "evaluate",
    ];

    let task_lower = task.to_lowercase();
    for verb in verbs {
        let pattern = format!("{} ", verb);
        let mut start_idx = 0;
        while let Some(pos) = task_lower[start_idx..].find(&pattern) {
            let actual_pos = start_idx + pos;
            let after_verb = &task[actual_pos + pattern.len()..];
            // Read until delimiter: '.', ';', '\n', or " and then ", " then "
            let end_delims = [".", ";", "\n", " and then ", " then "];
            let mut end_pos = after_verb.len();
            for delim in end_delims {
                if let Some(dp) = after_verb.to_lowercase().find(delim) {
                    if dp < end_pos {
                        end_pos = dp;
                    }
                }
            }
            let candidate = after_verb[..end_pos].trim_matches(|c: char| c.is_whitespace() || c == '.' || c == ':');
            if !candidate.is_empty() && candidate.len() <= 200 {
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

    // 5. Clauses split by ',' or ';'
    for part in task.split(&[',', ';', ':'][..]) {
        let p = part.trim();
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
    fn test_typing_safety_agnostic_recipient_in_body() {
        // Any proper name in message body is caught
        let res = check_typing_safety(
            "Sarah Connor",
            Some("AXTextArea"),
            Some("Message body"),
            "Messages",
            &[],
        );
        assert!(!res.allowed);
        assert!(res.violation.unwrap().contains("Refusing to type contact name"));

        // Honorific name
        let res_dr = check_typing_safety(
            "Dr. Watson",
            Some("AXTextArea"),
            Some("Chat box"),
            "Slack",
            &[],
        );
        assert!(!res_dr.allowed);

        // Email handle
        let res_email = check_typing_safety(
            "alice@example.com",
            Some("AXTextArea"),
            Some("Message body"),
            "Mail",
            &[],
        );
        assert!(!res_email.allowed);
    }

    #[test]
    fn test_typing_safety_allows_recipient_in_to_field() {
        let res = check_typing_safety(
            "Alice Smith",
            Some("AXTextField"),
            Some("To: recipient"),
            "Messages",
            &[],
        );
        assert!(res.allowed);
        assert!(res.violation.is_none());
    }

    #[test]
    fn test_typing_safety_prevents_long_text_in_to_field() {
        let res = check_typing_safety(
            "Here is the report with our calculations for this week",
            Some("AXTextField"),
            Some("To:"),
            "Messages",
            &[],
        );
        assert!(!res.allowed);
        assert!(res.violation.unwrap().contains("Refusing to type message text"));
    }

    #[test]
    fn test_document_write_safety() {
        let res = evaluate_document_write_safety(
            "Create a new document with quarterly findings",
            Some("AXTextArea"),
            Some("Existing Project Roadmap from last year"),
        );
        assert!(!res.allowed);
        assert!(res.violation.unwrap().contains("Refusing to type/paste into existing document"));

        // When task explicitly references document name:
        let res_explicit = evaluate_document_write_safety(
            "Append notes to Project Roadmap",
            Some("AXTextArea"),
            Some("Project Roadmap"),
        );
        assert!(res_explicit.allowed);
    }

    #[test]
    fn test_verify_task_completion_agnostic_share() {
        let elements = vec![
            NativeElement {
                id: "e1".into(),
                role: "AXSheet".into(),
                label: "Share Popover".into(),
                x: 100, y: 100, w: 200, h: 200, focused: false,
            }
        ];
        let history = vec!["click Share".to_string()];
        let res = verify_task_completion("Email project sheet to team", "Mail", &elements, &history);
        assert!(!res.verified);
        assert!(res.reason.contains("sheet is still active"));
    }

    #[test]
    fn test_verify_task_completion_agnostic_success() {
        let elements = vec![];
        let history = vec![
            "click Compose".to_string(),
            "type alice@test.com".to_string(),
            "type Project update".to_string(),
            "send return".to_string(),
        ];
        let res = verify_task_completion("Email Alice the project update", "Mail", &elements, &history);
        assert!(res.verified);
    }

    #[test]
    fn test_verify_task_completion_unresolved_error_dialog() {
        let elements = vec![
            NativeElement {
                id: "e1".into(),
                role: "AXAlert".into(),
                label: "Error: Could not connect to mail server".into(),
                x: 100, y: 100, w: 200, h: 200, focused: false,
            }
        ];
        let history = vec!["click Send".to_string()];
        let res = verify_task_completion("Send invoice to customer", "Mail", &elements, &history);
        assert!(!res.verified);
        assert!(res.reason.contains("error dialog is active"));
    }

    #[test]
    fn test_agent_stuck_detection() {
        let recent = vec![
            "click Button".to_string(),
            "click Button".to_string(),
            "click Button".to_string(),
        ];
        let res = evaluate_agent_loop_or_stuck("CLICK", 0, &recent);
        assert!(res.should_escalate_or_pause);
        assert!(res.is_looping);
    }

    #[test]
    fn test_extract_text_candidates() {
        let task = "Calculate 50 * 20 in Calculator and write \"Invoice #1042\" to Alice at alice@work.com";
        let cands = extract_text_candidates(task, 50);
        assert!(cands.contains(&"Invoice #1042".to_string()));
        assert!(cands.iter().any(|c| c.contains("50 * 20")));
        assert!(cands.iter().any(|c| c.contains("alice@work.com")));
    }
}
