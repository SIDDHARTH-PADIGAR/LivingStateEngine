
# Demo: healthy to at-risk and back

## Prerequisites

- Python environment installed.
- Dependencies installed.
- Synthetic dataset generated.

## Start

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\generate_dataset.py
.\.venv\Scripts\python.exe scripts\run_challenge_benchmark.py
.\.venv\Scripts\python.exe -m streamlit run .\app.py
```

## Walkthrough

1. Load the synthetic dataset in the Streamlit sidebar.
2. Select `high_risk_contradiction-01`.
3. Set `Events replayed` to 1. The estimated status is `healthy`.
4. Move to 2. A high-severity risk changes the state to `at_risk`.
5. Move to 3. A healthy status report arrives, but the unresolved risk keeps the state at `at_risk`.
6. Move to 4. The risk is resolved and the state returns to `healthy`.
7. Open the evidence and History tabs. Inspect the risk event, resolution event, state snapshots, and recorded revisions.
8. Open the Recommendations & outcomes tab to inspect the recommended action and expected success criterion.

## What the demo establishes

The application can replay the supplied synthetic stream, update its deterministic estimate, and expose supporting evidence.

## What it does not establish

The demo does not establish real-world accuracy, recommendation effectiveness, causal impact, or production-scale performance.
