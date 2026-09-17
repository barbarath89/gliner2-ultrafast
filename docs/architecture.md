# Architecture

The agent combines local GLiNER2 inference, a text helper and a browser controller.

| File | Responsibility |
|---|---|
| `gliner.py` | Goal entity extraction, requirement/control scoring, autocomplete ranking and action selection |
| `agent.py` | Execution loop, history, progress and text-helper handoff |
| `snapshot.js` | DOM observation, accessible labels, observed node identities and freshness data |
| `browser.py` | Browser Harness connection, target validation and browser input |
| `model.py` / `questions.py` | Configurable text generation for the selected input |
| `dates.py` | Date parsing and normalization |
| `record.py` | Optional local inference trace capture, disabled by default |

## Local model

`fastino/gliner2-multi-v1` supplies entity spans and scores requirements against labels built from the current page. Inference runs locally. Scores are cached only for identical queries and complete label schemas.

The controller splits English goals into requirements, assigns them to available controls, tracks entered values and commits autocomplete selections. Calendar matching uses parsed dates. These are explicit code rules alongside model inference; the agent is not an end-to-end reasoning model.

The text helper receives the goal, current requirement, selected field, visible page text and recent actions. It returns a validated JSON string to type. Configure its endpoint and model through `.env`; the default Mercury endpoint is remote.

## Execution

Targets resolve to observed DOM nodes. The executor checks freshness, visibility, disabled state and occlusion before input. Model outputs cannot supply executable JavaScript or arbitrary selectors. Browser mutations are not automatically retried after uncertain execution.

Termination is heuristic. `DONE` is not proof of success: application-level verifiers should inspect the actual result independently. Example verifiers run after the agent loop and do not choose actions.

## Current scope

Work in progress. Common HTML and ARIA patterns are supported, but behavior varies with page structure and goal wording. Date parsing supports English month names, ISO dates and US-style numeric dates. The inspector's scores are not calibrated probabilities of task success.

The browser uses an owned tab in an existing Chrome profile. The default setup is not fully offline: websites and the configured text-model service require network access. Optional traces can include page content and must be reviewed before sharing.
