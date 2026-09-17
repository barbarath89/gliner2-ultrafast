"""Offline contracts for the GLiNER2 policy and the loop. No model load, no paid APIs."""

import json
import time
from copy import deepcopy
from unittest.mock import Mock

import pytest

from gliner_ultrafast import agent as loop
from gliner_ultrafast import gliner, model
from gliner_ultrafast.browser import StalePage, browser_operation, fingerprint


def page(actions=None):
    state = {
        "url": "https://example.test/",
        "title": "Search",
        "text": "Search",
        "scroll": {"y": 0},
        "actions": actions or [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Open Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }
    state["fingerprint"] = fingerprint(state)
    return state


def decision(action="e1"):
    return {
        "choice": action,
        "operation": "TYPE_TEXT",
        "target": "Search",
        "requirement": "for a book",
        "confidence": 1.0,
        "probabilities": {action: 1.0},
        "latency_ms": 10,
        "usage": {},
    }


class FakeResult:
    def __init__(self, scores, task):
        self.scores, self.task = scores, task

    def probabilities(self, _task):
        return self.scores

    def value(self, _task):
        return max(self.scores, key=self.scores.get)

    def confidence(self, _task):
        return max(self.scores.values(), default=0.0)


def stub(monkeypatch, scores, calls=None, values=()):
    """Score labels by lookup; anything unlisted is clearly unreferenced."""

    def look(text, schema, task):
        labels = schema.task_spec(task).label_names
        if calls is not None:
            calls.append({"text": text, "labels": list(labels), "task": task})
        table = scores.get(text, scores)
        return FakeResult({name: table.get(name, 0.001) for name in labels}, task)

    fake = Mock(
        classify=lambda text, schema: look(text, schema, schema.task_order[0]),
        batch_classify=lambda texts, schema: [look(t, schema, "referenced") for t in texts],
    )
    monkeypatch.setattr(gliner, "classifier", lambda: fake)
    monkeypatch.setattr(gliner, "extractor", lambda: Mock(
        extract_entities=lambda text, types: {"entities": {"location": list(values)}}))
    return fake


def one(text, values=()):
    """A single requirement, as a goal with no preposition produces."""
    return [{"text": text, "values": list(values), "date": None}]


def test_a_field_reachable_by_two_operations_is_one_choice(monkeypatch):
    stub(monkeypatch, {"Search": 0.5, "Open Search": 0.9})
    d = gliner.choose(page(), "search a book", [], parts=one("search a book"))
    # The click scores higher, but only typing can supply the value.
    assert d["operation"] == "TYPE_TEXT" and d["choice"] == "e1"


def test_the_operation_always_matches_the_observed_kind(monkeypatch):
    stub(monkeypatch, {"Go": 0.9})
    d = gliner.choose(page(), "submit it", [], parts=one("submit it"))
    assert (d["operation"], d["choice"]) == ("CLICK", "e3")


def test_dropdown_options_stay_separate_choices(monkeypatch):
    actions = [
        {"id": "e1", "kind": "select", "label": "Size - small", "role": "combobox",
         "value": "small", "current_value": "", "node": 5},
        {"id": "e2", "kind": "select", "label": "Size - large", "role": "combobox",
         "value": "large", "current_value": "", "node": 5},
    ]
    stub(monkeypatch, {"Size - large": 0.8})
    d = gliner.choose(page(actions), "large size", [], parts=one("large size"))
    assert (d["operation"], d["choice"]) == ("SELECT", "e2")


def test_two_requirements_cannot_take_the_same_control(monkeypatch):
    calls = []
    stub(monkeypatch, {"first": {"Search": 0.9, "Go": 0.8}, "second": {"Search": 0.95, "Go": 0.5}}, calls)
    parts = [{"text": "first", "values": [], "date": None}, {"text": "second", "values": [], "date": None}]
    d = gliner.choose(page(), "goal", [], parts=parts)
    # "second" wants the field more, so "first" is left the button it also wants.
    assert d["requirement"] == "first" and d["choice"] == "e3"


def test_requirements_run_in_the_order_the_goal_states_them(monkeypatch):
    stub(monkeypatch, {"first": {"Go": 0.99}, "second": {"Search": 0.9, "Open Search": 0.9}})
    parts = [{"text": "first", "values": [], "date": None}, {"text": "second", "values": [], "date": None}]
    # The goal names "first" first; where its control sits on the page is not a
    # statement about sequence.
    assert gliner.choose(page(), "goal", [], parts=parts)["choice"] == "e3"


def test_a_populated_form_is_sent_before_the_rest_of_the_goal(monkeypatch):
    actions = [
        {"id": "e1", "kind": "fill", "label": "Query", "role": "textbox", "value": "dune", "node": 1, "form": 9},
        {"id": "e2", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 2,
         "form": 9, "submit": True},
        {"id": "e3", "kind": "click", "label": "Result", "role": "link", "value": "", "node": 3},
    ]
    history = [{"step": 1, "action": "Query", "kind": "fill", "text": "dune", "form": 9}]
    stub(monkeypatch, {"Result": 0.99})
    # Its values mean nothing until it goes, so it goes before anything later.
    assert gliner.choose(page(actions), "goal", history, parts=one("goal"))["choice"] == "e2"


def test_a_value_reaches_a_field_it_barely_matches(monkeypatch):
    stub(monkeypatch, {"Search": 0.05, "Open Search": 0.05})
    parts = [{"text": "in Lisbon", "values": ["lisbon"], "date": None}]
    # The task states the value, so it has to be entered somewhere.
    assert gliner.choose(page(), "goal", [], parts=parts)["choice"] == "e1"


def test_a_value_still_may_not_press_an_unrelated_button(monkeypatch):
    stub(monkeypatch, {"Go": 0.05})
    parts = [{"text": "in Lisbon", "values": ["lisbon"], "date": None}]
    assert gliner.choose(page(), "goal", [], parts=parts)["operation"] != "CLICK"


def test_a_filled_field_is_not_offered_again(monkeypatch):
    filled = page()
    for action in filled["actions"][:2]:
        action["value"] = "dune"
    history = [{"step": 1, "action": "Search", "kind": "fill", "text": "dune"}]
    stub(monkeypatch, {"Search": 0.9, "Open Search": 0.9, "Go": 0.6})
    d = gliner.choose(filled, "goal", history, parts=one("goal"))
    # Both the fill and the click that only opens the same field are finished.
    assert d["choice"] == "e3"


def test_an_emptied_field_is_offered_again(monkeypatch):
    history = [{"step": 1, "action": "Search", "kind": "fill", "text": "dune"}]
    stub(monkeypatch, {"Search": 0.9, "Open Search": 0.9})
    assert gliner.choose(page(), "goal", history, parts=one("goal"))["choice"] == "e1"


def test_used_controls_stay_in_the_schema_as_negatives(monkeypatch):
    calls = []
    history = [{"step": 1, "action": "Cookie banner", "kind": "click", "text": None}]
    stub(monkeypatch, {"Go": 0.9}, calls)
    gliner.choose(page(), "goal", history, parts=one("goal"))
    # Without them a page of leftover links has nothing to be irrelevant against.
    assert "Cookie banner" in calls[0]["labels"]


def test_a_control_is_judged_once(monkeypatch):
    memory = {}
    stub(monkeypatch, {"Go": 0.9})
    gliner.choose(page(), "goal", [], memory, parts=one("goal"))
    sparse = page([{"id": "e1", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20}])
    stub(monkeypatch, {"Go": 0.02})
    # A changed alternative set is a new classification input; old softmax scores cannot carry over.
    assert gliner.choose(sparse, "goal", [], memory, parts=one("goal"))["operation"] == "BLOCKED"


def test_an_unreferenced_page_stops_instead_of_clicking(monkeypatch):
    stub(monkeypatch, {})
    history = [{"step": 1, "action": "Go", "kind": "click", "text": None},
               {"step": 2, "action": "Wait", "kind": "wait"}, {"step": 3, "action": "Wait", "kind": "wait"},
               {"step": 4, "action": "Scroll", "kind": "scroll"}, {"step": 5, "action": "Scroll", "kind": "scroll"}]
    assert gliner.choose(page(), "something else", history, parts=one("x"))["operation"] == "DONE"
    # Nothing done and nothing to do is not success.
    assert gliner.choose(page(), "something else", [], parts=one("x"))["operation"] == "BLOCKED"


def test_a_refused_field_is_not_offered_again(monkeypatch):
    stub(monkeypatch, {"Search": 0.9, "Open Search": 0.9, "Go": 0.6})
    d = gliner.choose(page(), "goal", [], None, {"Search"}, one("goal"))
    assert d["choice"] == "e3"


def test_a_populated_form_is_sent_before_the_page_is_left(monkeypatch):
    actions = [
        {"id": "e1", "kind": "fill", "label": "Query", "role": "textbox", "value": "dune", "node": 1, "form": 9},
        {"id": "e2", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 2,
         "form": 9, "submit": True},
        {"id": "e3", "kind": "click", "label": "Result", "role": "link", "value": "", "node": 3},
    ]
    history = [{"step": 1, "action": "Query", "kind": "fill", "text": "dune", "form": 9}]
    stub(monkeypatch, {"Result": 0.95})
    d = gliner.choose(page(actions), "goal", history, parts=one("goal"))
    # No requirement names a submit button; the form's own structure does.
    assert d["choice"] == "e2"


def test_a_typed_value_is_committed_before_anything_else(monkeypatch):
    actions = [
        {"id": "e1", "kind": "click", "label": "Zurich, Switzerland", "role": "option", "value": "", "node": 4},
        {"id": "e2", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 2},
    ]
    history = [{"step": 1, "action": "Where from?", "kind": "fill", "text": "Zurich"}]
    stub(monkeypatch, {"Go": 0.99})
    d = gliner.choose(page(actions), "goal", history, parts=one("goal"))
    assert d["choice"] == "e1"


def test_a_menu_of_alternatives_is_not_treated_as_a_dialog_to_confirm(monkeypatch):
    actions = [
        {"id": "e1", "kind": "click", "label": "Round trip", "role": "option", "value": "",
         "node": 1, "dialog": True},
        {"id": "e2", "kind": "click", "label": "One way", "role": "option", "value": "",
         "node": 2, "dialog": True},
    ]
    stub(monkeypatch, {"Round trip": 0.2, "One way": 0.2})
    # Nothing in there confirms anything, so pressing its best guess would just
    # toggle between the alternatives forever.
    assert gliner.choose(page(actions), "goal", [], parts=one("goal"))["operation"] != "CLICK"


def test_one_batched_pass_per_decision(monkeypatch):
    calls = []
    stub(monkeypatch, {"Go": 0.9}, calls)
    gliner.choose(page(), "goal", [], parts=one("goal"))
    assert len(calls) == 1


def test_prompt_markers_never_reach_the_schema(monkeypatch):
    actions = [{"id": "e1", "kind": "click", "label": "Save (draft) [C] now", "role": "button",
                "value": "", "node": 1}]
    calls = []
    stub(monkeypatch, {"Save draft now": 0.9}, calls)
    gliner.choose(page(actions), "goal", [], parts=one("goal"))
    # Labels are injected into the prompt verbatim; a marker would misalign the logits.
    assert calls[0]["labels"] == ["Save draft now"]


def test_requirements_split_at_prepositions_and_flag_values(monkeypatch):
    stub(monkeypatch, {}, values=["Zurich", "London"])
    parts = gliner.requirements("Find flights from Zurich to London")
    assert [p["text"] for p in parts] == ["Find flights", "from Zurich", "to London"]
    assert [bool(p["values"]) for p in parts] == [False, True, True]


def test_quoted_task_text_still_uses_the_llm(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Zurich"}'}}]})
    monkeypatch.setattr(model, "post_json", post)
    context = model.field_context('Fly from "Zurich" to London', page()["actions"][0], page(), [])
    assert model.field_text(context)[0] == "Zurich"
    assert post.call_count == 1
    sent = json.loads(post.call_args.args[2]["messages"][1]["content"])
    assert sent["goal"] == 'Fly from "Zurich" to London'


def test_missing_text_credential_stops_before_guessing(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TEXT_MODEL_API_KEY"):
        model.field_text({"goal": 'Enter "Zurich"'})


@pytest.fixture
def runner():
    a = loop.Agent.__new__(loop.Agent)
    a.screenshots = False
    a.pending_text = None
    a.served = set()
    a.requirements = []
    a.refused = set()
    a.fruitless = 0
    p = page()
    a.state = {
        "browser": Mock(fresh=Mock(return_value=True), observe=Mock(return_value=p)),
        "page": p,
        "decision": decision(),
        "goal": "Find a book",
        "history": [],
        "decisions": [],
        "status": "predicted",
        "started_at": time.perf_counter(),
        "record": False,
        "text_calls": [],
    }
    return a


def test_stale_decision_is_consumed_before_any_mutation(runner):
    runner.state["browser"].fresh.return_value = False
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["browser"].act.assert_not_called()
    assert runner.state["decision"] is None


def test_generated_text_reused_only_for_identical_retry_context(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 1
    assert runner.state["browser"].act.call_count == 2  # The first call rejects before any browser input.
    assert runner.pending_text is None


def test_changed_field_context_does_not_reuse_generated_text(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["page"]["text"] = "Different page context"
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 2


def test_loading_waits_do_not_trigger_no_progress_stop(runner):
    for _ in range(5):
        runner.state["decision"] = decision("wait")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert len(runner.state["history"]) == 5 and runner.state["status"] == "ready"


def test_stale_observation_preserves_executed_action(runner):
    runner.state["decision"] = decision("e3")
    runner.state["browser"].observe.side_effect = StalePage("changed")
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["history"][-1]["action"] == "Go"
    runner.state["browser"].act.assert_called_once()


def test_observation_is_one_atomic_browser_read(monkeypatch):
    import gliner_ultrafast.browser as browser

    p = page()
    cdp = Mock(return_value={"result": {"value": p}})
    monkeypatch.setattr(browser, "cdp", cdp)
    actual = browser_operation({"operation": "observe", "session": "test", "screenshot": False})
    assert actual["actions"] == p["actions"]
    assert cdp.call_count == 1
    assert cdp.call_args.args[0] == "Runtime.evaluate"


def test_executor_rejects_a_stale_page_before_browser_input(monkeypatch):
    import gliner_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.fresh = Mock(return_value=False)
    operation = Mock()
    monkeypatch.setattr(browser, "browser_operation", operation)
    with pytest.raises(StalePage):
        b.act(page()["actions"][0], page(), "book")
    operation.assert_not_called()


@pytest.mark.parametrize("response", [{"exceptionDetails": {}}, {"result": {}}])
def test_interrupted_dropdown_mutation_cannot_be_retried_as_stale(monkeypatch, response):
    import gliner_ultrafast.browser as browser

    # A navigation can destroy the evaluation result after the change event already fired.
    if "exceptionDetails" in response:
        response["exceptionDetails"] = {"text": "Execution context destroyed"}
    cdp = Mock(return_value=response)
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError, match="Dropdown execution"):
        browser_operation({"operation": "act", "session": "test", "action": {
            "id": "e1", "kind": "select", "node": 1, "value": "Design",
        }})
    assert cdp.call_count == 1


def test_fingerprint_tracks_values_and_identity_not_screenshots():
    p = page()
    other = deepcopy(p)
    other["screenshot"] = "changed"
    assert fingerprint(p) == fingerprint(other)
    other["actions"][0]["node"] = 99
    assert fingerprint(p) != fingerprint(other)


@pytest.mark.parametrize("changed", ["Departure", "Where from?", "Where to?", "year"])
def test_flight_verification_rejects_wrong_trip(changed):
    from examples.flights import verify

    actual = {
        "url": "https://www.google.com/travel/flights/search?tfs=example",
        "text": "Track prices from Zürich to London departing 2026-09-20",
        "actions": [
            {"label": k, "value": v}
            for k, v in [
                ("Change ticket type. One way", "One way"),
                ("Where from?", "Zürich"),
                ("Where to?", "London"),
                ("Departure", "Sun, Sep 20"),
                ("Nonstop flight on Sunday, September 20. Select flight", ""),
            ]
        ],
    }
    assert verify(actual)["passed"]
    if changed == "year":
        actual["text"] = actual["text"].replace("2026", "2027")
    else:
        next(a for a in actual["actions"] if a["label"] == changed)["value"] = "wrong"
    assert not verify(actual)["passed"]


@pytest.mark.parametrize(
    "content", ["Thinking: Zurich", '{"text":null}', '{"text":"Zurich","extra":true}', '{"text":123}']
)
def test_text_helper_rejects_invalid_values(monkeypatch, content):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    with pytest.raises(ValueError, match="nothing typed"):
        model.field_text({"goal": "Find a flight"})


def test_navigation_during_prediction_reobserves_without_action(runner):
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    runner.command("tick")
    assert runner.state["status"] == "ready"
    assert runner.state["decision"] is None
    runner.state["browser"].act.assert_not_called()
    # The tick decided nothing and executed nothing; enough of those is a hang.
    assert runner.fruitless == 1


def test_a_page_that_never_executes_stops_instead_of_spending_the_budget(runner):
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    for _ in range(loop.FRUITLESS):
        runner.command("tick")
    with pytest.raises(ValueError, match="could not be executed"):
        runner.command("tick")
    assert runner.state["status"] == "blocked"


def test_generic_navigation_does_not_complete_named_values(monkeypatch):
    actions = [{"id": "e1", "kind": "click", "label": "Plan a journey", "role": "button", "node": 1}]
    stub(monkeypatch, {t: {"Plan a journey": 0.99} for t in ["Plan a journey", "from Cambridge", "to Ely"]})
    parts = [
        {"text": "Plan a journey", "values": [], "date": None},
        {"text": "from Cambridge", "values": ["cambridge"], "date": None},
        {"text": "to Ely", "values": ["ely"], "date": None},
    ]
    choice = gliner.choose(page(actions), "goal", [], parts=parts)
    assert choice["choice"] == "e1"
    assert choice["covered"] == ["Plan a journey"]


def test_named_result_can_complete_a_value_requirement(monkeypatch):
    actions = [{"id": "e1", "kind": "click", "label": "Café Aurora - independent café", "role": "link", "node": 1}]
    stub(monkeypatch, {"Café Aurora - independent café": 0.99})
    choice = gliner.choose(page(actions), "goal", [], parts=one("open Cafe Aurora", ["cafe aurora"]))
    assert choice["covered"] == ["open Cafe Aurora"]


def test_name_evidence_does_not_match_substrings():
    assert not gliner.names_value("Newcastle", "New")
    assert gliner.names_value("Version 2 . 10 . 3", "2.10.3")


def test_relabelled_field_is_not_retyped_in_same_document():
    action = {"kind": "fill", "label": "Starting point Cambridge", "node": 5, "document_id": 123, "value": "Cambridge"}
    history = [{"kind": "fill", "action": "Choose starting point", "node": 5, "document_id": 123, "text": "Cambridge"}]
    assert gliner.satisfied(action, history)
    assert not gliner.satisfied({**action, "document_id": 456}, history)


def test_grid_autocomplete_is_scoped_to_the_typed_field(monkeypatch):
    actions = [
        {"id": "e1", "kind": "click", "label": "Cambridge station", "role": "gridcell", "node": 8, "suggestion_for": 5},
        {"id": "e2", "kind": "click", "label": "Ely station", "role": "gridcell", "node": 9, "suggestion_for": 6},
    ]
    history = [{"action": "Starting point", "kind": "fill", "text": "Cambridge", "node": 5}]
    stub(monkeypatch, {"Cambridge station": 0.95, "Ely station": 0.99})
    ordered = gliner.groups(page(actions), history, ())
    assert gliner.execute(gliner.suggestion(ordered, history)["group"])["id"] == "e1"


def test_literal_autocomplete_matches_exclude_unrelated_suggestions(monkeypatch):
    actions = [
        {"id": "e1", "kind": "click", "label": "Central Library, Market Square", "role": "option", "node": 8},
        {"id": "e2", "kind": "click", "label": "Museum Library, High Street", "role": "option", "node": 9},
    ]
    history = [{"action": "Destination", "kind": "fill", "text": "Central Library", "node": 5}]
    stub(monkeypatch, {"Central Library, Market Square": 0.60, "Museum Library, High Street": 0.99})
    ordered = gliner.groups(page(actions), history, ())
    assert gliner.execute(gliner.suggestion(ordered, history)["group"])["id"] == "e1"


def test_later_requirement_cannot_steal_only_available_field(monkeypatch):
    actions = [{"id": "e1", "kind": "fill", "label": "Destination", "role": "textbox", "node": 1, "value": ""}]
    scores = {"to Cambridge": {"Destination": 0.8}, "then to Ely": {"Destination": 0.99}}
    stub(monkeypatch, scores)
    pairs = [("to Cambridge", "cambridge"), ("then to Ely", "ely")]
    parts = [{"text": t, "values": [v], "date": None} for t, v in pairs]
    result = gliner.choose(page(actions), "goal", [], parts=parts)
    assert result["requirement"] == "to Cambridge"


def test_date_completion_requires_the_assigned_field_and_closed_popup():
    from datetime import date

    parts = [{"text": "on 2026-11-12", "date": date(2026, 11, 12)}]
    history = [{"requirement": "on 2026-11-12", "kind": "fill", "action": "Start date", "node": 7, "document_id": 1}]
    action = {"kind": "fill", "label": "Start date", "value": "2026-11-12", "node": 7, "document_id": 1}
    assert loop.completed_dates(parts, {"actions": [action]}, history) == {"on 2026-11-12"}
    assert not loop.completed_dates(parts, {"actions": [{**action, "dialog": True}]}, history)
    assert not loop.completed_dates(parts, {"actions": [{**action, "value": "2026-11-13"}]}, history)
    other = {**action, "label": "Return date", "node": 8}
    assert not loop.completed_dates(parts, {"actions": [other]}, history)
