"""Extracted runner executes without repository imports, database, or credentials."""

import io
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

from tests.unit.test_regression_bundle import evidence, example_manifest


def extracted(tmp_path):
    from counterforge_regression.bundle import build_bundle

    bundle = build_bundle(example_manifest(), evidence())
    with ZipFile(io.BytesIO(bundle.zip_bytes)) as archive:
        archive.extractall(tmp_path)
    return tmp_path


def invoke(root, mode, count=None):
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(root),
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


@pytest.mark.parametrize("count,code", [(5, 0), (4, 1)])
def test_clean_fixture_healthy_passes_broken_asserts(tmp_path, count, code):
    result = invoke(extracted(tmp_path), "fixture", count)
    assert result.returncode == code, result.stdout + result.stderr
    if code:
        assert "expected=5 actual=4" in result.stdout
        assert "ExecutionError" not in result.stdout


def test_recorded_replay_is_offline_labelled_and_still_fails(tmp_path):
    root = extracted(tmp_path)
    result = subprocess.run(
        [sys.executable, "-I", str(root / "runner/target_adapter.py"), "--replay-recorded"],
        cwd=root,
        env={},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "OFFLINE RECORDED REPLAY" in result.stdout
    assert "LOCAL_FIXTURE" in result.stdout and "recorded_counts=[4, 4, 4]" in result.stdout
    assert "expected=5 actual=4" in result.stderr


def test_tampering_blocks_even_fixture_invocation(tmp_path):
    root = extracted(tmp_path)
    (root / "evidence/failing.png").write_bytes(b"tampered")
    result = invoke(root, "fixture", 5)
    assert result.returncode == 1 and "integrity" in result.stdout.lower()
    assert "LOCAL FIXTURE invocation" not in result.stdout


def test_unknown_parser_and_no_opt_in_are_execution_errors(tmp_path):
    root = extracted(tmp_path)
    result = invoke(root, "fixture", "I cannot answer")
    assert result.returncode == 1 and "ExecutionError" in result.stdout
    assert "expected=5 actual=" not in result.stdout
    result = invoke(root, "")
    assert result.returncode == 1 and "explicit" in result.stdout


def test_verify_export_command_runs_outside_checkout_with_restricted_environment():
    result = subprocess.run(
        [sys.executable, "-m", "scripts.verify_export"],
        cwd=Path(__file__).resolve().parents[2],
        env={"PATH": "/usr/bin:/bin", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "healthy=0 broken=1 replay=1 tamper=1" in result.stdout
    assert "LOCAL_FIXTURE" in result.stdout


@pytest.mark.parametrize("flags", [["-I", "-O"], ["-O"]])
def test_optimized_python_cannot_turn_wrong_count_into_success(tmp_path, flags):
    root = extracted(tmp_path)
    result = subprocess.run(
        [sys.executable, *flags, str(root / "runner/target_adapter.py"), "--replay-recorded"],
        cwd=root,
        env={"PYTHONOPTIMIZE": "1"},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "expected=5 actual=4" in result.stderr
    assert "correctness assertion passed" not in result.stdout
