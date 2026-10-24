"""Python-side behaviour that doesn't need the Rust library."""

from Foundation import NSOperationQueue

from cu import indicator


def test_overlay_does_not_queue_without_a_run_loop():
    # The MCP server and CLI never run the main run loop, so queued blocks would never drain.
    indicator._ENABLED = False
    q = NSOperationQueue.mainQueue()
    before = q.operationCount()
    for i in range(200):
        indicator.show_cursor_active(i, i)
        indicator.show_click_ring(i, i)
        indicator.hide_cursor_active()
    assert q.operationCount() == before


def test_jev_never_sees_clipboard_contents(monkeypatch):
    from cu import jev
    from cu.screen import Element

    secret = "hunter2-correct-horse"
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(jev, "_clipboard_has_content", lambda: True)
    sent = []

    def fake_ask(self, state, questions, assets=None, model=None):
        sent.append(repr(state) + repr(questions))
        pick = lambda q: {"choice": next(iter(questions[q]["criteria"])), "probabilities": {}}
        return {q: pick(q) if "criteria" in questions[q] else {"noul": 1.0} for q in questions}

    monkeypatch.setattr(jev.Jev, "ask", fake_ask)
    import subprocess
    subprocess.run(["pbcopy"], input=secret.encode())  # if anything still reads the clipboard, it gets this
    try:
        j = jev.Jev()
        els = [Element(id="e0", role="AXButton", label="OK", x=1, y=1, w=10, h=10)]
        j.decide("click OK", "Finder", els, [], ["OK"], ["Finder"])
        j.check("click OK", "Finder", els, [])
    finally:
        subprocess.run(["pbcopy"], input=b"")
    assert sent and not any(secret in s for s in sent)


def test_menubar_imports_time():
    # The double-tap shortcut handler used `time` without importing it.
    import ast
    from pathlib import Path
    tree = ast.parse(Path("cu/menubar.py").read_text())
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert "time" in names
