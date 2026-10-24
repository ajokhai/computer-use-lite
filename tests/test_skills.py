"""Skills without a screen or network: made by the user, learned only when worth keeping, handed to
the next run that fits, dropped when they keep failing, bounded, and shared without personal details."""

import json
import shutil
import subprocess

import pytest

from cu import recovery, skills

POST = {"name": "Post on X", "where": "x.com", "when": "Posting a tweet on X",
        "steps": ["Open https://x.com/compose/post", "Click the 'Post text' box", "Type {text}", "Click Post"],
        "notes": ["The 'What's happening' heading isn't the composer"], "worth_keeping": True}
LOG = ["  jev → OPEN_APP Google Chrome", "  jev → CLICK TextArea \"Post text\"", "  jev → TYPE \"hello there\"",
       "  jev → CLICK Button \"Post\""]


def test_user_skill_is_saved_found_and_shown():
    s = skills.save(POST, "user")
    assert s["source"] == "user" and len(s["steps"]) == 4
    found = skills.find("post a tweet on x.com saying hi", ["google chrome", "x.com"])
    assert [f["name"] for f in found] == ["Post on X"]
    assert "1. Open https://x.com/compose/post" in skills.as_text(found[0])
    assert skills.find("make a note in Notes", ["notes"]) == []


def test_similar_skills_merge_and_a_learned_one_never_overwrites_the_users():
    skills.save(POST, "user")
    skills.save({**POST, "name": "Post a Tweet on X", "steps": ["something worse"]}, "learned")
    kept = skills.all_skills()
    assert len(kept) == 1 and kept[0]["steps"] == POST["steps"] and kept[0]["source"] == "user"


def test_auto_learning_is_selective():
    ask = lambda system, text: dict(POST)
    assert skills.learn("post \"hi\" on x.com", LOG, ok=False, retried=True, ask_json=ask) is None  # failures teach lessons, not skills
    assert skills.learn("post \"hi\" on x.com", LOG, ok=True, retried=False, ask_json=ask) is None  # once is luck
    assert skills.learn("post \"yo\" on x.com", LOG, ok=True, retried=False, ask_json=ask)["source"] == "learned"
    trivial = lambda system, text: {**POST, "name": "Open Notes", "where": "notes", "worth_keeping": False}
    assert skills.learn("open notes", LOG, ok=True, retried=True, ask_json=trivial) is None  # judged not reusable
    assert len(skills.all_skills()) == 1


def test_a_hard_won_success_is_kept_at_once():
    s = skills.learn("post \"hi\" on x.com", LOG, ok=True, retried=True, ask_json=lambda s, t: dict(POST))
    assert s and s["name"] == "Post on X"


def test_learned_skills_that_keep_failing_are_dropped_but_the_users_stay():
    learned = skills.save({**POST, "name": "Send a DM", "when": "direct messages"}, "learned")
    mine = skills.save(POST, "user")
    for _ in range(3):
        skills.result(learned["id"], ok=False)
        skills.result(mine["id"], ok=False)
    assert [s["name"] for s in skills.all_skills()] == ["Post on X"]


def test_skills_stay_bounded(monkeypatch):
    monkeypatch.setattr(skills, "MAX_SKILLS", 3)
    for i in range(6):
        skills.save({"name": f"Thing {i} unique{i}", "where": f"app{i}", "steps": ["x"] * 50}, "learned")
    kept = skills.all_skills()
    assert len(kept) == 3 and all(len(s["steps"]) <= skills.MAX_STEPS for s in kept)


def test_saving_a_finished_task_without_a_vision_model_keeps_its_actions():
    s = skills.from_run("post \"hello there\" on x.com", LOG, ask_json=lambda s, t: None, name="Tweet Something")
    assert s["name"] == "Tweet Something" and s["source"] == "user" and s["where"] == "x.com"
    assert "hello there" not in json.dumps(s)  # the user's own words aren't part of the skill


def test_packs_share_without_personal_details(tmp_path):
    skills.save({**POST, "steps": [*POST["steps"], "Mention @ajokhai and email josh@example.com"]}, "user")
    out = tmp_path / "pack.json"
    assert skills.export(out) == 1
    text = out.read_text()
    assert "ajokhai" not in text and "josh@" not in text and "'Post text'" in text
    for s in skills.all_skills():
        skills.delete(s["id"])
    assert skills.import_(out) == 1 and skills.all_skills()[0]["source"] == "imported"
    assert skills.categories() == {"x.com": skills.all_skills()}


def test_recovery_hands_over_a_fitting_skill_and_scores_it():
    s = skills.save(POST, "user")
    tasks = []
    rec = recovery.Recovery(attempt=lambda t: tasks.append(t) or (True, "done"), log_tail=lambda: LOG,
                            ask_json=lambda system, text: None, where=lambda: "Google Chrome", log=lambda m: None)
    assert rec.run("post a tweet on x.com")[0]
    assert "A skill that fits" in tasks[0] and "Click the 'Post text' box" in tasks[0]
    assert next(x for x in skills.all_skills() if x["id"] == s["id"])["wins"] == 1


def test_from_video_uses_a_contact_sheet(monkeypatch):
    monkeypatch.setattr(skills, "contact_sheet", lambda path, **k: b"png")
    seen = []
    s = skills.from_video("/tmp/how-to.mov", lambda system, text, png: seen.append(png) or dict(POST))
    assert seen == [b"png"] and s["source"] == "video"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg to make a test video")
def test_contact_sheet_reads_a_real_video(tmp_path):
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10", "-t", "2",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    png = skills.contact_sheet(str(video), frames=6, width=600)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
