//! Skills: named, step-by-step ways of doing something in an app or site, shared with the Python
//! side (cu/skills.py): the same skills.json and lock, merge rule, cap and ranking. Bounded: at most
//! MAX_SKILLS skills of at most MAX_STEPS short steps; the user's own skills are never dropped.

use std::collections::HashSet;
use std::path::PathBuf;

use serde_json::{json, Map, Value};

use crate::lessons::{clip, now, with_list};

pub const MAX_SKILLS: usize = 100;
const MAX_STEPS: usize = 20;
const STEP_CHARS: usize = 200;
const STOPWORDS: &[&str] = &["the", "and", "for", "with", "that", "this", "into"];

fn words(s: &str) -> HashSet<String> {
    s.to_lowercase()
        .split(|c: char| !c.is_ascii_lowercase())
        .filter(|w| w.len() >= 3 && !STOPWORDS.contains(w))
        .map(String::from)
        .collect()
}

/// Text outside quotes: quoted words are content to type, not what the task is about.
fn unquoted(s: &str) -> String {
    s.split(['"', '\u{201c}', '\u{201d}']).step_by(2).collect::<Vec<_>>().join(" ")
}

fn text<'a>(s: &'a Map<String, Value>, k: &str) -> &'a str {
    s.get(k).and_then(Value::as_str).unwrap_or("")
}

fn num(s: &Map<String, Value>, k: &str) -> f64 {
    s.get(k).and_then(Value::as_f64).unwrap_or(0.0)
}

/// How much a skill is worth keeping; the user's own are worth the most.
fn value(s: &Map<String, Value>, t: f64) -> f64 {
    let base = if text(s, "source") == "user" { 100.0 } else { 0.0 };
    base + 2.0 * num(s, "wins") - 3.0 * num(s, "fails") + 0.5 * num(s, "uses") - (t - num(s, "updated")) / 86400.0 / 30.0
}

fn about(s: &Map<String, Value>) -> HashSet<String> {
    words(&format!("{} {}", text(s, "name"), text(s, "when")))
}

pub fn as_text(s: &Map<String, Value>) -> String {
    let mut out = format!("Skill \"{}\" ({}):", text(s, "name"), text(s, "where"));
    for (i, step) in s.get("steps").and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str).enumerate() {
        out.push_str(&format!("\n  {}. {step}", i + 1));
    }
    for note in s.get("notes").and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str) {
        out.push_str(&format!("\n  Note: {note}"));
    }
    out
}

pub struct Skills {
    dir: PathBuf,
}

impl Skills {
    pub fn new(dir: PathBuf) -> Self {
        Skills { dir }
    }

    /// Skills that fit the task, best first: those for these places (or "general") sharing a word
    /// with the task's name and "when".
    pub fn find(&self, task: &str, places: &[String], limit: usize) -> Result<Vec<String>, String> {
        let want = words(&unquoted(task));
        let places: Vec<String> = places.iter().map(|p| p.trim().to_lowercase()).filter(|p| !p.is_empty()).collect();
        let t = now();
        with_list(&self.dir, "skills", |skills| {
            let mut hits: Vec<(f64, String)> = skills
                .iter()
                .filter_map(Value::as_object)
                .filter_map(|s| {
                    let place = text(s, "where");
                    let here = places.iter().any(|p| p == place);
                    let overlap = about(s).intersection(&want).count();
                    (overlap > 0 && (here || place == "general" || places.is_empty()))
                        .then(|| (overlap as f64 + if here { 1.0 } else { 0.0 } + 0.1 * value(s, t) / 100.0, as_text(s)))
                })
                .collect();
            hits.sort_by(|a, b| b.0.total_cmp(&a.0));
            hits.into_iter().take(limit.clamp(1, 5)).map(|(_, s)| s).collect()
        })
    }

