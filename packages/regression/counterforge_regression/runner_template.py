"""Only vetted static source is placed in bundles. Never render provider code."""

from pathlib import Path

RUNNER_VERSION = "1.0.0"
REQUIREMENTS = b"pytest==8.4.2\n"
TEST = b'''"""Portable correctness assertion; opt in via COUNTERFORGE_RUN_MODE."""
import importlib.util
import os
from pathlib import Path


def test_visual_regression():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("counterforge_runner", root / "runner/target_adapter.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    runner.run(root, os.environ.get("COUNTERFORGE_RUN_MODE", ""), os.environ.get("COUNTERFORGE_FIXTURE_COUNT"))
'''
README = b"""# CounterForge portable correctness regression

Verify sha256sums.txt before trusting extracted code. Install the pinned requirements
in an isolated Python 3.12 environment OUTSIDE this extracted directory:
python -m venv /tmp/counterforge-runner-venv
/tmp/counterforge-runner-venv/bin/python -m pip install -r requirements.txt
Use that environment's Python for the commands below. Extra bundle files are rejected.

Offline historical replay (does not call a provider):
python runner/target_adapter.py --replay-recorded
COUNTERFORGE_RUN_MODE=replay python -m pytest -q tests

The historical wrong result MUST fail with expected/actual; replay is not live reproduction.
LOCAL_FIXTURE bundles contain simulated evidence, never a real model discovery.
For fixture self-tests only: COUNTERFORGE_RUN_MODE=fixture COUNTERFORGE_FIXTURE_COUNT=5 python -m pytest -q tests

Explicit authorized live run (REAL_PROVIDER bundles only): set MODEL_API_KEY in the
environment, then python runner/target_adapter.py --live or
COUNTERFORGE_RUN_MODE=live python -m pytest -q tests.
One call maximum, 45 second timeout, at most 4096 completion tokens, no retries.
Provider-reported usage is printed; missing usage/cost remains unknown.
The assertion always compares actual count with expected ground truth.
Transport, refusal, parser, format and integrity failures are execution errors.
Provider-managed behavior may change. Human demo approval is separate from export.
"""


def static_members() -> dict[str, bytes]:
    return {
        "runner/target_adapter.py": Path(__file__).with_name("standalone.py").read_bytes(),
        "tests/test_visual_regression.py": TEST,
        "README.md": README,
        "requirements.txt": REQUIREMENTS,
    }
