"""The vision models with fake SDK clients and no real input or network: which model is picked,
falling back across providers on quota, each provider's actions mapped to cu.actions with
screenshot pixels scaled to screen points, and the corner-abort and confirmation paths."""

from types import SimpleNamespace as NS

import pytest

from cu import actions, agent, config, usage, vision
from cu.screen import Element
from cu.vision import base, claude, compat, openai as oai

SEND = Element(id="e0", role="AXButton", label="Buy Now", x=200, y=100, w=60, h=30)
NOTE = Element(id="e1", role="AXTextField", label="Note", x=300, y=400, w=200, h=30)


class FakeShot(base.Shot):
    """A 640x400 screenshot of a 1280x800-point screen: every pixel is 2 points."""

    def __init__(self, max_width: int = 1280):
        self.png, self.width, self.height, self.scale = b"png", 640, 400, 2.0


@pytest.fixture
def desk(monkeypatch):
    """Record actions instead of performing them; never touch the usage file or the real screen."""
    done = []
    for mod in (claude, oai, compat):
        monkeypatch.setattr(mod, "Shot", FakeShot)
    monkeypatch.setattr(compat, "ax_elements", lambda limit=200: ("Notes", [SEND, NOTE]))
    monkeypatch.setattr(base, "ax_elements", lambda limit=200: ("Notes", [SEND, NOTE]))
    monkeypatch.setattr(base, "element_at", lambda x, y: SEND if (x, y) == (200, 100) else None)
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    monkeypatch.setattr(usage, "available", lambda m: True)
    monkeypatch.setattr(usage, "record", lambda *a, **k: None)
    monkeypatch.setattr(usage, "mark_exhausted", lambda *a, **k: None)
    monkeypatch.setattr(actions, "click", lambda x, y, button="left", clicks=1: done.append(f"{button}x{clicks} {x},{y}"))
    monkeypatch.setattr(actions, "type_text", lambda text, **k: done.append(f"type {text}"))
    monkeypatch.setattr(actions, "hotkey", lambda keys: done.append("+".join(keys)))
    monkeypatch.setattr(actions, "scroll", lambda x, y, d="down", amount=300: done.append(f"scroll {d} {amount} at {x},{y}"))
    monkeypatch.setattr(actions, "drag", lambda *p: done.append(f"drag {p}"))
    monkeypatch.setattr(actions, "move_to", lambda x, y, **k: done.append(f"move {x},{y}"))
    monkeypatch.setattr(actions, "open_app", lambda name: done.append(f"open {name}") or True)
    return done


def settings(**over):
    s = {**config.DEFAULTS, **over}
    s["limits"] = dict(config.DEFAULTS["limits"])
    return s


# --- picking a model -------------------------------------------------------------


def test_auto_tries_providers_with_keys_in_order(monkeypatch):
    for n in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setattr(vision, "load_settings", lambda: settings())
    assert [p for p, _ in vision.order() if vision.has_key(p)] == ["gemini"]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    assert [p for p, _ in vision.order() if vision.has_key(p)] == ["claude", "gemini"]
    assert vision.order()[0][1] == ["claude-opus-5-5", "claude-sonnet-5-5"]


def test_a_chosen_model_goes_first_and_alone_without_switching(monkeypatch):
    monkeypatch.setattr(vision, "load_settings", lambda: settings(vision_model="claude-sonnet-5-5", local_model="qwen"))
    seq = vision.order()
    assert seq[0] == ("claude", ["claude-sonnet-5-5", "claude-opus-5-5"])
    assert ("local", ["local:qwen"]) in seq
    monkeypatch.setattr(vision, "load_settings", lambda: settings(vision_model="local:qwen", local_model="qwen",
                                                                   auto_switch=False))
    assert vision.order() == [("local", ["local:qwen"])]


def test_a_local_model_needs_no_key(monkeypatch):
    monkeypatch.setattr(vision, "load_settings", lambda: settings(local_model=""))
    assert not vision.has_key("local")
    monkeypatch.setattr(vision, "load_settings", lambda: settings(local_model="llava"))
    assert vision.has_key("local")


def test_names_read_as_people_say_them():
    assert usage.pretty("claude-opus-5-5") == "Claude Opus 5.5"
    assert usage.pretty("gpt-6.1-sol") == "GPT-6.1 Sol"
    assert usage.pretty("gemini-3.8-flash") == "Gemini 3.8 Flash"
    assert usage.pretty("openrouter:vendor/model") == "OpenRouter vendor/model"
    assert usage.pretty("local:llava:7b") == "Local llava:7b"


# --- quota fallback ------------------------------------------------------------------