    /// Save a skill as the user's own (from an MCP client), replacing the similar one for the same place.
    pub fn save(&self, name: &str, place: &str, when: &str, steps: &[String], notes: &[String]) -> Result<String, String> {
        let steps: Vec<String> = steps.iter().map(|x| clip(x, STEP_CHARS)).filter(|x| !x.is_empty()).take(MAX_STEPS).collect();
        if steps.is_empty() {
            return Err("a skill needs at least one step".into());
        }
        let name = Some(clip(name, 60)).filter(|n| !n.is_empty()).unwrap_or_else(|| "Untitled Skill".into());
        let place = Some(clip(place, 60).to_lowercase()).filter(|p| !p.is_empty()).unwrap_or_else(|| "general".into());
        let when = clip(when, STEP_CHARS);
        let notes: Vec<String> = notes.iter().map(|x| clip(x, STEP_CHARS)).filter(|x| !x.is_empty()).take(8).collect();
        let t = now();
        let new_about = words(&format!("{name} {when}"));
        with_list(&self.dir, "skills", |skills| {
            for old in skills.iter_mut().filter_map(Value::as_object_mut) {
                let old_about = about(old);
                let similar = text(old, "where") == place
                    && !old_about.is_empty()
                    && !new_about.is_empty()
                    && old_about.intersection(&new_about).count() * 2 >= old_about.union(&new_about).count();
                if similar {
                    let mut merged: Vec<Value> = old.get("notes").and_then(Value::as_array).cloned().unwrap_or_default();
                    for n in &notes {
                        if !merged.iter().any(|m| m.as_str() == Some(n)) {
                            merged.push(json!(n));
                        }
                    }
                    merged.truncate(8);
                    old.insert("steps".into(), json!(steps));
                    if !when.is_empty() {
                        old.insert("when".into(), json!(when));
                    }
                    old.insert("notes".into(), Value::Array(merged));
                    old.insert("updated".into(), json!(t));
                    old.insert("source".into(), json!("user"));
                    return format!("updated skill \"{}\" for {place}", text(old, "name"));
                }
            }
            let id = format!("{:012x}", ((t * 1e6) as u64 ^ (std::process::id() as u64) << 20) & 0xffff_ffff_ffff);
            skills.push(json!({
                "id": id, "name": name, "where": place, "when": when, "steps": steps, "notes": notes,
                "source": "user", "uses": 0, "wins": 0, "fails": 0, "created": t, "updated": t,
            }));
            trim(skills, t);
            format!("saved skill \"{name}\" for {place}")
        })
    }
}

/// Drop learned skills that keep failing, then the least valuable, to stay under MAX_SKILLS.
fn trim(skills: &mut Vec<Value>, t: f64) {
    skills.retain(|s| {
        let s = s.as_object();
        s.is_none_or(|s| text(s, "source") == "user" || num(s, "fails") < 3.0 || num(s, "wins") > num(s, "fails"))
    });
    if skills.len() > MAX_SKILLS {
        let empty = Map::new();
        skills.sort_by(|a, b| value(b.as_object().unwrap_or(&empty), t).total_cmp(&value(a.as_object().unwrap_or(&empty), t)));
        skills.truncate(MAX_SKILLS);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store() -> (Skills, PathBuf) {
        let dir = std::env::temp_dir().join(format!("cu-skills-{}-{}", std::process::id(), now()));
        (Skills::new(dir.clone()), dir)
    }

    #[test]
    fn save_merges_similar_and_find_ranks_by_place_and_words() {
        let (s, dir) = store();
        let steps = |v: &[&str]| v.iter().map(|x| x.to_string()).collect::<Vec<_>>();
        assert!(s.save("Post on X", "X.com", "posting a tweet", &steps(&["Click Post", "Type", "Click Post"]), &[]).unwrap().starts_with("saved"));
        assert!(s.save("Post on X", "x.com", "posting a tweet", &steps(&["Click the compose box", "Type", "Click Post"]), &steps(&["Wait for the toast"])).unwrap().starts_with("updated"));
        s.save("Make a note", "notes", "", &steps(&["Press command+n"]), &[]).unwrap();
        let found = s.find("post a tweet saying \"make a note\"", &["x.com".into()], 2).unwrap();
        assert_eq!(found.len(), 1, "{found:?}");
        assert!(found[0].starts_with("Skill \"Post on X\" (x.com):\n  1. Click the compose box"), "{}", found[0]);
        assert!(found[0].ends_with("Note: Wait for the toast"));
        assert!(s.save("Nothing", "x.com", "", &[], &[]).is_err());
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn the_store_stays_bounded_and_keeps_the_users_skills() {
        let (s, dir) = store();
        std::fs::create_dir_all(&dir).unwrap();
        let learned: Vec<Value> = (0..150)
            .map(|i| json!({"id": format!("{i:012x}"), "name": format!("learned {i}"), "where": "app", "when": "",
                            "steps": ["x"], "source": "learned", "wins": 0, "fails": 0, "uses": 0, "updated": now()}))
            .collect();
        std::fs::write(dir.join("skills.json"), json!({"version": 1, "skills": learned}).to_string()).unwrap();
        s.save("Mine", "notes", "", &["Do it".to_string()], &[]).unwrap();
        let raw: Value = serde_json::from_str(&std::fs::read_to_string(dir.join("skills.json")).unwrap()).unwrap();
        let all = raw["skills"].as_array().unwrap();
        assert_eq!(all.len(), MAX_SKILLS);
        assert!(all.iter().any(|x| x["name"] == "Mine"));
        let _ = std::fs::remove_dir_all(dir);
    }
}
