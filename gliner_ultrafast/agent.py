"""The complete agent loop. Typed choices, observable state, bounded execution."""

import base64
import time
from pathlib import Path

from . import record
from .browser import Browser, StalePage
from .dates import first_date, normalise
from .gliner import choose, classifier, requirements
from .model import action_space, field_context, field_text
from .questions import FRUITLESS, MAX_STEPS


def committed(history):
    """The form of the most recent field a value was typed into."""
    return next((h.get("form") for h in reversed(history) if h.get("kind") == "fill"), None)


def filled_label(history):
    """The name of the most recent field a value was typed into."""
    return next((h.get("action") for h in reversed(history) if h.get("kind") == "fill"), None)


def opens_dialog(page):
    return any(action.get("dialog") for action in page["actions"])


def completed_dates(parts, page, history):
    """A date requirement is complete when its assigned field shows that date.

    A calendar's close animation can outlast the click observation. Read the
    resulting field state rather than losing progress because a dialog lingered.
    """
    if opens_dialog(page):
        return set()
    completed = set()
    for part in parts:
        if not part.get("date"):
            continue
        targets = [h for h in history if h.get("requirement") == part["text"] and h.get("kind") == "fill"]
        for target in targets:
            for action in page["actions"]:
                if action.get("kind") != "fill":
                    continue
                same_node = (target.get("node") is not None and target.get("document_id") is not None
                             and target["node"] == action.get("node")
                             and target["document_id"] == action.get("document_id"))
                same_field = same_node or target["action"] == action["label"]
                if same_field and first_date(action.get("value")) == part["date"]:
                    completed.add(part["text"])
    return completed