class FakeModel(base.VisionModel):
    def __init__(self, provider, result=None, out_of_quota=False):
        self.provider, self.label = provider, provider.title()
        super().__init__([f"{provider}-model"], True, lambda s: None)
        self.result, self.out_of_quota, self.tasks = result, out_of_quota, []

    def act(self, instruction, max_turns=15, **kw):
        self.tasks.append(instruction)
        if self.out_of_quota:
            raise usage.QuotaExhausted(self.provider, None, "test")
        return self.result

    def plan(self, task, history):
        if self.out_of_quota:
            raise usage.QuotaExhausted(self.provider, None, "test")
        return {"status": "done", "summary": self.provider}


def test_quota_moves_to_the_next_provider_and_continues_from_the_screen(desk):
    first, second = FakeModel("claude", out_of_quota=True), FakeModel("openai", (True, "did it"))
    v = vision.Vision(auto_switch=True, log=lambda s: None, clients={"claude": first, "openai": second})
    assert v.act("open Notes") == (True, "did it")
    assert second.tasks[0].startswith("open Notes") and base.CONTINUE_NOTE in second.tasks[0]
    assert v.plan("x", [])["summary"] == "openai"


def test_without_switching_quota_ends_the_task(desk):
    v = vision.Vision(auto_switch=False, log=lambda s: None,
                      clients={"claude": FakeModel("claude", out_of_quota=True), "openai": FakeModel("openai", (True, ""))})
    with pytest.raises(usage.QuotaExhausted) as e:
        v.act("open Notes")
    assert e.value.provider == "vision"


def test_quota_inside_a_provider_steps_down_its_models(desk, monkeypatch):
    out = {"m1"}
    monkeypatch.setattr(usage, "available", lambda m: m not in out)
    m = FakeModel("claude")
    m.models = ["m1", "m2"]
    assert m.model == "m2"
    out.add("m2")
    with pytest.raises(usage.QuotaExhausted):
        m.model


# --- Claude: the computer toolset ----------------------------------------------------


def use(name, i, **inp):
    return NS(type="tool_use", id=f"t{i}", name=name, input=inp, toolset_name="computer")


def reply(*blocks, stop="tool_use"):
    return NS(content=list(blocks), stop_reason=stop, usage=NS(input_tokens=10, output_tokens=5))


class FakeAnthropic:
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
        self.beta = NS(messages=NS(create=self.create))
        self.messages = NS(create=self.create)

    def create(self, **kw):
        self.requests.append(kw)
        return self.replies.pop(0)


def run_claude(replies, **kw):
    client = FakeAnthropic(replies)
    m = claude.Claude(["claude-opus-5-5"], log=lambda s: None, client=client)
    return client, m.act("save the note", **{"confirm": lambda q: True, "log": lambda s: None, **kw})


def test_claude_actions_map_to_screen_points(desk):
    client, (ok, msg) = run_claude([
        reply(NS(type="text", text="Saving."), use("left_click", 1, coordinate=[150, 200]),
              use("type", 2, text="hi"), use("key", 3, text="cmd+s"),
              use("scroll", 4, coordinate=[10, 20], scroll_direction="down", scroll_amount=2)),
        reply(NS(type="text", text="Saved the note."), stop="end_turn"),
    ])
    assert (ok, msg) == (True, "Saved the note.")
    assert desk == ["leftx1 300,400", "type hi", "cmd+s", "scroll down 100 at 20,40"]
    req = client.requests[0]
    assert req["tools"][0]["type"] == "computer_toolset_20260801" and req["fallbacks"] == "default"
    results = client.requests[1]["messages"][2]["content"]  # user, assistant, then the tool results
    assert [r["tool_use_id"] for r in results] == ["t1", "t2", "t3", "t4"]
    assert all(r["toolset_name"] == "computer" for r in results)
    assert results[-1]["content"][1]["type"] == "image"  # the batch's effect, without another round trip


def test_claude_stops_the_batch_at_the_first_failure(desk, monkeypatch):
    monkeypatch.setattr(actions, "type_text", lambda text, **k: (_ for _ in ()).throw(RuntimeError("no field")))
    client, _ = run_claude([reply(use("type", 1, text="hi"), use("key", 2, text="enter")),
                            reply(NS(type="text", text="ok"), stop="end_turn")])
    r1, r2 = client.requests[1]["messages"][2]["content"]
    assert r1["is_error"] and "no field" in r1["content"]
    assert r2["is_error"] and r2["content"][0]["text"] == claude.NOT_EXECUTED  # plus the screenshot of where it stopped
    assert desk == []


