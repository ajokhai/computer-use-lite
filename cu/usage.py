"""Usage tracker. Counts every request this app makes, per provider, per reset window, and
remembers when a provider said it was out of quota. Shared across processes via a JSON file."""

import fcntl
import json
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import DATA, load_settings

PACIFIC = ZoneInfo("America/Los_Angeles")  # Gemini daily quotas reset at midnight Pacific
JEV_USD_PER_TOKEN = 0.042 / 1e6
FILE = DATA / "usage.json"


class QuotaExhausted(Exception):
    def __init__(self, provider: str, until: float | None, detail: str = ""):
        super().__init__(f"{provider} is out of quota{f' ({detail})' if detail else ''}")
        self.provider, self.until = provider, until


def next_reset(provider: str, now: datetime | None = None) -> datetime:
    """End of the current window: midnight Pacific for Gemini, the monthly reset day for Jev."""
    if provider == "jev":
        day = int(load_settings()["limits"].get("jev", {}).get("reset_day") or 1)
        now = now or datetime.now().astimezone()
        candidate = now.replace(day=min(day, 28), hour=0, minute=0, second=0, microsecond=0)
        if candidate <= now:
            month = candidate.month % 12 + 1
            candidate = candidate.replace(year=candidate.year + (month == 1), month=month)
        return candidate
    now = (now or datetime.now(PACIFIC)).astimezone(PACIFIC)
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


@contextmanager
def _locked():
    DATA.mkdir(parents=True, exist_ok=True)
    with open(DATA / "usage.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = json.loads(FILE.read_text())
        except (OSError, ValueError):
            data = {}
        yield data
        FILE.write_text(json.dumps(data, indent=2))


def _bucket(data: dict, provider: str) -> dict:
    """The provider's counters for the current window, rolled over if the window ended."""
    reset = next_reset(provider).timestamp()
    b = data.get(provider)
    if not b or b.get("window_end") != reset:
        b = {"window_end": reset, "requests": 0, "input_tokens": 0, "output_tokens": 0,
             "exhausted_until": (b or {}).get("exhausted_until")}
        data[provider] = b
    return b


def record(provider: str, input_tokens: int = 0, output_tokens: int = 0):
    with _locked() as data:
        b = _bucket(data, provider)
        b["requests"] += 1
        b["input_tokens"] += input_tokens
        b["output_tokens"] += output_tokens


def mark_exhausted(provider: str, until: float | None):
    """until=None means 'until the window resets'."""
    with _locked() as data:
        b = _bucket(data, provider)
        b["exhausted_until"] = until or b["window_end"]


def clear_exhausted(provider: str | None = None):
    with _locked() as data:
        for name, b in list(data.items()):
            if isinstance(b, dict) and provider in (None, name):
                b["exhausted_until"] = None


def status(provider: str) -> dict:
    """Current-window usage, the configured limit, and whether the provider is usable."""
    with _locked() as data:
        b = dict(_bucket(data, provider))
    limit = load_settings()["limits"].get(provider, {})
    now = time.time()
    out = {**b, "provider": provider, "reset_at": b["window_end"], "limit": None, "used": None}
    if provider == "jev":
        out["used"] = b["input_tokens"] * JEV_USD_PER_TOKEN
        out["limit"] = limit.get("usd_per_month")
    else:
        out["used"] = b["requests"]
        out["limit"] = limit.get("requests_per_day")
    over_limit = out["limit"] is not None and out["used"] >= out["limit"]
    blocked_until = b.get("exhausted_until") if (b.get("exhausted_until") or 0) > now else None
    out["available"] = not over_limit and blocked_until is None
    out["blocked_until"] = b["window_end"] if over_limit else blocked_until
    return out


def available(provider: str) -> bool:
    return status(provider)["available"]


def is_quota_error(err: Exception) -> tuple[bool, float | None]:
    """(is it a quota/rate error, when to retry). Daily quotas block until reset, per-minute ones briefly."""
    code = getattr(err, "status_code", None) or getattr(err, "code", None)
    text = str(err)
    if code == 429 or "RESOURCE_EXHAUSTED" in text or "quota" in text.lower():
        if "PerMinute" in text or "per minute" in text.lower():
            return True, time.time() + 60
        return True, None
    if code in (402,) or "insufficient" in text.lower() or "billing" in text.lower():
        return True, None
    return False, None


def fmt_until(ts: float) -> str:
    secs = max(0, int(ts - time.time()))
    d, rem = divmod(secs, 86400)
    h, m = divmod(rem // 60, 60)
    return f"{d}d {h}h" if d else f"{h}h {m}m" if h else f"{m}m"


def pretty(provider: str) -> str:
    return "Jev" if provider == "jev" else "Gemini " + provider.removeprefix("gemini-").replace("-", " ").title()


def summary_lines(models: list[str]) -> list[str]:
    lines = []
    for p in [*models, "jev"]:
        s = status(p)
        name = pretty(p)
        if p == "jev":
            used = f"${s['used']:.3f}" + (f" of ${s['limit']:.2f}" if s["limit"] else "") + " this month"
        else:
            used = f"{s['used']}" + (f" / {s['limit']}" if s["limit"] else "") + " requests today"
        if s["limit"] and s["used"] < s["limit"]:
            left = s["limit"] - s["used"]
            used += f" · {left:.2f} left" if p == "jev" else f" · {left} left"
        tail = f"out · back in {fmt_until(s['blocked_until'])}" if not s["available"] else f"resets in {fmt_until(s['reset_at'])}"
        lines.append(f"{name}: {used} · {tail}")
    return lines
