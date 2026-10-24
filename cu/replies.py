"""What the app says out loud, worded like a person would: the acknowledgement repeats back what it
heard ("Sure, I'll open Notes and make a note saying hi."), and the reply at the end says what happened
or what got in the way, not just "Done."

Also how long a pause ends a spoken request: longer when the words so far sound unfinished."""

import random
import re

# Turn the user's words into the app's: "post my tweet" -> "post your tweet".
_SWAP = {"my": "your", "mine": "yours", "me": "you", "myself": "yourself", "i": "you", "i'm": "you're",
         "i've": "you've", "i'll": "you'll", "your": "my", "yours": "mine", "you": "me"}
_ASKING = re.compile(r"^(?:(?:hey|ok(?:ay)?|so|um+|uh+)[\s,]+)*(?:(?:can|could|would|will) you(?: please)?|please|"
                     r"i (?:want|need|would like|'d like) (?:you )?to|go ahead and|i want you to)\s+", re.I)
_LEADS = ("Sure, I'll {}.", "Okay, I'll {}.", "Got it, I'll {}.", "Alright, I'll {}.")

# Words a sentence doesn't end on: hearing one of these last means the speaker is still going.
_UNFINISHED = {"and", "or", "but", "so", "then", "to", "the", "a", "an", "of", "for", "with", "in", "on", "at",
               "that", "saying", "says", "say", "like", "um", "uh", "my", "your", "is", "it", "because", "if",
               "into", "from", "about", "called", "named", "which", "who", "when", "where", "this"}


def ack(command: str) -> str:
    """A short spoken reply that shows the request was understood."""
    what = _ASKING.sub("", command.strip()).rstrip(" .?!")
    if not what:
        return "Okay."
    words = what.split()
    words = [_swap(w) for w in words]
    what = " ".join(words)
    if len(what) > 90:  # long requests: just the start, so it doesn't read the whole thing back
        what = what[:90].rsplit(" ", 1)[0] + " and the rest"
    return random.choice(_LEADS).format(what[:1].lower() + what[1:] if not what[:2].isupper() else what)


def _swap(word: str) -> str:
    core = word.strip(",;:")
    swapped = _SWAP.get(core.lower())
    if swapped is None or word.startswith(("'", '"')):
        return word
    return word.replace(core, swapped)


def result(report: str, task: str = "") -> str:
    """The spoken reply when a task ends, from the agent's report line."""
    text = re.sub(r"\s*\([^)]*\)", "", (report or "").splitlines()[0] if report else "").strip(" .")
    low = text.lower()
    if low in ("", "done", "complete", "completed", "finished"):
        return "All done." + (" Anything else?" if task else "")
    if low.startswith("done"):
        rest = text[4:].strip(" :,-")
        return f"All done: {rest}." if rest else "All done."
    if low.startswith("stopped by user") or low == "stopped":
        return "Okay, I stopped."
    if "declined" in low:
        return "Okay, I won't do that."
    if "screen corner" in low:
        return "I stopped because the mouse went into a screen corner."
    if "out of quota" in low:
        return "I'm out of quota on every model I can use, so I had to stop."
    if low.startswith("needs user"):
        return f"I need you for this one: {text.split(':', 1)[-1].strip()}."
    if low.startswith("blocked"):
        why = text.split(":", 1)[-1].strip()
        return f"I couldn't finish. I got stuck: {why}." if why else "I couldn't finish that."
    if low.startswith("error"):
        return "Something went wrong and I couldn't finish. The details are in the log."
    if low.startswith("stopped"):
        return f"I had to stop: {text.split(':', 1)[-1].strip()}."
    return text[:1].upper() + text[1:] + "."  # a model's own summary already reads like a reply


def pause_for(text: str, base: float) -> float:
    """Seconds of quiet that end an utterance: longer while the words so far sound unfinished."""
    words = re.findall(r"[a-z']+", (text or "").lower())
    if not words:
        return base
    if words[-1] in _UNFINISHED or len(words) < 3:
        return base * 2
    return base