def test_claude_risky_click_asks_and_a_no_stops(desk):
    asked = []
    _, (ok, msg) = run_claude([reply(use("left_click", 1, coordinate=[100, 50]))],
                              confirm=lambda q: asked.append(q) or False)
    assert not ok and msg.startswith("user declined")
    assert asked and "Buy Now" in asked[0]
    assert desk == []


def test_claude_risky_click_is_allowed_when_the_user_said_so(desk):
    _, (ok, _) = run_claude([reply(use("left_click", 1, coordinate=[100, 50])),
                             reply(NS(type="text", text="sent"), stop="end_turn")],
                            confirm=lambda q: pytest.fail("asked"), allow_risky=True)
    assert ok and desk == ["leftx1 200,100"]


def test_claude_refusal_ends_the_task(desk):
    _, (ok, msg) = run_claude([reply(stop="refusal")])
    assert not ok and "declined" in msg


def test_corner_abort_propagates_out_of_claude(desk, monkeypatch):
    def corner(*a, **k):
        raise actions.pyautogui.FailSafeException("corner")
    monkeypatch.setattr(actions, "click", corner)
    with pytest.raises(actions.pyautogui.FailSafeException):
        run_claude([reply(use("left_click", 1, coordinate=[1, 1]))])


# --- OpenAI: the Responses computer tool ---------------------------------------------


class FakeResponses:
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
        self.responses = NS(create=self.create)

    def create(self, **kw):
        self.requests.append(kw)
        return self.replies.pop(0)


def computer_call(*acts, checks=()):
    return NS(type="computer_call", call_id="c1", actions=list(acts), action=None,
              pending_safety_checks=[NS(id=f"s{i}", code="code", message=m) for i, m in enumerate(checks)])


def response(*items, text=""):
    return NS(id="r", output=list(items), output_text=text, usage=NS(input_tokens=3, output_tokens=2))


def run_openai(replies, **kw):
    client = FakeResponses(replies)
    m = oai.OpenAI(["gpt-6.1-sol"], log=lambda s: None, client=client)
    return client, m.act("search penguins", **{"confirm": lambda q: True, "log": lambda s: None, **kw})


def test_openai_actions_map_to_screen_points(desk):
    client, (ok, msg) = run_openai([
        response(computer_call({"type": "click", "button": "left", "x": 10, "y": 20},
                               {"type": "type", "text": "penguin"},
                               {"type": "keypress", "keys": ["ENTER"]},
                               {"type": "scroll", "x": 5, "y": 5, "scroll_x": 0, "scroll_y": -300},
                               {"type": "drag", "path": [{"x": 1, "y": 2}, {"x": 3, "y": 4}]})),
        response(text="Searched."),
    ])
    assert (ok, msg) == (True, "Searched.")
    assert desk == ["leftx1 20,40", "type penguin", "ENTER", "scroll up 300 at 10,10", "drag (2, 4, 6, 8)"]
    out = client.requests[1]
    assert out["previous_response_id"] == "r" and out["tools"][0] == {"type": "computer"}
    assert out["input"][0]["type"] == "computer_call_output" and out["input"][0]["output"]["type"] == "computer_screenshot"


def test_openai_safety_checks_are_confirmed_and_acknowledged(desk):
    client, (ok, _) = run_openai([response(computer_call({"type": "wait"}, checks=["sensitive site"])),
                                  response(text="ok")])
    assert ok and client.requests[1]["input"][0]["acknowledged_safety_checks"][0]["id"] == "s0"
    _, (ok, msg) = run_openai([response(computer_call({"type": "wait"}, checks=["sensitive site"]))],
                              confirm=lambda q: False)
    assert not ok and msg.startswith("user declined")


def test_openai_function_tools_open_apps_and_yield(desk):
    call = NS(type="function_call", call_id="f1", name="open_app", arguments='{"app_name": "Notes"}')
    client, (ok, _) = run_openai([response(call), response(text="opened")])
    assert ok and desk == ["open Notes"]
    assert client.requests[1]["input"][0] == {"type": "function_call_output", "call_id": "f1", "output": '{"status": "ok"}'}
    stop = NS(type="function_call", call_id="f2", name="yield_to_user", arguments='{"reason": "2FA"}')
    _, (ok, msg) = run_openai([response(stop)])
    assert not ok and "2FA" in msg


# --- OpenRouter and local models: JSON actions ---------------------------------------


class FakeChat:
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kw):
        self.requests.append(kw)
        content = self.replies.pop(0)
        return NS(choices=[NS(message=NS(content=content))], usage=NS(prompt_tokens=1, completion_tokens=1))


