//! Lessons learned about apps and sites, shared with the Python agent (cu/memory.py): the same
//! file, lock, merge rule, cap and ranking, so a fix found through MCP helps the app's own agent
//! and the other way round. Bounded: at most MAX_LESSONS lessons with short fields (~200 KB).

use std::fs::{self, File};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Map, Value};

pub const MAX_LESSONS: usize = 200;
const MAX_TEXT: usize = 300;

/// ~/Library/Application Support/Computer Use (where the app keeps its settings).
pub fn default_dir() -> PathBuf {
    let home = std::env::var_os("HOME").unwrap_or_default();
    Path::new(&home).join("Library/Application Support/Computer Use")
}

pub(crate) fn now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

/// Collapse whitespace and cut to `n` characters, ending with "…" when cut.
pub fn clip(text: &str, n: usize) -> String {
    let t = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if t.chars().count() <= n {
        t
    } else {
        format!("{}…", t.chars().take(n - 1).collect::<String>())
    }
}

fn num(l: &Map<String, Value>, k: &str, default: f64) -> f64 {
    l.get(k).and_then(Value::as_f64).unwrap_or(default)
}

fn text<'a>(l: &'a Map<String, Value>, k: &str) -> &'a str {
    l.get(k).and_then(Value::as_str).unwrap_or("")
}

/// How much a lesson is worth keeping: confirmed fixes, repeats and recent use count; age fades it.
fn score(l: &Map<String, Value>, t: f64) -> f64 {
    let worked = if text(l, "outcome") == "worked" { 2.0 } else { 1.0 };
    worked + 0.5 * num(l, "seen", 1.0) + 0.2 * num(l, "used", 0.0) - (t - num(l, "at", 0.0)) / 86400.0 / 30.0
}

fn words(s: &str) -> std::collections::HashSet<String> {
    s.to_lowercase()
        .split(|c: char| !c.is_ascii_lowercase())
        .filter(|w| w.len() >= 4)
        .map(String::from)
        .collect()
}

/// Run `f` on the list stored in `<dir>/<name>.json` under the key `name` ({"version": 1, name: [...]}),
/// holding an exclusive flock on `<name>.lock` as the Python side does, then write it back.
/// A missing or unreadable file starts empty.
pub(crate) fn with_list<T>(dir: &Path, name: &str, f: impl FnOnce(&mut Vec<Value>) -> T) -> Result<T, String> {
    fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    let lock = File::create(dir.join(format!("{name}.lock"))).map_err(|e| e.to_string())?;
    lock.lock().map_err(|e| e.to_string())?;
    let path = dir.join(format!("{name}.json"));
    let mut data: Value = fs::read_to_string(&path).ok().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or(Value::Null);
    if !data.get(name).is_some_and(Value::is_array) {
        data = json!({"version": 1, name: []});
    }
    let list = data.get_mut(name).and_then(Value::as_array_mut).expect("checked above");
    let out = f(list);
    let body = serde_json::to_string_pretty(&data).map_err(|e| e.to_string())?;
    fs::write(&path, body).map_err(|e| e.to_string())?;
    Ok(out)
}

pub struct Store {
    dir: PathBuf,
}

impl Store {
    pub fn new(dir: PathBuf) -> Self {
        Store { dir }
    }

    fn with_lessons<T>(&self, f: impl FnOnce(&mut Vec<Value>) -> T) -> Result<T, String> {
        with_list(&self.dir, "lessons", f)
    }

    /// Save a lesson, merged with the same fix for the same place. Returns how often it's been seen.
    pub fn remember(&self, place: &str, task: &str, what: &str, fix: &str, worked: bool, model: &str) -> Result<u64, String> {
        let place = clip(place, 60).to_lowercase();
        let (what, fix, t) = (clip(what, MAX_TEXT), clip(fix, MAX_TEXT), now());
        self.with_lessons(|lessons| {
            for old in lessons.iter_mut().filter_map(Value::as_object_mut) {
                if text(old, "where") == place && text(old, "fix").to_lowercase() == fix.to_lowercase() {
                    let seen = num(old, "seen", 1.0) as u64 + 1;
                    old.insert("what".into(), json!(what));
                    old.insert("at".into(), json!(t));
                    old.insert("model".into(), json!(clip(model, 40)));
                    old.insert("seen".into(), json!(seen));
                    if worked {
                        old.insert("outcome".into(), json!("worked"));
                    }
                    return seen;
                }
            }
            lessons.push(json!({
                "where": place, "task": clip(task, 120), "what": what, "fix": fix,
                "outcome": if worked { "worked" } else { "failed" }, "model": clip(model, 40),
                "at": t, "seen": 1, "used": 0,
            }));
            if lessons.len() > MAX_LESSONS {
                let empty = Map::new();
                lessons.sort_by(|a, b| {
                    let (a, b) = (a.as_object().unwrap_or(&empty), b.as_object().unwrap_or(&empty));
                    score(b, t).total_cmp(&score(a, t))
                });
                lessons.truncate(MAX_LESSONS);
            }
            1
        })
    }

