"""Local GLiNER requirement extraction and scoring against observed browser controls.

The controller handles ordering, progress, dates and supported operations.
The model supplies entity spans and control scores.
"""

import os
import re
import threading
import time
import unicodedata

from . import record
from .dates import first_date, same_date

MODEL = os.environ.get("GLINER_MODEL", "fastino/gliner2-multi-v1")

FLOOR = float(os.environ.get("GLINER_FLOOR", "0.50"))

VALUE_FLOOR = float(os.environ.get("GLINER_VALUE_FLOOR", "0.02"))


RESERVED = re.compile(r"\[(?:P|C|E|R|L|DESCRIPTION|EXAMPLE|OUTPUT)\]|[()\[\]]")
OPERATIONS = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT"}


NAMES = {**OPERATIONS, "key": "PRESS_ENTER"}

VALUE_TYPES = {
    "location": "a place, city, country, airport or address",
    "date": "a calendar date or day",
    "time": "a clock time",
    "number": "a count, quantity or amount",
    "person": "a person's name",
    "organization": "a company, brand or organisation name",
    "money": "a price or monetary amount",


    "product": "a product, package, library, tool or software name",
    "title": "the title of a book, article, page or work",
}


VERBS = (
    "open|click|press|select|choose|go|view|find|search|set|enter|type|add|remove|check|"
    "uncheck|submit|close|show|read|download|install|book|buy|sort|filter|apply|confirm"
)
SPLIT = re.compile(
    r"(?<=\s)(?=(?:from|to|on|in|at|for|with|by|into|about|between|before|after|during|"
    rf"without|under|over|near|then)\s)|(?<=\s)(?=and\s+(?:{VERBS})\b)|(?<=[.;:])\s+",
    re.IGNORECASE,
)
CONFIRM = "confirm and close this dialog"
SUBMIT = "run the search with the values that were entered"
SUBMIT_FLOOR = float(os.environ.get("GLINER_SUBMIT_FLOOR", "0.5"))
CONFIRM_FLOOR = float(os.environ.get("GLINER_CONFIRM_FLOOR", "0.5"))

CONFIDENT = float(os.environ.get("GLINER_CONFIDENT", "0.90"))
KINDS = {
    "fill": "a text field to type a value into",
    "select": "a dropdown value to choose",
    "click": "a button or link to press",
}
_LOCK = threading.Lock()
_CLASSIFIER = None
_EXTRACTOR = None


def classifier():
    """Load once per process. The first call pays encoder load; later calls do not."""
    global _CLASSIFIER, _EXTRACTOR
    with _LOCK:
        if _CLASSIFIER is None:
            from gliner2.classification import Classifier

            _CLASSIFIER = Classifier.from_pretrained(MODEL).eval()
            _EXTRACTOR = _CLASSIFIER.model
        return _CLASSIFIER


def extractor():
    """The same weights as the classifier, read through the extraction head."""
    classifier()
    return _EXTRACTOR