def test_compatible_models_click_elements_by_id_or_pixels(desk):
    client = FakeChat(['{"status": "continue", "actions": [{"type": "click", "element": "e1"},'
                       ' {"type": "type", "text": "milk"}, {"type": "click", "x": 50, "y": 60}]}',
                       'Sure! {"status": "done", "message": "Added milk."}'])
    m = compat.Local(["llava"], log=lambda s: None, client=client)
    assert m.models == ["local:llava"]
    assert m.act("add milk", confirm=lambda q: True, log=lambda s: None) == (True, "Added milk.")
    assert desk == ["leftx1 300,400", "type milk", "leftx1 100,120"]
    assert client.requests[0]["model"] == "llava"
    assert client.requests[0]["messages"][1]["content"][1]["type"] == "image_url"


def test_compatible_risky_click_by_element_asks(desk):
    client = FakeChat(['{"status": "continue", "actions": [{"type": "click", "element": "e0"}]}'])
    m = compat.OpenRouter(["vendor/model"], log=lambda s: None, client=client)
    ok, msg = m.act("tidy the page", confirm=lambda q: False, log=lambda s: None)
    assert not ok and "Buy Now" in msg and desk == []


def test_a_local_server_that_isnt_running_counts_as_unavailable():
    import openai
    m = compat.Local(["llava"], log=lambda s: None, client=object())
    assert m.unreachable(openai.APIConnectionError(request=None))


# --- the agent with a vision model --------------------------------------------------------


def test_agent_vision_mode_reports_a_corner_abort(desk, monkeypatch):
    def corner(*a, **k):
        raise actions.pyautogui.FailSafeException("corner")
    monkeypatch.setattr(actions, "click", corner)
    client = FakeAnthropic([reply(use("left_click", 1, coordinate=[1, 1]))])
    v = vision.Vision(log=lambda s: None, clients={"claude": claude.Claude(["claude-opus-5-5"], log=lambda s: None,
                                                                            client=client)})
    a = agent.Agent(mode="vision", vision=v, auto_switch=False, log=lambda s: None, confirm=lambda q: True)
    assert a.run("click it").startswith("stopped: mouse moved into a screen corner")


def test_shot_maps_through_the_display_origin():
    # A screenshot of a second display at (-1920, -200), 1920 points wide, sent at 960 pixels.
    s = FakeShot()
    s.ox, s.oy, s.scale = -1920, -200, 2.0
    assert s.point(100, 50) == (-1720, -100)
    assert s.pixel(-1720, -100) == (100, 50)


def test_only_unrequested_payments_and_deletions_need_asking():
    from cu.vision.base import is_risky_action as risky
    assert not risky("CLICK", "Post", "", "post a tweet saying hi")  # the request asked for it
    assert not risky("CLICK", "Send", "", "reply to Sam")  # sending isn't paying or deleting
    assert not risky("CLICK", "Border", "", "")  # whole words only
    assert risky("CLICK", "Buy Now", "", "find the cheapest flight")
    assert not risky("CLICK", "Buy Now", "", "buy the cheapest flight")
    assert risky("CLICK", "Delete", "", "tidy my inbox")
    assert not risky("CLICK", "Delete", "", "delete the spam emails")


# --- context budgets ----------------------------------------------------------------------


def test_context_budgets_differ_by_model_and_settings_override_them(monkeypatch):
    assert base.context_budget("claude-opus-5-5") == 200_000
    assert base.context_budget("gpt-6.1-sol") == 128_000
    assert base.context_budget("local:llava") == 16_000
    assert base.context_budget("something-new") == base.DEFAULT_BUDGET
    monkeypatch.setattr(config, "load_settings", lambda: settings(context_budgets={"local:": 50_000}))
    assert base.context_budget("local:llava") == 50_000


def test_a_long_conversation_starts_fresh_with_a_summary(desk):
    big = NS(input_tokens=190_000, output_tokens=5)  # 95% of Claude's budget
    client = FakeAnthropic([
        reply(use("left_click", 1, coordinate=[150, 200])),
        NS(content=[use("type", 2, text="hi")], stop_reason="tool_use", usage=big),
        reply(NS(type="text", text="Done after a fresh start."), stop="end_turn"),
    ])
    m = claude.Claude(["claude-opus-5-5"], log=lambda s: None, client=client)
    ok, msg = m.act("save the note", confirm=lambda q: True, log=lambda s: None)
    assert (ok, msg) == (True, "Done after a fresh start.")
    fresh = client.requests[2]["messages"]
    assert fresh is not client.requests[1]["messages"]  # a new conversation, not the old one carried on
    text = fresh[0]["content"][0]["text"]
    assert text.startswith("save the note") and "Fresh start" in text and "left_click" in text
