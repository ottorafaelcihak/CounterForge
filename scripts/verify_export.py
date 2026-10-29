"""Offline portable runner gate; a broken count must cause an assertion failure."""

from __future__ import annotations

import io
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID
from zipfile import ZipFile

from counterforge_regression.bundle import build_bundle, canonical
from counterforge_regression.manifest import RegressionManifest
from PIL import Image


def fixture_bundle() -> bytes:
    manifest = RegressionManifest(
        finding_id=UUID(int=1),
        export_job_id=UUID(int=2),
        test_id="LOCAL_FIXTURE-five-objects",
        question="How many objects? Return only an integer.",
        expected={"kind": "integer", "value": 5, "verified_by": "human"},
        model={
            "adapter": "LOCAL_FIXTURE",
            "model_id": "LOCAL_FIXTURE",
            "revision": "fixture-v1",
            "sampling_options": {},
        },
        invariant_verification={
            "verdict": "VALID",
            "reviewed_by": "fixture-self-test",
            "verifier_version": "fixture-v1",
        },
        confirmation={"screening_failures": 1, "fresh_attempts": 3, "fresh_failures": 3},
        mutation={"family": "brightness_shift", "strength": 0.1},
        provenance="LOCAL_FIXTURE",
        recorded_at=datetime(2026, 10, 29, tzinfo=UTC),
    )
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(output, format="PNG")
    evidence = {
        f"evidence/{name}.png": output.getvalue()
        for name in ("original", "failing", "protected-mask", "comparison")
    }
    evidence["evidence/recipe.json"] = canonical(
        {"family": "brightness_shift", "provenance": "LOCAL_FIXTURE"}
    )
    evidence["evidence/validity-report.json"] = canonical(
        {"verdict": "VALID", "provenance": "LOCAL_FIXTURE"}
    )
    evidence["evidence/target-responses.json"] = canonical(
        [
            {"phase": phase, "outcome": "INCORRECT", "parsed_count": 4, "raw_text": "4"}
            for phase in ("SCREENING", "CONFIRMATION", "CONFIRMATION", "CONFIRMATION")
        ]
    )
    return build_bundle(manifest, evidence).zip_bytes


def check(root: Path, mode: str, count: int | None = None) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(root.parent),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "COUNTERFORGE_RUN_MODE": mode,
    }
    if count is not None:
        env["COUNTERFORGE_FIXTURE_COUNT"] = str(count)
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "pytest",
            "-q",
            "--tb=short",
            "-s",
            "-p",
            "no:cacheprovider",
            "tests",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


def main() -> int:
    # Explicit /tmp: no app DATA_ROOT, DB, secrets, repository imports or env inheritance.
    with tempfile.TemporaryDirectory(prefix="counterforge-export-", dir="/tmp") as directory:
        root = Path(directory)
        if root.is_relative_to(Path(__file__).resolve().parents[1]):
            raise RuntimeError("Self-test must run outside checkout")
        with ZipFile(io.BytesIO(fixture_bundle())) as archive:
            archive.extractall(root)
        healthy = check(root, "fixture", 5)
        broken = check(root, "fixture", 4)
        replay = check(root, "replay")
        optimized = subprocess.run(
            [
                sys.executable,
                "-I",
                "-O",
                str(root / "runner/target_adapter.py"),
                "--replay-recorded",
            ],
            cwd=root,
            env={},
            capture_output=True,
            text=True,
            timeout=20,
        )
        (root / "evidence/failing.png").write_bytes(b"tampered")
        tamper = check(root, "fixture", 5)
        checks = (
            healthy.returncode == 0,
            optimized.returncode == 1 and "expected=5 actual=4" in optimized.stderr,
            broken.returncode == 1 and "AssertionError: expected=5 actual=4" in broken.stdout,
            replay.returncode == 1
            and "OFFLINE RECORDED REPLAY" in replay.stdout
            and "AssertionError: expected=5 actual=4" in replay.stdout,
            tamper.returncode == 1
            and "integrity" in tamper.stdout
            and "LOCAL FIXTURE invocation" not in tamper.stdout,
        )
        if not all(checks):
            for label, result in [
                ("healthy", healthy),
                ("optimized", optimized),
                ("broken", broken),
                ("replay", replay),
                ("tamper", tamper),
            ]:
                print(f"{label}: exit={result.returncode}\n{result.stdout}\n{result.stderr}")
            return 1
        print(
            "LOCAL_FIXTURE offline runner self-test outside checkout; restricted environment; no provider calls"
        )
        print(
            f"healthy={healthy.returncode} broken={broken.returncode} replay={replay.returncode} tamper={tamper.returncode} optimized={optimized.returncode}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
