# Computer Use (lite)

Control the whole Mac, not just a browser. Any AI can drive it through the built-in MCP server, and the menu bar app can run tasks on its own. Every API key is optional.

## Install
1. Open `Computer Use <version>.dmg` (next to this Project folder) and drag **Computer Use** into Applications.
2. Open it. A cursor icon appears in the menu bar; there's no Dock icon and no window.
3. Go to **Settings › Allow Accessibility…** and **Allow Screen Recording…**, then add Computer Use.
4. Optional: **Settings › API Keys…** (they're stored in the macOS Keychain) and **Settings › Open at Login**.
5. Optional: under **Connect AI Apps**, tick Antigravity, VS Code, Cursor, Windsurf or Claude, then restart that app.

## Let any AI drive the Mac (MCP)
The app bundles `cu-mcp`, a native MCP server written in Rust. It needs no API key: the AI you already use does the thinking, and `cu-mcp` does the looking and clicking.

- **Connect AI Apps** writes `cu-mcp` into the MCP config of Antigravity, VS Code, Cursor, Windsurf or Claude.
- **Any other MCP client:** add a stdio server:
  ```json
  {"command": "/Applications/Computer Use.app/Contents/Frameworks/cu-mcp", "args": []}
  ```
  Use the path where your copy of `cu-mcp` actually is. When you build from source, it's `crates/cu-native/target/release/cu-mcp`.
- **Permissions belong to the app that starts the server.** That's your editor or AI app, so give it Accessibility and Screen Recording too. The `check_permissions` tool tells you what's missing.

**Tools:** `take_screenshot`, `list_screen_elements`, `click_element`, `click_at`, `type_text`, `press_keys`, `scroll`, `drag`, `move_mouse`, `open_app`, `open_url`, `select_menu`, `read_element`, `list_windows`, `wait_for_change`, `speak`, `computer_task`, `check_permissions`.

All x/y coordinates are pixels of the latest screenshot. `computer_task` hands a whole task to the app's own agent, which needs its own keys (see Modes).

## Modes (the menu bar agent)
Pick one under **Mode**. Modes are named by role. The built-in vision model is currently Gemini, so the vision modes need a Gemini key.

| Mode | Keys needed | Who thinks | Who clicks |
|---|---|---|---|
| **Auto** | any | the best mode your keys and remaining quota allow | |
| **Vision + Jev** | Gemini and Jev | the vision model plans a few steps at a time | Jev, with the vision model when Jev is unsure |
| **Jev Only** | Jev | Jev | Jev |
| **Vision Only** | Gemini | the vision model | the vision model |

**Jev Only:** Jev works from text only and doesn't generate text. Each turn, the app reads the accessibility tree of the frontmost app. In a single request, Jev chooses the next action, the element it applies to, the phrase from your task to type, and the installed app to open. Put text you want typed in quotes, or after words like *saying*, *titled* or *search for*.

## The bar
One shortcut opens a floating bar. The default is **double-tap Control**. Change it in **Settings › Shortcut**: pick a preset (double-tap Option, Command, Shift or Fn) or choose **Record Shortcut…** to record a key combination.

- Press the shortcut once to open the bar, again to start listening, and again to send.
- The bar has a mic button, your request, the mode and a send button.
- **Return** sends. **Esc** stops listening, or closes the bar. **Up/Down** steps through recent tasks.
- Clicking away closes the bar, unless it's listening.
- You can also open it with **Ask Computer Use…** in the menu.

## Menu
- Status, **Last** (once a task has finished), **Stop** (⌘.)
- **Ask Computer Use…**
- **Mode**, **Voice**, **Usage**
- **While Working**, **Connect AI Apps**, **Settings**
- **Quit Computer Use**

## Voice
Voice options are in the **Voice** menu.

- **Start Listening When the Bar Opens** (on by default): say what you want, then pause. Your words appear in the bar as you speak, and the task starts when you stop. Press the shortcut again to finish early.
- **Hands-free — say “Computer, …”:** always listening, and acts only on speech that starts with "Computer" or "Hey computer".
- **"Stop"** halts the running task. A new instruction mid-task replaces the current one.
- **Speak Replies:** says "On it", reads any confirmation questions aloud, and gives a short summary when the task ends.
- **Narrate While Working:** talks you through each step ("Opening Notes", "Clicking New Note"), whichever model is acting: Jev, the vision model (in its own words), or an AI driving through MCP. Through MCP, an AI can also call `speak` to talk to you directly. The mic ignores the app's own voice.

### Listen With (speech to text, all on your Mac)
| Model | Size | Notes |
|---|---|---|
| Apple | built in | The default; nothing to download. |
| Moonshine Tiny / Base | 110 / 250 MB | Light and fast. Needs the Voice Pack. |
| Parakeet v2 (English) / v3 (25 languages) | 2.5 GB | The most accurate; runs on the Mac's GPU. Needs the Voice Pack. |

**Add a custom model:** **Voice › Listen With › Add Custom Model…**, then paste either:
- **A Hugging Face model name.** Parakeet models converted for MLX work, e.g. `mlx-community/parakeet-tdt_ctc-0.6b-ja`, and so do Moonshine ONNX models.
- **A folder on your Mac.** Parakeet needs `config.json` and `model.safetensors`; Moonshine needs `encoder_model.onnx` and `decoder_model_merged.onnx`.

If the name doesn't say which kind of model it is, add `parakeet:` or `moonshine:` to the front. A model downloads the first time you use it. Whisper models aren't supported, because their MLX package needs PyTorch.

### Speaking Voice
- **Mac:** any voice installed on your Mac. **Get More Voices…** opens System Settings, where you can download the Premium and Enhanced ones.
- **Kokoro:** natural-sounding local voices (Heart, Bella, Michael, Emma, George and more). Needs the Voice Pack; the model (about 120 MB) downloads on first use.
- **Gemini:** Google's 30 voices (Kore, Puck, Charon…). Needs a Gemini key, runs in the cloud, and counts toward your Gemini usage.

Picking a voice plays a short preview.

### Voice Pack
`Computer Use Voice Pack <version>.zip` is next to the DMG. It's a separate download so that the app download stays small. Install it from **Voice › Install Voice Pack…**; the app also looks for it in Downloads. Choosing a model or voice that needs the pack offers to install it.

## Usage and switching
- **Usage** in the menu shows each model's use in the current window, what's left, and when it resets. Gemini resets at midnight Pacific; Jev resets on your billing day.
- Neither provider has an API that reports your remaining quota, so the app counts its own requests. Click a line to enter that model's limit from AI Studio or your TypeSafe dashboard. The count doesn't include usage from outside this app.
- When a provider replies "out of quota", the app marks that model as out until it resets.
- **While Working › Switch Models When One Runs Out:** steps down the Gemini models (3.8 Flash → 3.5 Flash-Lite → 3 Flash preview), and moves between Jev and the vision model, carrying on from the current screen.

## Updates
- The app checks GitHub Releases at launch and every 6 hours.
- With **Settings › Update Automatically** on (the default), it downloads a new version and installs it once no task is running. It replaces itself in Applications, updates the Voice Pack if you have it, and relaunches.
- **Settings › Check for Updates…** checks right away. It becomes **Install Update X…** when an update is waiting.
- While the repo is private, the app downloads using your `gh` login (or a `GITHUB_TOKEN` in the Keychain).
- **Permissions:** builds are signed with a local certificate, "Computer Use Local Signing", so macOS keeps the Accessibility and Screen Recording permissions across updates. If you're coming from an older ad-hoc-signed build, you need to grant them again one time.

**Publishing an update:**
1. Bump `__version__` in `cu/__init__.py`.
2. Commit.
3. Run `./packaging/release.sh "What changed"`. It builds, pushes, and creates the GitHub release with the DMG and Voice Pack attached.

## Safety
- The app asks before clicking send, buy or delete-style buttons, unless you tick **While Working › Don't Ask Before Send, Buy or Delete**. **Ask Before Every Action** makes it ask before every step.
- **Emergency stop:** move the mouse hard into any screen corner. This stops both the app and `cu-mcp`. You can also use **Stop** (⌘.).
- Your clipboard never leaves your Mac.

## Development
- `uv run pytest` runs the Python tests.
- `cargo test` in `crates/cu-native` runs the Rust tests. These include a read-only stdio test of the real `cu-mcp` binary.
- `cargo build --release` in `crates/cu-native` builds `target/release/cu-mcp`.
- `uv run python -m cu "task"` (CLI), `uv run python -m cu --doctor`, `uv run python -m cu --elements`
- `./packaging/build.sh` builds the app and writes `Computer Use <version>.dmg` and the Voice Pack next to the Project folder, replacing the previous ones.
- `./packaging/release.sh "notes"` publishes a release for the updater.
- **Logs:** `~/Library/Logs/Computer Use/` holds `tasks.log` (what each task did; **Settings › Open Log**), `app.log` and `crash.log`. Each rotates at 5 MB, keeping one older copy.
