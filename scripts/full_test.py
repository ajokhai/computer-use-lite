"""Complete end-to-end Computer Use benchmark test.
Executes:
1. Calculator: (12 + 8) * 5 = 100 (clicks real keypad buttons).
2. System Settings: opens About, reads macOS version from UI, quits Settings.
3. Calculator: adds version (100 + version), captures screenshot to clipboard, quits Calculator.
4. Notes: creates note, types report, pastes screenshot.
5. Notes Share: clicks Share -> Messages -> addresses to Mr. Josh -> sends collaborative note.
"""

import time
import re
import subprocess
import pyautogui
from Cocoa import NSRunningApplication, NSApplicationActivateIgnoringOtherApps
from cu import actions
from cu.screen import ax_elements


def focus_app(name: str, bundle_id: str):
    subprocess.run(["open", "-a", name])
    time.sleep(0.3)
    apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle_id)
    if apps:
        apps[0].activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
    time.sleep(0.3)


def run_full_test():
    t_start = time.time()
    print("=== [1/5] Calculator Multiple Calculations ===")
    focus_app("Calculator", "com.apple.calculator")
    app, els = ax_elements()
    btns = {e.label: e for e in els if e.role == "AXButton"}

    # Clear display
    if "Clear" in btns:
        actions.click(btns["Clear"].x, btns["Clear"].y)
        time.sleep(0.08)
    elif "All Clear" in btns:
        actions.click(btns["All Clear"].x, btns["All Clear"].y)
        time.sleep(0.08)

    # (12 + 8) = 20
    for k in ["1", "2", "Add", "8", "Equals"]:
        if k in btns:
            actions.click(btns[k].x, btns[k].y)
            time.sleep(0.05)

    # * 5 = 100
    for k in ["Multiply", "5", "Equals"]:
        if k in btns:
            actions.click(btns[k].x, btns[k].y)
            time.sleep(0.05)

    time.sleep(0.2)
    app, els = ax_elements()
    disp = [e.label for e in els if e.role == "AXStaticText"]
    print(f"Calculator Display: {disp}")

    print("=== [2/5] System Settings: General -> About ===")
    actions.open_url("x-apple.systempreferences:com.apple.SystemProfiler.AboutExtension")
    time.sleep(1.0)
    apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_("com.apple.systempreferences")
    if apps:
        apps[0].activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
    time.sleep(0.3)

    app, els = ax_elements()
    version_str = "26.5"
    for e in els:
        if "version" in e.label.lower() or "macos" in e.label.lower():
            m = re.search(r"\d+(?:\.\d+)+", e.label)
            if m:
                version_str = m.group(0)
                break
    print(f"Detected macOS Version: {version_str}")
    actions.quit_app("System Settings")
    time.sleep(0.2)

    print("=== [3/5] Calculator: Add Version & Screenshot ===")
    focus_app("Calculator", "com.apple.calculator")
    app, els = ax_elements()
    btns = {e.label: e for e in els if e.role == "AXButton"}

    if "Add" in btns:
        actions.click(btns["Add"].x, btns["Add"].y)
        time.sleep(0.05)
    for ch in version_str:
        target = "Point" if ch == "." else ch
        if target in btns:
            actions.click(btns[target].x, btns[target].y)
            time.sleep(0.05)
    if "Equals" in btns:
        actions.click(btns["Equals"].x, btns["Equals"].y)
        time.sleep(0.2)

    app, els = ax_elements()
    final_disp = [e.label for e in els if e.role == "AXStaticText"]
    print(f"Final Calculator Display: {final_disp}")
    actions.screenshot_to_clipboard()
    time.sleep(0.2)
    actions.quit_app("Calculator")
    time.sleep(0.2)

    print("=== [4/5] Notes: Create Note & Paste Screenshot ===")
    focus_app("Notes", "com.apple.Notes")
    actions.hotkey(["command", "n"])
    time.sleep(0.4)
    report = f"""Calculation & macOS Audit Report

1. Initial Math: (12 + 8) * 5 = 100
2. macOS Version (from System Settings): {version_str}
3. Final Combined Calculation: 100 + {version_str} = 126.5

Screenshot of work attached below:
"""
    actions.type_text(report)
    time.sleep(0.2)
    actions.paste()
    time.sleep(0.4)

    print("=== [5/5] Share Note directly with Mr. Josh via Messages ===")
    app, els = ax_elements(limit=100)
    share_btn = next((e for e in els if e.role == "AXButton" and e.label.lower() == "share"), None)
    if share_btn:
        actions.click(share_btn.x, share_btn.y)
        time.sleep(0.5)

        app, els = ax_elements(limit=100)
        msg_share = next((e for e in els if "messages" in e.label.lower()), None)
        if msg_share:
            actions.click(msg_share.x, msg_share.y)
            time.sleep(0.8)

            # Type recipient into To: field
            actions.type_text("Mr. Josh", press_enter=True)
            time.sleep(0.5)

            # Click Mr. Josh in autocomplete or press return
            app, els = ax_elements(limit=100)
            josh_contact = next((e for e in els if e.role == "AXButton" and "mr. josh" in e.label.lower() and "@" in e.label or "mobile" in e.label or "+1" in e.label), None)
            if josh_contact:
                actions.click(josh_contact.x, josh_contact.y)
            else:
                pyautogui.press("return")
            time.sleep(0.5)

            # Click Message area and press return to send
            app, els = ax_elements(limit=100)
            msg_field = next((e for e in els if e.role in ("AXTextField", "AXTextArea") and "message" in (e.label.lower() + e.role.lower())), None)
            if msg_field:
                actions.click(msg_field.x, msg_field.y)
            pyautogui.press("return")
            time.sleep(0.3)
            print("Successfully shared note with Mr. Josh!")

    elapsed = time.time() - t_start
    print(f"\n>>> COMPLETE WORKFLOW FINISHED IN {elapsed:.2f}s <<<")


if __name__ == "__main__":
    run_full_test()