    /// The best lessons for these places (and task), best first, as "- On …" lines; marks them used.
    pub fn recall(&self, places: &[String], task: &str, limit: usize) -> Result<Vec<String>, String> {
        let keys: Vec<String> = places.iter().map(|k| k.trim().to_lowercase()).filter(|k| !k.is_empty()).collect();
        let task_words = words(task);
        let t = now();
        self.with_lessons(|lessons| {
            let mut hits: Vec<(f64, usize)> = lessons
                .iter()
                .enumerate()
                .filter_map(|(i, l)| {
                    let l = l.as_object()?;
                    keys.iter().any(|k| k == text(l, "where")).then(|| {
                        let overlap = words(text(l, "task")).intersection(&task_words).count() as f64;
                        (overlap + score(l, t), i)
                    })
                })
                .collect();
            hits.sort_by(|a, b| b.0.total_cmp(&a.0));
            hits.truncate(limit.clamp(1, 20));
            hits.iter()
                .filter_map(|&(_, i)| {
                    let l = lessons[i].as_object_mut()?;
                    let used = num(l, "used", 0.0) as u64 + 1;
                    l.insert("used".into(), json!(used));
                    let tag = if text(l, "outcome") == "worked" { "worked" } else { "not confirmed yet" };
                    Some(format!("- On {}: {} → {} ({tag})", text(l, "where"), text(l, "what"), text(l, "fix")))
                })
                .collect()
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store() -> (Store, PathBuf) {
        let dir = std::env::temp_dir().join(format!("cu-lessons-{}-{}", std::process::id(), now()));
        (Store::new(dir.clone()), dir)
    }

    #[test]
    fn remember_merges_and_recall_ranks() {
        let (s, dir) = store();
        assert_eq!(s.remember("X.com", "post a tweet", "typed into the page", "click the Post text box first", false, "mcp client").unwrap(), 1);
        assert_eq!(s.remember("x.com", "", "typed into the page again", "Click the post text box first", true, "mcp client").unwrap(), 2);
        s.remember("notes", "make a note", "no New button", "press command+n", true, "").unwrap();
        let got = s.recall(&["x.com".into()], "post a tweet now", 6).unwrap();
        assert_eq!(got, vec!["- On x.com: typed into the page again → click the Post text box first (worked)"]);
        let raw: Value = serde_json::from_str(&fs::read_to_string(dir.join("lessons.json")).unwrap()).unwrap();
        let first = &raw["lessons"][0];
        assert_eq!((first["seen"].as_u64(), first["used"].as_u64()), (Some(2), Some(1)));
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn the_store_stays_bounded_and_keeps_unknown_fields() {
        let (s, dir) = store();
        fs::create_dir_all(&dir).unwrap();
        let seed = format!(r#"{{"version":1,"lessons":[{{"where":"a","fix":"f","what":"w","extra":7,"outcome":"worked","seen":50,"at":{}}}]}}"#, now());
        fs::write(dir.join("lessons.json"), seed).unwrap();
        for i in 0..(MAX_LESSONS + 30) {
            s.remember("b", "", &"long ".repeat(200), &format!("fix {i}"), i % 2 == 0, "").unwrap();
        }
        let raw: Value = serde_json::from_str(&fs::read_to_string(dir.join("lessons.json")).unwrap()).unwrap();
        let lessons = raw["lessons"].as_array().unwrap();
        assert_eq!(lessons.len(), MAX_LESSONS);
        assert!(lessons.iter().all(|l| l["what"].as_str().unwrap().chars().count() <= MAX_TEXT));
        assert!(lessons.iter().any(|l| l["extra"] == 7), "fields this side doesn't know survive a write");
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn clip_collapses_and_marks_cuts() {
        assert_eq!(clip("  a \n b  ", 10), "a b");
        assert_eq!(clip("abcdef", 4), "abc…");
    }
}