def clean(value, limit=90):
    """Strip prompt markers, collapse whitespace, bound length."""
    text = RESERVED.sub(" ", str(value if value is not None else ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def names_value(label, value):
    """Literal evidence, allowing Unicode accents and punctuation differences."""
    def words(text):
        text = unicodedata.normalize("NFKD", str(text)).casefold()
        text = "".join(c for c in text if not unicodedata.combining(c))
        return " ".join(re.findall(r"\w+", text))

    needle = words(value)
    return bool(needle) and f" {needle} " in f" {words(label)} "


def requirements(goal):
    """Split the goal into requirements and extract literal values with GLiNER."""
    text = clean(goal, 600)
    found = extractor().extract_entities(text, VALUE_TYPES)["entities"]
    values = {v.lower() for spans in found.values() for v in spans if len(v) > 1}
    parts = []
    for part in SPLIT.split(text):
        part = part.strip(" ,.;:")
        if len(part) < 2:
            continue
        lowered = part.lower()
        found = sorted(v for v in values if v in lowered)

        parts.append({"text": part, "values": found, "date": first_date(part)})
    return parts or [{"text": text, "values": sorted(values), "date": first_date(text)}]


def satisfied(action, history):
    """Check execution history and current field values for an already-served control."""
    seen = 0
    for entry in history:
        same_node = (action.get("node") is not None and action.get("document_id") is not None
                     and entry.get("node") == action["node"]
                     and entry.get("document_id") == action["document_id"])
        if (not same_node and entry.get("action") != action["label"]) or entry.get("kind") != action["kind"]:
            continue
        if action["kind"] != "fill":
            return True
        seen += 1


        if entry.get("text") and action.get("value"):
            return True


        if seen >= 2:
            return True
    return False


def groups(state, history, refused):
    """Group supported actions by observed node, preserving document order."""
    ordered = {}
    for position, action in enumerate(state["actions"]):
        if action["kind"] not in OPERATIONS:
            continue
        label = clean(action["label"])
        if not label:
            continue
        key = (action["node"], action["value"] if action["kind"] == "select" else None)
        group = ordered.setdefault(key, {"position": position, "actions": {}})
        group["actions"].setdefault(label, action)


    for group in ordered.values():
        action = execute(group)
        group["open"] = (
            not satisfied(action, history)
            and action["label"] not in refused
            and not action.get("self_link")
        )
        group["takes_value"] = action["kind"] in {"fill", "select"}
    return ordered


def execute(group):
    """Typing beats clicking into the same field; otherwise there is one action."""
    actions = list(group["actions"].values())
    return next((a for a in actions if a["kind"] == "fill"), actions[0])


def rate(group, probabilities):
    """Return the score of the action that would execute for this group."""
    return probabilities.get(clean(execute(group)["label"]), 0.0)


def schema_for(ordered, history, value_takers):
    """Labels for one scoring pass, plus the negatives that keep it honest."""
    from gliner2.classification import ClassificationSchema

    labels = {}
    for group in ordered.values():


        action = execute(group)
        if value_takers and (action["kind"] == "click" or not group["open"]):
            continue
        labels[clean(action["label"])] = KINDS[action["kind"]]


    for entry in history:
        if not value_takers and entry.get("kind") in OPERATIONS:
            labels.setdefault(clean(entry.get("action")), KINDS[entry["kind"]])
    labels.pop("", None)
    if not labels:
        return None, {}


    return ClassificationSchema().single("referenced", labels, activation="softmax"), labels


def match(state, goal, history, refused, memory, parts):
    """Score requirements against observed controls, narrowing uncertain value matches to inputs."""
    ordered = groups(state, history, refused)
    results, latency = {}, 0
    texts = [part["text"] for part in parts]
    scored, spent = pass_over(ordered, history, texts, False, memory)
    results.update(scored)
    latency += spent
    unsure = []
    for part in parts:
        if not part["values"]:
            continue
        available = [g for g in ordered.values() if g["open"]]
        scores = results.get(part["text"], {})
        named = any(
            execute(g)["kind"] != "fill" and rate(g, scores) >= CONFIDENT
            and all(names_value(execute(g)["label"], value) for value in part["values"])
            for g in available
        )
        if not named and any(g["takes_value"] for g in available):
            unsure.append(part["text"])
    if unsure:
        scored, spent = pass_over(ordered, history, unsure, True, memory)
        results.update(scored)
        latency += spent
    return ordered, results, latency


def pass_over(ordered, history, texts, value_takers, memory):
    """One batched encoder call, or none at all if every answer is remembered."""
    schema, labels = schema_for(ordered, history, value_takers)
    if schema is None or not texts:
        return {}, 0


    # Softmax scores are reusable only for an identical full label schema.
    signature = tuple(labels.items())
    fresh = [text for text in texts if (text, signature, value_takers) not in memory]
    latency = 0
    if fresh:
        started = time.perf_counter()
        answers = classifier().batch_classify(fresh, schema)
        latency = round((time.perf_counter() - started) * 1000)
        for text, answer in zip(fresh, answers):
            memory[(text, signature, value_takers)] = dict(answer.probabilities("referenced"))
        record.capture(
            schema, "referenced", fresh,
            [dict(a.probabilities("referenced")) for a in answers],
            question="requirement", value_takers=value_takers,
        )
    return {text: memory[(text, signature, value_takers)] for text in texts}, latency


def favourite(ordered, probabilities):
    """The open control a requirement would pick if nothing else were competing."""
    best_group, best_score = None, 0.0
    for group in ordered.values():
        if not group["open"]:
            continue
        score = rate(group, probabilities)
        if score > best_score:
            best_group, best_score = group, score
    return best_group if best_score >= FLOOR else None


def best(ordered, scores, parts):
    """Assign requirements to available controls, respecting modal scope and explicit dates."""
    valued = {part["text"] for part in parts if part["values"]}
    dates = {part["text"]: part["date"] for part in parts if part.get("date")}
    texts = [text for text in scores if any(group["open"] for group in ordered.values())]
    open_groups = [group for group in ordered.values() if group["open"]]


    inside = [group for group in open_groups if execute(group).get("dialog")]
    if inside:
        open_groups = inside


    # Parse explicit calendar labels rather than comparing near-identical dates semantically.
    for text, wanted in dates.items():
        for group in open_groups:
            if any(same_date(label, wanted) for label in group["actions"]):
                return [{"requirement": text, "score": 1.0, "group": group}]
    offers = []
    for group in open_groups:
        column = {}
        for index, text in enumerate(texts):
            score = rate(group, scores[text])
            if not score:
                continue
            if score >= (VALUE_FLOOR if text in valued and group["takes_value"] else FLOOR):
                column[index] = score
        if column:
            offers.append((group, column))
    taken = assign(offers, len(texts))


    order = {text: index for index, text in enumerate(texts)}
    chosen = [
        {"requirement": texts[index], "score": score, "group": group, "rank": order[texts[index]]}
        for group, index, score in taken
    ]
    return sorted(chosen, key=lambda item: (item["rank"], item["group"]["position"]))


def threshold(carries_value, takes_value):
    """Return the minimum score for a requirement/control pairing."""
    if not carries_value:
        return FLOOR
    return VALUE_FLOOR if takes_value else CONFIDENT


def assign(offers, count):
    """Find a one-to-one assignment, prioritizing earlier requirements before total score."""
    table = {0: (0.0, ())}
    for group, column in offers:
        nxt = dict(table)
        for mask, (total, picked) in table.items():
            for index, score in column.items():
                bit = 1 << index
                if mask & bit:
                    continue
                key = mask | bit
                candidate = (total + score, picked + ((group, index, score),))
                if key not in nxt or nxt[key][0] < candidate[0]:
                    nxt[key] = candidate
        table = nxt
        if len(table) > 1 << min(count, 14):
            break


    # Earlier requirements take precedence when controls are limited.
    def priority(item):
        mask, (score, _) = item
        coverage = tuple(bool(mask & (1 << index)) for index in range(count))
        return coverage, score

    return max(table.items(), key=priority)[1][1]


def dialog(ordered, chosen):
    """Select a pending dialog action or score its available confirmation controls."""
    from gliner2.classification import ClassificationSchema

    inside = {}
    for group in ordered.values():
        if execute(group).get("dialog"):
            inside[clean(execute(group)["label"])] = group
    if not inside:
        return None
    wanted = [c for c in chosen if execute(c["group"]).get("dialog")]
    if wanted:
        return wanted


    openable = {label: group for label, group in inside.items() if group["open"] and not first_date(label)}
    if not openable:
        return None
    schema = ClassificationSchema().single("confirm", list(openable), activation="softmax")
    result = classifier().classify(CONFIRM, schema)
    record.capture(schema, "confirm", [CONFIRM], [dict(result.probabilities("confirm"))],
                   question="confirm")


    if result.confidence("confirm") < CONFIRM_FLOOR:
        return None
    return [{"requirement": None, "score": result.confidence("confirm"), "group": openable[result.value("confirm")]}]


def sent_key(entry):
    """What a fill belongs to: its form, or itself when it has none."""
    return entry.get("form") or ("field", entry.get("action"))


def unsent_form(state, ordered, history, chosen):
    """Find a submission action for a populated, uncommitted form."""
    from gliner2.classification import ClassificationSchema


    filled = {sent_key(entry) for entry in history if entry.get("kind") == "fill"}
    sent = {sent_key(entry) for entry in history if entry.get("submit")}
    sent |= {("field", entry["committed_field"]) for entry in history if entry.get("committed_field")}


    holding = {sent_key({"form": a.get("form"), "action": a["label"]})
               for a in state["actions"] if a["kind"] == "fill" and a.get("value")}
    pending = (filled - sent) & holding
    if not pending:
        return None
    for group in ordered.values():
        action = execute(group)
        if group["open"] and action.get("submit") and action.get("form") in pending:
            return {"requirement": None, "score": 1.0, "group": group}


    if chosen:
        return None
    buttons = {}
    for group in ordered.values():
        action = execute(group)
        if group["open"] and action["kind"] == "click" and action.get("form") in pending:
            buttons[clean(action["label"])] = group
    if not buttons:
        return enter(state)
    schema = ClassificationSchema().single("submit", list(buttons), activation="softmax")
    result = classifier().classify(SUBMIT, schema)
    record.capture(schema, "submit", [SUBMIT], [dict(result.probabilities("submit"))], question="submit")
    if result.confidence("submit") >= SUBMIT_FLOOR:
        return {"requirement": None, "score": result.confidence("submit"), "group": buttons[result.value("submit")]}
    return enter(state)


def enter(state):
    """The last resort for sending a field: the key the user would press."""
    key = control(state, "press_enter")
    return None if key is None else {"requirement": None, "score": 1.0,
                                     "group": {"position": -1, "actions": {key["label"]: key}, "open": True,
                                               "takes_value": False}}


def suggestion(ordered, history):
    """Rank observed autocomplete suggestions for the most recently entered value."""
    from gliner2.classification import ClassificationSchema

    if not history or history[-1].get("kind") != "fill" or not history[-1].get("text"):
        return None
    options = {}
    for group in ordered.values():
        action = execute(group)
        associated = action.get("suggestion_for") is not None and action["suggestion_for"] == history[-1].get("node")
        if group["open"] and (associated or action.get("role") == "option"):
            options[clean(action["label"])] = group
    if not options:
        return None
    typed = clean(history[-1]["text"], 60)


    exact = {label: group for label, group in options.items() if names_value(label, typed)}
    if exact:
        options = exact
    schema = ClassificationSchema().single("suggestion", list(options), activation="softmax")
    result = classifier().classify(typed, schema)
    record.capture(schema, "suggestion", [typed], [dict(result.probabilities("suggestion"))],
                   question="suggestion")
    picked = result.value("suggestion")


    return {"requirement": None, "score": result.confidence("suggestion"),
            "group": options[picked], "commits": True}


def control(state, name):
    return next((a for a in state["actions"] if a["id"] == name), None)


def idle(history):
    """How many steps in a row have read the page without changing it."""
    count = 0
    for entry in reversed(history):
        if entry.get("kind") not in {"wait", "scroll"}:
            break
        count += 1
    return count


def choose(state, goal, history, memory=None, refused=(), parts=None, served=()):
    """Same contract as the hosted choice model: one observation, one decision."""
    memory = {} if memory is None else memory
    parts = [p for p in (requirements(goal) if parts is None else parts) if p["text"] not in served]
    ordered, scores, latency = match(state, goal, history, refused, memory, parts)
    chosen = best(ordered, scores, parts)
    sending = unsent_form(state, ordered, history, chosen)
    if sending:


        chosen = [sending] + chosen
    committing = suggestion(ordered, history)
    chosen = [committing] if committing else (dialog(ordered, chosen) or chosen)
    commits = bool(committing)

    if chosen:
        group = chosen[0]["group"]
        action = execute(group)
        choice, operation, confidence = action["id"], NAMES[action["kind"]], chosen[0]["score"]
        requirement = chosen[0]["requirement"]


        covered = [requirement] if requirement is not None else []
        if commits and history[-1].get("requirement") and names_value(action["label"], history[-1].get("text", "")):
            covered = [history[-1]["requirement"]]
        if action["kind"] == "fill":


            holding = {part["text"] for part in parts if part["values"]}
            covered = [text for text in covered if text not in holding]
        else:


            unproven = {
                part["text"] for part in parts
                if part["values"] and not all(names_value(action["label"], value) for value in part["values"])
            }
            covered = [text for text in covered if text not in unproven]
    else:


        requirement, covered = None, []


        # Termination is heuristic; callers must verify the actual outcome.
        waited = idle(history)
        wait = control(state, "wait") if history and waited < 2 else None
        scroll = control(state, "scroll_down") if waited < 4 else None
        if wait:
            choice, operation, confidence = wait["id"], "WAIT", 1.0
        elif scroll:
            choice, operation, confidence = scroll["id"], "SCROLL_DOWN", 1.0
        else:
            acted = any(entry.get("kind") in OPERATIONS for entry in history)
            choice = operation = "DONE" if acted else "BLOCKED"
            confidence = 1.0
    return {
        "choice": choice,
        "operation": operation,
        "target": execute(chosen[0]["group"])["label"] if chosen else None,
        "requirement": requirement,
        "covered": covered,
        "commits": commits,
        "date": next((p["date"] for p in parts if p["text"] == requirement), None),
        "confidence": confidence,
        "probabilities": probabilities_by_id(ordered, scores, choice, confidence),
        "operation_probabilities": {operation: confidence},
        "target_probabilities": {c["group"] and execute(c["group"])["label"]: c["score"] for c in chosen},
        "target_confidence": chosen[0]["score"] if chosen else None,
        "raw_answers": {text: dict(sorted(p.items(), key=lambda kv: -kv[1])[:8]) for text, p in scores.items()},
        "model": MODEL,
        "usage": {"requirements": len(parts), "labels": sum(len(g["actions"]) for g in ordered.values())},
        "latency_ms": latency,
        "request": {"model": MODEL, "requirements": [p["text"] for p in parts]},
    }


def probabilities_by_id(ordered, scores, choice, confidence):
    """The inspector reads per-action numbers; a group scores as its best requirement."""
    by_id = {}
    for group in ordered.values():
        by_id[execute(group)["id"]] = max((rate(group, p) for p in scores.values()), default=0.0)
    return by_id if choice in by_id else {**by_id, choice: confidence}
