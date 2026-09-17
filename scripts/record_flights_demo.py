"""A measured live run with continuous CDP screencast; original timestamps retained."""

import base64
import hashlib
import json
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from browser_harness.helpers import drain_events

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
URL = "https://www.google.com/travel/flights?hl=en"
GOALS = "Find a one-way ticket from New York to San Francisco on October 9, 2026."


def verify(page):
    vals = {a["label"].strip(): a.get("value") for a in page["actions"]}
    flights = [a["label"] for a in page["actions"] if "Select flight" in a["label"]]
    checks = {
        "results_page": (
            urlparse(page["url"]).hostname == "www.google.com"
            and urlparse(page["url"]).path == "/travel/flights/search"
        ),
        "one_way": vals.get("Change ticket type. One way") == "One way",
        "origin": vals.get("Where from?") in ["New York", "New York City"],
        "destination": vals.get("Where to?") == "San Francisco",
        "date": vals.get("Departure") == "Fri, Oct 9",
        "flights": bool(flights) and all("Friday, October 9" in f for f in flights),
    }
    encoded = parse_qs(urlparse(page["url"]).query).get("tfs", [""])[0]
    try:
        checks["year"] = b"2026-10-09" in base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except ValueError:
        checks["year"] = False
    return {"passed": all(checks.values()), "checks": checks, "url": page["url"], "visible_flights": flights}


from gliner_ultrafast import Agent  # noqa: E402


def main():
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/flights/recorded")
    folder.mkdir(parents=True, exist_ok=False)
    source_hashes = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (ROOT / "gliner_ultrafast").iterdir()
        if p.suffix in {".py", ".js"}
    }
    agent = Agent(URL, GOALS)
    (folder / "frames").mkdir(exist_ok=True)
    (folder / "frames" / "000000.jpg").write_bytes(
        base64.b64decode(agent.browser.call("Page.captureScreenshot", format="jpeg", quality=85)["data"])
    )
    frames = folder / "screencast"
    frames.mkdir(exist_ok=True)
    stop = threading.Event()
    epoch = time.time()
    errors = []

    def capture():
        try:
            while not stop.is_set():
                for event in drain_events():
                    if event["method"] != "Page.screencastFrame" or event.get("session_id") != agent.browser.session:
                        continue
                    p = event["params"]
                    timestamp = max(0, round((p["metadata"]["timestamp"] - epoch) * 1000))
                    (frames / f"{timestamp:06d}.jpg").write_bytes(base64.b64decode(p["data"]))
                    agent.browser.call("Page.screencastFrameAck", sessionId=p["sessionId"])
                stop.wait(0.015)
        except Exception as e:
            errors.append(str(e))

    agent.browser.call("Page.startScreencast", format="jpeg", quality=80, maxWidth=1120, maxHeight=780, everyNthFrame=1)
    worker = threading.Thread(target=capture, daemon=True)
    worker.start()
    # The first prediction starts the run timer; this anchors video timestamps to it.
    epoch = time.time()
    try:
        for state in agent.run():
            action = state["history"][-1]["action"] if state["history"] else ""
            print(state["elapsed_ms"], state["status"], action, flush=True)
    finally:
        time.sleep(0.08)  # Drain the last frame, outside the reported agent time.
        stop.set()
        worker.join(timeout=3)
        agent.browser.call("Page.stopScreencast")
        state = agent.snapshot()
        state["final_page"] = agent.browser.observe(screenshot=False)
        state["verification"] = verify(state["final_page"])
        (frames / f"{state['elapsed_ms']:06d}.jpg").write_bytes(
            base64.b64decode(agent.browser.call("Page.captureScreenshot", format="jpeg", quality=95)["data"])
        )
        state["source_hashes"] = source_hashes
        state["recording_errors"] = errors
        (folder / "state.json").write_text(json.dumps(state, indent=2, default=str))
        (folder / "session.json").write_text(
            json.dumps({"target": agent.browser.target, "session": agent.browser.session})
        )
    agent.close()
    print(json.dumps(state["verification"], indent=2))
    print("Screencast frames", len(list(frames.glob("*.jpg"))), "errors", errors)
    if not state["verification"]["passed"]:
        raise SystemExit("Final-page verification failed")


if __name__ == "__main__":
    main()
