"""Spoken replies read like a person talking, and a pause mid-sentence doesn't cut the speaker off."""

from cu import replies


def test_the_acknowledgement_repeats_back_what_was_asked():
    said = replies.ack("Can you please open my notes and make a note saying hi")
    assert said.endswith("open your notes and make a note saying hi.") and said != "On it."
    assert replies.ack("post a tweet on X").endswith("I'll post a tweet on X.")
    assert "and the rest" in replies.ack("write " + "a very long request " * 10)


def test_the_reply_at_the_end_says_what_happened():
    assert replies.result("done (verified; jev p=0.91)", "open notes") == "All done. Anything else?"
    assert replies.result("I posted the tweet and it shows on your profile") == "I posted the tweet and it shows on your profile."
    assert "stuck" in replies.result("blocked: Jev is stuck at 'CLICK'")
    assert replies.result("stopped by user") == "Okay, I stopped."
    assert "need you" in replies.result("needs user: enter your 2FA code")


def test_an_unfinished_sentence_gets_a_longer_pause():
    assert replies.pause_for("open notes and", 2.0) == 4.0
    assert replies.pause_for("post a tweet saying", 2.0) == 4.0
    assert replies.pause_for("open my notes app please", 2.0) == 2.0
