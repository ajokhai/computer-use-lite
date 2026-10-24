"""Lessons and recovery without a screen or network: lessons merge, stay bounded and come back for
the right place; exports carry no personal details; a failed attempt is assessed and retried with
the new guidance, what worked is remembered, and a person's stop is never retried."""

import json

from cu import memory, recovery


def test_lessons_merge_and_come_back_for_the_right_place():
    memory.record("x.com", "post a tweet", "clicked a heading", "click 'Post text' first", "failed", "jev")
    memory.record("x.com", "post a tweet", "clicked a heading again", "Click 'Post text' first", "worked", "claude")
    memory.record("notes", "make a note", "typed nowhere", "press cmd+n first")
    lessons = memory.all_lessons()
    assert len(lessons) == 2
    x = next(l for l in lessons if l["where"] == "x.com")
    assert x["seen"] == 2 and x["outcome"] == "worked"
    got = memory.recall(memory.places("go to x.com and tweet", "Google Chrome"), "tweet")
    assert [l["where"] for l in got] == ["x.com"]
    assert memory.all_lessons()[0]["used"] == 1
    assert "→ click 'Post text' first (worked)" in memory.as_text(got)


def test_lessons_stay_bounded(monkeypatch):
    monkeypatch.setattr(memory, "MAX_LESSONS", 5)
    for i in range(12):
        memory.record("app", "task", "x" * 1000, f"fix {i}")
    lessons = memory.all_lessons()
    assert len(lessons) == 5
    assert all(len(l["what"]) <= memory.MAX_TEXT for l in lessons)


def test_export_removes_personal_details_and_imports_elsewhere(tmp_path):
    memory.record("x.com", "post \"my secret plan\" as @ajokhai",
                  "typed \"my secret plan\" for josh@example.com at https://x.com/ajokhai/status/123456789",
                  "click 'Post text', then open https://x.com/compose/post, paste key sk-abcdef123456 later", "worked")
    out = tmp_path / "lessons-export.json"
    assert memory.export(out, names=["Josh"]) == 1
    text = out.read_text()
    assert "'Post text'" in text  # UI labels are kept: they're what makes a lesson useful
    for secret in ("secret plan", "ajokhai", "josh@", "123456789", "sk-abcdef", "status", "Josh"):
        assert secret not in text, secret
    data = json.loads(text)
    assert set(data["lessons"][0]) == {"where", "what", "fix", "outcome", "seen"}
    memory.forget()
    assert memory.import_(out) == 1
    assert memory.all_lessons()[0]["model"] == "imported"


def run(attempts, verdicts=None, log=None):
    """A Recovery whose attempts and assessments are scripted."""
    tasks = []

    def attempt(task):
        tasks.append(task)
        return attempts.pop(0)
    verdicts = list(verdicts or [])
    rec = recovery.Recovery(attempt=attempt, log_tail=lambda: list(log or []),
                            ask_json=lambda system, text: verdicts.pop(0) if verdicts else None,
                            where=lambda: "Google Chrome", log=lambda s: None, model="test")
    return rec, tasks, rec.run("go to x.com and post a tweet")


def test_a_failure_is_assessed_and_retried_with_new_guidance():
    rec, tasks, (ok, report) = run(
        [(False, "blocked: Jev is stuck at 'CLICK'"), (True, "done")],
        [{"diagnosis": "clicked a heading, not the composer", "next_try": "click the 'Post text' box, then type",
          "lesson": "On x.com the composer is the 'Post text' box"}])
    assert ok and report == "done"
    assert len(tasks) == 2 and recovery.NOTES not in tasks[0]
    assert "click the 'Post text' box" in tasks[1] and recovery.strip_notes(tasks[1]) == "go to x.com and post a tweet"
    learned = memory.all_lessons()
    assert {l["where"] for l in learned} == {"google chrome", "x.com"}
    assert all(l["outcome"] == "worked" and "Post text" in l["fix"] for l in learned)


def test_the_next_run_starts_with_what_was_learned():
    run([(False, "blocked: stuck"), (True, "done")], [{"diagnosis": "d", "next_try": "use the 'Post text' box"}])
    _, tasks, _ = run([(True, "done")])
    assert "Lessons from earlier runs" in tasks[0] and "Post text" in tasks[0]


def test_without_a_vision_model_known_patterns_guide_the_retry():
    _, tasks, (ok, _) = run([(False, "blocked: nothing"), (False, "blocked: nothing"), (False, "blocked: nothing")],
                            log=["FAILED: TYPE \"hi\" (no text field has focus, so nothing was typed)"])
    assert not ok and len(tasks) == 3
    assert "Click the text box itself" in tasks[1]
    assert memory.all_lessons()[0]["outcome"] == "failed"  # what didn't work is kept too


def test_a_persons_stop_is_never_retried():
    for report in ("stopped by user", "stopped: user declined", "stopped: mouse moved into a screen corner",
                   "needs user: enter your 2FA code"):
        _, tasks, (ok, _) = run([(False, report), (True, "done")])
        assert len(tasks) == 1 and not ok, report


def test_give_up_stops_retrying():
    _, tasks, _ = run([(False, "blocked"), (True, "done")], [{"diagnosis": "needs a paid plan", "next_try": "-",
                                                             "give_up": True}])
    assert len(tasks) == 1


def test_an_assessor_error_falls_back_to_patterns():
    tasks = []
    attempts = [(False, "stopped: hit the round limit"), (True, "done")]

    def boom(system, text):
        raise RuntimeError("vision model down")
    rec = recovery.Recovery(attempt=lambda t: tasks.append(t) or attempts.pop(0), log_tail=list, ask_json=boom,
                            log=lambda s: None)
    assert rec.run("open notes")[0]
    assert "Go more directly" in tasks[1]


def test_jev_reads_notes_as_hints_not_as_text_to_type():
    task = recovery.with_notes("type 'hello' into the note", ["This time: click the Note field first"])
    assert recovery.notes_of(task) == ["This time: click the Note field first"]
    assert recovery.strip_notes(task) == "type 'hello' into the note"
