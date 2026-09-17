"""Optional local JSONL traces of classifier inputs and outputs.

Enable with GLINER_RECORD pointing to a file. Capture is disabled by default.
Labels reflect predictions unless an explicit gold label is supplied. Logged
predictions must not be treated as verified training targets. Traces may contain
page content and should be reviewed before sharing.
"""

import json
import os
import threading
import time
import uuid

RUN = uuid.uuid4().hex[:12]
_LOCK = threading.Lock()
_PATH = None
_STEP = 0
_BUFFER = None


def path():
    """Where to write, or None when capture is off."""
    global _PATH
    if _PATH is None:
        _PATH = os.environ.get("GLINER_RECORD", "")
    return _PATH or None


def begin():
    """Buffer trace writes until the caller commits or discards them."""
    global _BUFFER
    _BUFFER = []


def commit():
    """Flush buffered traces; label validation remains the caller’s responsibility."""
    global _BUFFER
    lines, _BUFFER = _BUFFER, None
    target = path()
    if lines and target:
        with _LOCK, open(target, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    return len(lines or [])


def discard():
    """Discard buffered traces."""
    global _BUFFER
    dropped, _BUFFER = len(_BUFFER or []), None
    return dropped


def context(**fields):
    """Per-run facts worth keeping beside every question this run asks."""
    global _CONTEXT
    _CONTEXT = dict(fields)


_CONTEXT = {}


def step():
    global _STEP
    _STEP += 1
    return _STEP


def capture(schema, task, texts, predictions, gold=None, **meta):
    """Write one line per query, in the trainer's format.

    `predictions` is a list of {label: probability} in the same order as `texts`.
    `gold` is the answer the run turned out to need, when something other than
    the model knows it -- a replayed trajectory, or a verified outcome. Without
    it the line carries the prediction and is marked as not yet supervised.
    """
    target = path()
    if not target:
        return
    spec = schema.task_spec(task)
    labels = list(spec.label_names)
    descriptions = {label.name: label.description for label in spec.labels if label.description}
    lines = []
    for index, (text, probabilities) in enumerate(zip(texts, predictions)):
        predicted = max(probabilities, key=probabilities.get) if probabilities else None
        answer = gold[index] if gold else predicted
        classification = {"task": task, "labels": labels, "true_label": [answer] if answer else []}
        if spec.instruction:
            classification["prompt"] = spec.instruction
        if descriptions:
            classification["label_descriptions"] = descriptions
        if not spec.is_exclusive:
            classification["multi_label"] = True
        lines.append(
            json.dumps(
                {
                    "input": text,
                    "output": {"classifications": [classification]},
                    "meta": {
                        "run": RUN,
                        "step": step(),
                        "at": round(time.time(), 3),
                        "predicted": {label: round(value, 6) for label, value in probabilities.items()},
                        "gold_known": bool(gold),
                        "model_was_right": bool(gold) and predicted == answer,
                        **_CONTEXT,
                        **meta,
                    },
                },
                ensure_ascii=False,
            )
        )
    if _BUFFER is not None:
        _BUFFER.extend(lines)
        return
    with _LOCK, open(target, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