class Agent:
    def __init__(self, url, goals, *, record_dir=None, screenshots=False):
        task = goals.strip() if isinstance(goals, str) else "\n".join(goals).strip()
        if not task:
            raise ValueError("Supply a task")
        plan = [task]
        self.pending_text = None
        # How strongly the task refers to each control, judged once per control.
        self.reference = {}
        # Fields the text helper had no value for. Offering them again only
        # repeats the same refusal.
        self.refused = set()
        # Requirements whose action has run. Each is answered by one action.
        self.served = set()
        # Ticks in a row that decided something and then failed to execute it.
        # Without this a page that keeps going stale spends the whole model
        # budget predicting and never moves, which reads as a hang.
        self.fruitless = 0
        # Load the encoder before the run clock starts; it is a process cost, not
        # a per-decision one, and charging it to the first action hides both.
        classifier()
        # The goal's requirements come from the goal alone, so they are read once
        # rather than re-derived against every page.
        self.requirements = requirements(task)
        record.context(goal=task, url=url, requirements=[part["text"] for part in self.requirements])
        self.browser = Browser(url)
        self.record_dir = Path(record_dir) if record_dir else None
        self.screenshots = screenshots or bool(record_dir)
        try:
            page = self.browser.observe(screenshot=self.screenshots)
        except Exception:
            self.browser.close()
            raise
        self.state = dict(
            browser=self.browser,
            goal="\n".join(plan),
            page=page,
            decision=None,
            history=[],
            status="ready",
            plan=plan,
            plan_index=0,
            decisions=[],
            text_calls=[],
            elapsed_ms=0,
            started_at=None,
            record=bool(self.record_dir),
        )
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            (self.record_dir / "000000.jpg").write_bytes(base64.b64decode(page["screenshot"]))

    def snapshot(self):
        return {
            **{k: v for k, v in self.state.items() if k != "browser"},
            "elements": action_space(self.state["page"]["actions"])[0],
        }

    def command(self, name, body=None):
        body = body or {}
        state = self.state
        if name == "tick":
            if self.fruitless >= FRUITLESS:
                state["status"] = "blocked"
                raise ValueError(f"Stopped after {FRUITLESS} decisions that could not be executed")
            try:
                self.command("predict", {})
                before = len(state["history"])
                result = self.command("act", {"fingerprint": state["page"]["fingerprint"]})
                self.fruitless = 0 if len(state["history"]) > before else self.fruitless + 1
                return result
            except StalePage:
                self.fruitless += 1
                state["decision"] = None
                state["status"] = "ready"
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
        elif name == "predict":
            if not state["browser"]:
                raise ValueError("Start a demo first")
            if state["started_at"] is None:
                state["started_at"] = time.perf_counter()
            if not state["browser"].fresh(state["page"]):
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["decision"] = None
            if state["status"] in {"done", "blocked"}:
                raise ValueError("This run has stopped. Start a fresh demo.")
            if len(state["decisions"]) >= MAX_STEPS * 2:
                raise ValueError("Reached the demo's model-call budget")
            state["decision"] = choose(
                state["page"], state["goal"], state["history"], self.reference,
                self.refused, self.requirements, self.served,
            )
            state["decisions"].append(
                {
                    **state["decision"],
                    "fingerprint": state["page"]["fingerprint"],
                    "elapsed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                }
            )
            state["status"] = "predicted"
        elif name == "act":
            decision, page = state["decision"], state["page"]
            if not decision or body.get("fingerprint") != page["fingerprint"]:
                raise ValueError("Observe and choose before acting")
            # Consume once, before any mutation or model call. A retry cannot double-click.
            state["decision"] = None
            selected = decision["choice"]
            if selected in {"DONE", "BLOCKED"}:
                if not state["browser"].fresh(page):
                    state["status"] = "ready"
                    raise StalePage("Page changed since the decision. Choose again.")
                state["status"] = "done" if selected == "DONE" else "blocked"
                state["plan_index"] = int(selected == "DONE")
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
            action = next(a for a in page["actions"] if a["id"] == selected)
            if len(state["history"]) >= MAX_STEPS:
                state["status"] = "blocked"
                raise ValueError(f"Stopped at the {MAX_STEPS}-action demo budget")
            text, helper = None, None
            if action["kind"] == "fill":
                if not state["browser"].fresh(page):
                    raise StalePage("Page changed before text generation. Choose again.")
                context = field_context(state["goal"], action, page, state["history"], decision.get("requirement"))
                if self.pending_text and self.pending_text[0] == context:
                    _, text, helper = self.pending_text
                else:
                    try:
                        text, helper = field_text(context)
                    except ValueError as error:
                        # The goal supplies no value for this field. Nothing is
                        # guessed; the field stops being offered and the run goes on.
                        self.refused.add(action["label"])
                        state["status"] = "ready"
                        state["text_calls"].append({"field": action["label"], "error": str(error)})
                        return self.snapshot()
                    text = normalise(text, decision.get("date"))
                    self.pending_text = (context, text, helper)
                    state["text_calls"].append({**helper, "field": action["label"], "value": text})
            # Browser.act checks freshness immediately before input, including after text generation.
            state["browser"].act(action, page, text=text)
            self.pending_text = None
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            # Record execution before observing. A stale post-action observation must not erase the action.
            state["history"].append(
                {
                    "step": len(state["history"]) + 1,
                    "action": action["label"],
                    "node": action.get("node"),
                    "document_id": action.get("document_id"),
                    "requirement": decision.get("requirement"),
                    "kind": action["kind"],
                    # A suggestion sits in its own popup form; what it commits is
                    # the form of the field that was just typed into.
                    "form": (committed(state["history"])
                             if action["kind"] == "key" else action.get("form")),
                    "submit": bool(action.get("submit") or action["kind"] == "key"),
                    # Taking a suggestion commits the field it completes, not the
                    # form around it. A flight search has three fields and one
                    # Search button; marking the form sent at the first
                    # autocomplete means the button is never pressed.
                    "committed_field": filled_label(state["history"]) if decision.get("commits") else None,
                    "choice": selected,
                    "probability": decision["probabilities"][selected],
                    "confidence": decision["confidence"],
                    "latency_ms": decision["latency_ms"],
                    "text": text,
                    "text_helper": helper["model"] if helper else None,
                    "text_latency_ms": helper["latency_ms"] if helper else 0,
                    "operation": decision["operation"],
                    "target": decision["target"],
                    "page_changed": None,
                    "url": page["url"],
                    "usage": decision["usage"],
                    "executed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                    "elapsed_ms": state["elapsed_ms"],
                }
            )
            state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            self.served.update(completed_dates(self.requirements, state["page"], state["history"]))
            if decision.get("covered") and not opens_dialog(state["page"]):
                # A control that opened a menu or picker has not answered its
                # requirement yet; the answer is one of the things it just
                # offered. The control itself is already masked by then, so the
                # requirement cannot simply choose it again.
                self.served.update(decision["covered"])
            state["history"][-1].update(
                page_changed=state["page"]["fingerprint"] != page["fingerprint"],
                url=state["page"]["url"],
                elapsed_ms=state["elapsed_ms"],
            )
            if state["record"]:
                (self.record_dir / f"{state['elapsed_ms']:06d}.jpg").write_bytes(
                    base64.b64decode(state["page"]["screenshot"])
                )
            repeated = state["history"][-3:]
            state["status"] = (
                "blocked"
                if len(repeated) == 3 and all(h["page_changed"] is False and h["kind"] != "wait" for h in repeated)
                else "ready"
            )
        else:
            raise ValueError("Unknown command")
        return self.snapshot()

    def run(self):
        while self.state["status"] not in {"done", "blocked"}:
            yield self.command("tick")

    def close(self):
        self.browser.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
