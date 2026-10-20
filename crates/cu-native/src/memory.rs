use std::fs;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct LearnedStep {
    pub op: String,
    #[serde(default)]
    pub target: Option<String>,
    #[serde(default)]
    pub keys: Vec<String>,
    #[serde(default)]
    pub text: Option<String>,
    #[serde(default)]
    pub description: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct LearnedPattern {
    pub id: String,
    pub app: String,
    pub intent: String,
    pub keywords: Vec<String>,
    pub description: String,
    pub steps: Vec<LearnedStep>,
    pub success_count: u32,
    pub created_at: f64,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct MemoryStore {
    pub patterns: Vec<LearnedPattern>,
}

fn memory_file_path() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
    let mut path = PathBuf::from(home);
    path.push("Library");
    path.push("Application Support");
    path.push("Computer Use");
    path.push("learned_skills.json");
    path
}

impl MemoryStore {
    pub fn load() -> Self {
        let path = memory_file_path();
        if !path.exists() {
            let mut store = Self::default();
            store.seed_defaults();
            return store;
        }

        match fs::read_to_string(&path) {
            Ok(content) => serde_json::from_str(&content).unwrap_or_else(|_| {
                let mut store = Self::default();
                store.seed_defaults();
                store
            }),
            Err(_) => {
                let mut store = Self::default();
                store.seed_defaults();
                store
            }
        }
    }

    pub fn save(&self) -> Result<(), std::io::Error> {
        let path = memory_file_path();
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let json = serde_json::to_string_pretty(self).map_err(|e| {
            std::io::Error::new(std::io::ErrorKind::Other, e.to_string())
        })?;
        fs::write(path, json)
    }

    pub fn seed_defaults(&mut self) {
        let notes_share = LearnedPattern {
            id: "notes_share_messages".to_string(),
            app: "Notes".to_string(),
            intent: "Share note via Messages".to_string(),
            keywords: vec!["share".into(), "messages".into(), "invite".into(), "note".into()],
            description: "Click Share button in Notes toolbar, choose Messages in popover, type recipient, and send".to_string(),
            steps: vec![
                LearnedStep {
                    op: "CLICK_POPUP".into(),
                    target: Some("Share".into()),
                    keys: vec![],
                    text: None,
                    description: "Open Share popover in Notes toolbar".into(),
                },
                LearnedStep {
                    op: "CHOOSE_SHARE_SERVICE".into(),
                    target: Some("Messages".into()),
                    keys: vec![],
                    text: None,
                    description: "Select Messages from share options".into(),
                },
                LearnedStep {
                    op: "TYPE_RECIPIENT".into(),
                    target: None,
                    keys: vec![],
                    text: None,
                    description: "Enter contact into recipient field and confirm".into(),
                },
                LearnedStep {
                    op: "HOTKEY".into(),
                    target: None,
                    keys: vec!["command".into(), "return".into()],
                    text: None,
                    description: "Send invitation link".into(),
                },
            ],
            success_count: 5,
            created_at: 1700000000.0,
        };
        self.patterns.push(notes_share);
        let _ = self.save();
    }

    pub fn find_match(&self, task: &str, app: &str) -> Option<LearnedPattern> {
        let task_lower = task.to_lowercase();
        let app_lower = app.to_lowercase();

        for pat in &self.patterns {
            let pat_app = pat.app.to_lowercase();
            if !pat_app.contains(&app_lower) && !app_lower.contains(&pat_app) {
                continue;
            }
            let match_score = pat
                .keywords
                .iter()
                .filter(|kw| task_lower.contains(&kw.to_lowercase()))
                .count();

            if match_score >= 2 || (pat.keywords.len() == 1 && match_score == 1) {
                return Some(pat.clone());
            }
        }
        None
    }

    pub fn record_learning(
        &mut self,
        app: &str,
        intent: &str,
        keywords: Vec<String>,
        steps: Vec<LearnedStep>,
        description: &str,
    ) -> LearnedPattern {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs_f64();
        let id = format!("learned_{}_{}", app.to_lowercase().replace(' ', "_"), now as u64);
        let pattern = LearnedPattern {
            id,
            app: app.to_string(),
            intent: intent.to_string(),
            keywords,
            description: if description.is_empty() { intent.to_string() } else { description.to_string() },
            steps,
            success_count: 1,
            created_at: now,
        };
        self.patterns.insert(0, pattern.clone());
        let _ = self.save();
        pattern
    }
}

// C FFI interface
use std::ffi::{CStr, CString};
use std::os::raw::c_char;

#[no_mangle]
pub extern "C" fn cu_memory_find_match(task: *const c_char, app: *const c_char) -> *mut c_char {
    if task.is_null() || app.is_null() {
        return std::ptr::null_mut();
    }
    let task_str = match unsafe { CStr::from_ptr(task) }.to_str() {
        Ok(s) => s,
        Err(_) => return std::ptr::null_mut(),
    };
    let app_str = match unsafe { CStr::from_ptr(app) }.to_str() {
        Ok(s) => s,
        Err(_) => return std::ptr::null_mut(),
    };

    let store = MemoryStore::load();
    if let Some(matched) = store.find_match(task_str, app_str) {
        if let Ok(json) = serde_json::to_string(&matched) {
            if let Ok(c_str) = CString::new(json) {
                return c_str.into_raw();
            }
        }
    }
    std::ptr::null_mut()
}

#[no_mangle]
pub extern "C" fn cu_memory_save_pattern(pattern_json: *const c_char) -> bool {
    if pattern_json.is_null() {
        return false;
    }
    let json_str = match unsafe { CStr::from_ptr(pattern_json) }.to_str() {
        Ok(s) => s,
        Err(_) => return false,
    };
    if let Ok(pat) = serde_json::from_str::<LearnedPattern>(json_str) {
        let mut store = MemoryStore::load();
        store.patterns.insert(0, pat);
        return store.save().is_ok();
    }
    false
}

#[no_mangle]
pub extern "C" fn cu_memory_list_patterns() -> *mut c_char {
    let store = MemoryStore::load();
    if let Ok(json) = serde_json::to_string(&store.patterns) {
        if let Ok(c_str) = CString::new(json) {
            return c_str.into_raw();
        }
    }
    std::ptr::null_mut()
}

#[no_mangle]
pub extern "C" fn cu_memory_free_string(s: *mut c_char) {
    if !s.is_null() {
        unsafe {
            let _ = CString::from_raw(s);
        }
    }
}
