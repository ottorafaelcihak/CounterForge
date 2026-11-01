"""Offline consumer contract: trusted identity, structured verdicts and fixed controls."""

import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import time
import warnings
from dataclasses import asdict, replace
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile, ZipInfo

import pytest
from counterforge_regression import ci

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/regression_ci"
PIN = "e91352ad992ea233cd6429b18218b92de796b2551ff4193896713428bc46b6a6"
CONTROLS = [
    ("healthy", "fixture", 5, 0, "PASS", "OK", 5, 5),
    ("broken", "fixture", 4, 1, "REGRESSION", "COUNT_MISMATCH", 5, 4),
    ("replay", "replay", None, 1, "REGRESSION", "COUNT_MISMATCH", 5, 4),
    ("tampered", "fixture", 5, 2, "EXECUTION_ERROR", "BUNDLE_SHA_MISMATCH", None, None),
]


@pytest.fixture
def bundle():
    data = (FIXTURES / "v1-fixture.zip").read_bytes()
    assert len(data) == 8234
    assert hashlib.sha256(data).hexdigest() == PIN
    return data


@pytest.mark.parametrize("case,mode,count,exit_code,verdict,reason,expected,actual", CONTROLS)
def test_real_isolated_child_controls(
    bundle, case, mode, count, exit_code, verdict, reason, expected, actual
):
    if case == "tampered":
        bundle = bytes([bundle[0] ^ 1]) + bundle[1:]
    result = ci.evaluate_bundle(
        bundle, trusted_sha256=PIN, mode=mode, fixture_count=count, case_id=case
    )
    assert (result.exit_code, result.verdict, result.reason_code) == (exit_code, verdict, reason)
    assert (result.expected_count, result.actual_count) == (expected, actual)
    assert result.provider_calls == result.cost_usd == 0
    assert result.case_id == case


def test_outer_pin_stops_before_child(bundle, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unverified bytes reached child")

    monkeypatch.setattr(ci, "_invoke_child", forbidden)
    result = ci.evaluate_bundle(bundle, trusted_sha256="0" * 64, mode="fixture", fixture_count=5)
    assert result.reason_code == "BUNDLE_SHA_MISMATCH"
    assert result.exit_code == 2


def test_catalog_and_suite_have_fixed_expectations():
    entries = ci.parse_catalog((FIXTURES / "catalog.json").read_bytes(), catalog_dir=FIXTURES)
    assert [e.case_id for e in entries] == [c[0] for c in CONTROLS]
    report = ci.run_catalog()
    assert (report.exit_code, report.verdict, report.reason_code) == (0, "PASS", "OK")
    assert report.selected_ids == ("healthy", "broken", "replay", "tampered")
    assert len(report.checks) == 4
    assert all(check.matched for check in report.checks)


@pytest.mark.parametrize("case,mode,count,exit_code,verdict,reason,expected,actual", CONTROLS)
def test_public_case_cli_reports_genuine_exits(
    tmp_path, capsys, case, mode, count, exit_code, verdict, reason, expected, actual
):
    destination = tmp_path / "result.json"
    assert ci.main(["case", "--case-id", case, "--report", str(destination)]) == exit_code
    report = json.loads(destination.read_bytes())
    assert json.loads(capsys.readouterr().out) == report
    assert (report["exit_code"], report["verdict"], report["reason_code"]) == (
        exit_code,
        verdict,
        reason,
    )
    assert (report["expected_count"], report["actual_count"]) == (expected, actual)
    assert len(destination.read_bytes()) <= 16384


def test_exact_control_comparison_rejects_every_changed_dimension():
    actual = ci.CaseResult(
        1, "broken", "REGRESSION", 1, "COUNT_MISMATCH", 5, 4, PIN, "fixture", "LOCAL_FIXTURE"
    )
    expected = ci.ExpectedOutcome(1, "REGRESSION", "COUNT_MISMATCH", 5, 4)
    assert ci.matches_control(actual, expected)
    for field, value in [
        ("exit_code", 2),
        ("verdict", "EXECUTION_ERROR"),
        ("reason_code", "CHILD_TIMEOUT"),
        ("expected_count", None),
        ("actual_count", 5),
    ]:
        assert not ci.matches_control(replace(actual, **{field: value}), expected)


def test_suite_cli_and_explicit_subset(tmp_path, capsys):
    report = tmp_path / "suite.json"
    assert ci.main(["suite", "--report", str(report)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == json.loads(report.read_bytes())
    assert result["kind"] == "PIPELINE_SELF_TEST"
    assert result["selected_ids"] == ["healthy", "broken", "replay", "tampered"]
    assert all(c["matched"] for c in result["checks"])
    assert ci.run_catalog(["replay", "healthy"]).selected_ids == ("replay", "healthy")


# Task 2: these tests challenge boundaries independently of the trusted runner.


def encode_catalog(cases):
    return json.dumps({"schema_version": 1, "cases": cases}).encode()


def ordinary(case="real"):
    return {
        "case_id": case,
        "bundle_path": "v1-fixture.zip",
        "expected_sha256": PIN,
        "mode": "fixture",
        "fixture_count": 5,
    }


def protocol(**updates):
    result = {
        "verdict": "PASS",
        "reason_code": "OK",
        "expected_count": 5,
        "actual_count": 5,
        "mode": "fixture",
        "provenance": "LOCAL_FIXTURE",
    }
    result.update(updates)
    return json.dumps(result).encode()


def rewrite_bundle(bundle, change):
    with ZipFile(io.BytesIO(bundle)) as source:
        members = {n: source.read(n) for n in source.namelist()}
    change(members)
    # Keep inner hashes consistent; rejection must not depend only on inner integrity.
    manifest = json.loads(members["manifest.json"])
    manifest["sha256"] = {
        n: hashlib.sha256(v).hexdigest()
        for n, v in members.items()
        if n not in {"manifest.json", "sha256sums.txt"}
    }
    members["manifest.json"] = json.dumps(manifest).encode()
    members["sha256sums.txt"] = "".join(
        f"{hashlib.sha256(v).hexdigest()}  {n}\n"
        for n, v in sorted(members.items())
        if n != "sha256sums.txt"
    ).encode()
    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_STORED) as target:
        for n, v in members.items():
            target.writestr(n, v)
    return output.getvalue()


@pytest.mark.parametrize(
    "changes",
    [
        {"mode": "live"},
        {"mode": "replay", "fixture_count": 5},
        {"fixture_count": None},
        {"fixture_count": True},
        {"fixture_count": -1},
        {"fixture_count": 1001},
        {"trusted_sha256": "short"},
        {"trusted_sha256": True},
        {"case_id": "../../raw"},
        {"zip_bytes": "https://invalid/fixture.zip"},
    ],
)
def test_api_invalid_input_is_safe(bundle, changes):
    args = dict(zip_bytes=bundle, trusted_sha256=PIN, mode="fixture", fixture_count=5)
    args.update(changes)
    result = ci.evaluate_bundle(**args)
    assert (result.exit_code, result.reason_code) == (2, "INVALID_INPUT")
    assert result.expected_count is result.actual_count is None


@pytest.mark.parametrize(
    "mutation", ["absolute", "parent", "symlink", "duplicate", "members", "declared-size", "runner"]
)
def test_malicious_archive_rejected_before_child(bundle, monkeypatch, mutation):
    def forbidden(*args):
        pytest.fail("hostile archive invoked child")

    monkeypatch.setattr(ci, "_invoke_child", forbidden)
    if mutation == "runner":
        data = rewrite_bundle(
            bundle,
            lambda m: m.__setitem__(
                "runner/target_adapter.py", b"raise RuntimeError('untrusted')\n"
            ),
        )
    else:
        output = io.BytesIO(bundle)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with ZipFile(output, "a") as archive:
                if mutation == "symlink":
                    info = ZipInfo("evidence/link")
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    archive.writestr(info, "target")
                elif mutation == "members":
                    for i in range(41):
                        archive.writestr(f"evidence/x{i}", b"x")
                else:
                    archive.writestr(
                        {
                            "absolute": "/absolute",
                            "parent": "../escape",
                            "duplicate": "manifest.json",
                            "declared-size": "evidence/big",
                        }[mutation],
                        b"x",
                    )
        data = output.getvalue()
        if mutation == "declared-size":
            index = data.rfind(b"PK\x01\x02")
            data = (
                data[: index + 24] + (101 * 1024 * 1024).to_bytes(4, "little") + data[index + 28 :]
            )
    result = ci.evaluate_bundle(
        data, trusted_sha256=hashlib.sha256(data).hexdigest(), mode="fixture", fixture_count=5
    )
    assert (result.exit_code, result.reason_code) == (2, "BUNDLE_INVALID")
    assert (
        ci.evaluate_bundle(data, trusted_sha256=PIN, mode="fixture", fixture_count=5).reason_code
        == "BUNDLE_SHA_MISMATCH"
    )


@pytest.mark.parametrize(
    "output,code",
    [
        (b"", 1),
        (b"AssertionError expected=5 actual=4", 1),
        (b'{"verdict":', 1),
        (protocol(), 1),
        (protocol(actual_count=True), 0),
        (protocol(expected_count=True), 0),
        (protocol(extra="untrusted"), 0),
        (protocol(mode="live"), 0),
        (protocol(provenance="raw text"), 0),
        (protocol(reason_code="untrusted"), 0),
        (protocol(actual_count=1001), 0),
        (protocol() + protocol(), 0),
        (protocol()[:-1] + b',"mode":"fixture"}', 0),
        (
            protocol(
                verdict="EXECUTION_ERROR",
                reason_code="BUNDLE_SHA_MISMATCH",
                expected_count=None,
                actual_count=None,
            ),
            2,
        ),
        (
            protocol(
                verdict="EXECUTION_ERROR",
                reason_code="CHILD_EXECUTION_ERROR",
                expected_count=5,
                actual_count=4,
            ),
            2,
        ),
    ],
)
def test_counterfeit_protocol_cannot_be_regression(bundle, monkeypatch, output, code):
    monkeypatch.setattr(ci, "_invoke_child", lambda *a: (code, output, b""))
    result = ci.evaluate_bundle(bundle, trusted_sha256=PIN, mode="fixture", fixture_count=5)
    assert (result.exit_code, result.reason_code) == (2, "UNKNOWN_CHILD_OUTPUT")
    assert result.expected_count is result.actual_count is None


@pytest.mark.parametrize(
    "exception,reason",
    [
        (ci.standalone.ExecutionError("private /tmp/path"), "RUNNER_EXECUTION_ERROR"),
        (AssertionError("other expected=5 actual=4;"), "CHILD_EXECUTION_ERROR"),
        (
            AssertionError("expected=5 actual=4; mode=fixture provenance=LOCAL_FIXTURE\nraw"),
            "CHILD_EXECUTION_ERROR",
        ),
        (RuntimeError("private"), "CHILD_EXECUTION_ERROR"),
    ],
)
def test_child_exception_taxonomy_suppresses_runner_text(
    bundle, tmp_path, monkeypatch, capsys, exception, reason
):
    with ZipFile(io.BytesIO(bundle)) as archive:
        for name in archive.namelist():
            destination = tmp_path / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(name))

    def broken(*args):
        print("private /tmp/path " * 10000)
        print("raw stderr", file=sys.stderr)
        raise exception

    monkeypatch.setattr(ci.standalone, "run", broken)
    assert ci.main(["--_child", str(tmp_path), "fixture", "5"]) == 2
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["reason_code"] == reason
    assert report["expected_count"] is report["actual_count"] is None
    assert captured.err == ""
    assert "private" not in captured.out
    assert len(captured.out) < 8192


def test_unparseable_recorded_answer_is_execution_error(bundle):
    def change(members):
        observations = json.loads(members["evidence/target-responses.json"])
        for observation in observations:
            if observation["phase"] == "CONFIRMATION":
                observation["raw_text"] = "unknown"
        members["evidence/target-responses.json"] = json.dumps(observations).encode()

    data = rewrite_bundle(bundle, change)
    result = ci.evaluate_bundle(
        data, trusted_sha256=hashlib.sha256(data).hexdigest(), mode="replay"
    )
    assert (result.exit_code, result.reason_code) == (2, "RUNNER_EXECUTION_ERROR")


@pytest.mark.parametrize("count", [0, 1000])
def test_fixture_boundary_counts_are_strict_valid(bundle, count):
    result = ci.evaluate_bundle(bundle, trusted_sha256=PIN, mode="fixture", fixture_count=count)
    assert (result.exit_code, result.actual_count) == (1, count)


@pytest.mark.parametrize(
    "change",
    [
        lambda c: [],
        lambda c: [ordinary(str(i)) for i in range(11)],
        lambda c: [c, c],
        lambda c: [c, dict(c, case_id="other", expected_sha256="0" * 64)],
        lambda c: [dict(c, bundle_path="https://host/input.zip")],
        lambda c: [dict(c, bundle_path="missing.zip")],
        lambda c: [dict(c, bundle_path="../input.zip")],
        lambda c: [dict(c, expected_sha256="short")],
        lambda c: [dict(c, mode="live")],
        lambda c: [dict(c, mode="replay")],
        lambda c: [dict(c, fixture_count=True)],
        lambda c: [dict(c, variant="flip_first_byte")],
        lambda c: [dict(c, expectation={"exit_code": 0})],
    ],
)
def test_catalog_fails_closed(tmp_path, bundle, change):
    (tmp_path / "v1-fixture.zip").write_bytes(bundle)
    with pytest.raises(ValueError, match="^CATALOG_INVALID$"):
        ci.parse_catalog(encode_catalog(change(ordinary())), catalog_dir=tmp_path)


def test_catalog_maximum_reuse_and_duplicate_json(tmp_path, bundle):
    (tmp_path / "v1-fixture.zip").write_bytes(bundle)
    entries = ci.parse_catalog(
        encode_catalog([ordinary(str(i)) for i in range(10)]), catalog_dir=tmp_path
    )
    assert len(entries) == 10
    for data in [
        b"{}",
        b'{"schema_version":1,"schema_version":1,"cases":[]}',
        b'{"schema_version":true,"cases":[]}',
    ]:
        with pytest.raises(ValueError, match="CATALOG_INVALID"):
            ci.parse_catalog(data, catalog_dir=tmp_path)
    linked = tmp_path / "linked"
    linked.symlink_to(FIXTURES, target_is_directory=True)
    with pytest.raises(ValueError, match="CATALOG_INVALID"):
        ci.parse_catalog(
            encode_catalog([dict(ordinary(), bundle_path="linked/v1-fixture.zip")]),
            catalog_dir=tmp_path,
        )
    with pytest.raises(ValueError, match="CATALOG_INVALID"):
        ci.parse_catalog(encode_catalog([ordinary()]), catalog_dir=linked)


def test_catalog_fixed_control_expectations_cannot_be_overridden():
    source = json.loads((FIXTURES / "catalog.json").read_bytes())
    source["cases"][0]["expectation"]["exit_code"] = True
    with pytest.raises(ValueError, match="CATALOG_INVALID"):
        ci.parse_catalog(json.dumps(source).encode(), catalog_dir=FIXTURES)
    source["cases"][0]["expectation"]["exit_code"] = 1
    with pytest.raises(ValueError, match="CATALOG_INVALID"):
        ci.parse_catalog(json.dumps(source).encode(), catalog_dir=FIXTURES)


@pytest.mark.parametrize("ids", [[], ["healthy", "healthy"], ["missing"], ["real"], ["../private"]])
def test_suite_invalid_selection_reports_global_error(ids):
    report = ci.run_catalog(ids)
    assert report.exit_code == 2
    assert report.reason_code == "CATALOG_INVALID"
    assert report.checks == ()


def test_global_catalog_error_still_has_suite_context(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ci, "_CATALOG", tmp_path / "missing.json")
    result = ci.run_catalog()
    assert result.reason_code == "CATALOG_INVALID"
    assert result.selected_ids == ("healthy", "broken", "replay", "tampered")
    path = tmp_path / "result.json"
    assert ci.main(["suite", "--report", str(path)]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["reason_code"] == "CATALOG_INVALID"
    assert report["selected_ids"] == ["healthy", "broken", "replay", "tampered"]
    assert report["checks"] == []


@pytest.mark.parametrize(
    "args",
    [
        ["case", "--case-id", "missing"],
        ["case", "--case-id", "healthy", "--live"],
        ["suite", "--pin", PIN],
        ["suite", "--catalog", "other.json"],
        ["suite", "--fixture-count", "5"],
    ],
)
def test_cli_unsupported_inputs_have_safe_json(tmp_path, capsys, args):
    result = ci.main([*args, "--report", str(tmp_path / "report.json")])
    assert result == 2
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report["reason_code"] in {"INVALID_INPUT", "CATALOG_INVALID"}
    assert output.err == ""
    assert "Traceback" not in output.out


@pytest.mark.parametrize(
    "kind",
    [
        "input",
        "catalog",
        "symlink",
        "parent-symlink",
        "missing-parent",
        "extension",
        "directory",
        "hardlink",
    ],
)
def test_report_path_rejects_unsafe_destinations(tmp_path, capsys, monkeypatch, kind):
    path = tmp_path / "report.json"
    if kind == "input":
        path = FIXTURES / "v1-fixture.zip"
    elif kind == "catalog":
        path = FIXTURES / "catalog.json"
    elif kind == "symlink":
        path.symlink_to(tmp_path / "other.json")
    elif kind == "parent-symlink":
        link = tmp_path / "linked"
        link.symlink_to(tmp_path, target_is_directory=True)
        path = link / "report.json"
    elif kind == "missing-parent":
        path = tmp_path / "absent/report.json"
    elif kind == "extension":
        path = tmp_path / "report.txt"
    elif kind == "directory":
        path.mkdir()
    elif kind == "hardlink":
        local = tmp_path / "catalog.json"
        local.write_bytes((FIXTURES / "catalog.json").read_bytes())
        (tmp_path / "v1-fixture.zip").write_bytes((FIXTURES / "v1-fixture.zip").read_bytes())
        monkeypatch.setattr(ci, "_CATALOG", local)
        path.hardlink_to(local)
    assert ci.main(["suite", "--case-id", "tampered", "--report", str(path)]) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["reason_code"] == "REPORT_PATH_UNSAFE"
    assert str(tmp_path) not in output


def test_atomic_report_failure_safe_stdout_only(tmp_path, monkeypatch, capsys):
    path = tmp_path / "result.json"
    path.write_text("previous")

    def denied(*args):
        raise OSError("private path and environment")

    monkeypatch.setattr(ci.os, "replace", denied)
    assert ci.main(["suite", "--case-id", "tampered", "--report", str(path)]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["reason_code"] == "REPORT_WRITE_ERROR"
    assert report["checks"] == []
    assert path.read_text() == "previous"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("failure", [None, "CHILD_TIMEOUT"])
def test_snapshot_rehashed_readonly_and_cleanup_restored(bundle, monkeypatch, failure):
    observed = []
    original_restore = ci._restore

    def restore(root):
        original_restore(root)
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert all(
            stat.S_IMODE(p.stat().st_mode) == (0o700 if p.is_dir() else 0o600)
            for p in root.rglob("*")
        )

    def child(root, mode, count, home):
        observed.append(root.parent)
        assert not root.is_relative_to(Path.cwd())
        assert home.is_dir()
        with ZipFile(io.BytesIO(bundle)) as archive:
            assert {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} == set(
                archive.namelist()
            )
            for n in archive.namelist():
                assert (
                    hashlib.sha256((root / n).read_bytes()).digest()
                    == hashlib.sha256(archive.read(n)).digest()
                )
        assert stat.S_IMODE(root.stat().st_mode) == 0o500
        assert all(
            stat.S_IMODE(p.stat().st_mode) == (0o500 if p.is_dir() else 0o400)
            for p in root.rglob("*")
        )
        if failure:
            raise ci._ChildFailure(failure)
        return 0, protocol(), b""

    monkeypatch.setattr(ci, "_invoke_child", child)
    monkeypatch.setattr(ci, "_restore", restore)
    result = ci.evaluate_bundle(bundle, trusted_sha256=PIN, mode="fixture", fixture_count=5)
    assert result.reason_code == (failure or "OK")
    assert len(observed) == 1
    assert not observed[0].exists()


@pytest.mark.parametrize(
    "behavior,reason",
    [
        ("timeout", "CHILD_TIMEOUT"),
        ("stdout", "UNKNOWN_CHILD_OUTPUT"),
        ("stderr", "UNKNOWN_CHILD_OUTPUT"),
        ("missing-module", "UNKNOWN_CHILD_OUTPUT"),
    ],
)
def test_real_child_io_bounds_and_exact_environment(bundle, monkeypatch, behavior, reason):
    popen = subprocess.Popen
    seen = []
    code = {
        "timeout": "import time; time.sleep(60)",
        "stdout": "import os,time; os.write(1,b'x'*8193);time.sleep(60)",
        "stderr": "import os,time; os.write(2,b'x'*8193);time.sleep(60)",
        "missing-module": "raise ImportError('untrusted raw private path')",
    }[behavior]

    def substitute(args, **kwargs):
        assert args[:5] == [sys.executable, "-I", "-m", "counterforge_regression.ci", "--_child"]
        assert args[-2:] == ["fixture", "5"]
        assert set(kwargs["env"]) == {"PATH", "HOME"}
        assert Path(kwargs["env"]["HOME"]).is_dir()
        assert kwargs["cwd"] == Path(kwargs["env"]["HOME"])
        assert kwargs["env"]["PATH"] == os.defpath
        seen.append(Path(args[5]).parent)
        return popen([sys.executable, "-I", "-c", code], **kwargs)

    monkeypatch.setenv("MODEL_API_KEY", "dummy")
    monkeypatch.setenv("RUN_META_LIVE_TESTS", "1")
    monkeypatch.setenv("PYTHONPATH", "private")
    monkeypatch.setattr(ci.subprocess, "Popen", substitute)
    monkeypatch.setattr(ci, "_CHILD_SECONDS", 0.3)
    started = time.monotonic()
    result = ci.evaluate_bundle(bundle, trusted_sha256=PIN, mode="fixture", fixture_count=5)
    assert time.monotonic() - started < 3
    assert result.reason_code == reason
    assert result.exit_code == 2
    assert result.provider_calls == result.cost_usd == 0
    assert result.expected_count is result.actual_count is None
    assert seen and not seen[0].exists()
    safe = json.dumps(asdict(result))
    assert "private" not in safe and "Traceback" not in safe and "/tmp/" not in safe


def test_non_control_entries_cannot_enter_suite(tmp_path, bundle, monkeypatch):
    (tmp_path / "v1-fixture.zip").write_bytes(bundle)
    catalog = tmp_path / "catalog.json"
    catalog.write_bytes(encode_catalog([ordinary()]))
    monkeypatch.setattr(ci, "_CATALOG", catalog)
    assert ci.run_catalog(["real"]).reason_code == "CATALOG_INVALID"


def test_runtime_error_cannot_match_expected_tamper(monkeypatch):
    monkeypatch.setattr(
        ci,
        "_evaluate",
        lambda entry: ci.CaseResult(
            1,
            entry.case_id,
            "EXECUTION_ERROR",
            2,
            "CHILD_TIMEOUT",
            None,
            None,
            PIN,
            entry.mode,
            None,
        ),
    )
    result = ci.run_catalog(["tampered"])
    assert (result.exit_code, result.reason_code) == (2, "SELF_TEST_MISMATCH")
    assert not result.checks[0].matched


def test_fixture_protocol_must_agree_with_requested_count(bundle, monkeypatch):
    monkeypatch.setattr(ci, "_invoke_child", lambda *args: (0, protocol(), b""))
    result = ci.evaluate_bundle(bundle, trusted_sha256=PIN, mode="fixture", fixture_count=4)
    assert (result.exit_code, result.reason_code) == (2, "UNKNOWN_CHILD_OUTPUT")


@pytest.mark.parametrize("count", [-1, 1001, 9999999999])
def test_replay_wrong_signed_counts_remain_regressions(bundle, count):
    def change(members):
        observations = json.loads(members["evidence/target-responses.json"])
        for observation in observations:
            if observation["phase"] == "CONFIRMATION":
                observation["raw_text"] = str(count)
        members["evidence/target-responses.json"] = json.dumps(observations).encode()

    data = rewrite_bundle(bundle, change)
    result = ci.evaluate_bundle(
        data, trusted_sha256=hashlib.sha256(data).hexdigest(), mode="replay"
    )
    assert (result.exit_code, result.reason_code, result.actual_count) == (
        1,
        "COUNT_MISMATCH",
        count,
    )


def test_comparison_does_not_accept_bool_counts():
    actual = ci.CaseResult(
        1, "broken", "REGRESSION", 1, "COUNT_MISMATCH", 5, 4, PIN, "fixture", "LOCAL_FIXTURE"
    )
    expected = ci.ExpectedOutcome(1, "REGRESSION", "COUNT_MISMATCH", 5, 4)
    assert not ci.matches_control(replace(actual, exit_code=True), expected)
    assert not ci.matches_control(actual, replace(expected, exit_code=True))


@pytest.mark.parametrize("selection", [42, True, "healthy"])
def test_invalid_python_suite_selection_is_structured(selection):
    report = ci.run_catalog(selection)
    assert (report.exit_code, report.reason_code) == (2, "CATALOG_INVALID")


# Task 3: the consumer trusts the PR base and renders validated JSON only.
WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/ci-regression.yml"


def test_workflow_policy_boundary():
    text = WORKFLOW.read_text()
    import re

    assert "name: CounterForge offline regression" in text
    for forbidden in [
        "secrets.",
        "MODEL_API_KEY",
        "pull_request_target",
        "workflow_run",
        "schedule:",
        "inputs:",
        "github.event.pull_request.head",
        "continue-on-error",
    ]:
        assert forbidden not in text
    actions = re.findall(r"uses: ([^\s]+)", text)
    assert len(actions) == 3
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", action) for action in actions)
    assert "contents: read" in text and "timeout-minutes: 10" in text
    assert "group: ${{ github.workflow }}-${{ github.ref }}" in text
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in text
    assert "github.event.pull_request.base.sha" in text
    assert "github.event.pull_request.base.ref == 'master'" in text
    assert "github.ref == 'refs/heads/master'" in text
    assert "persist-credentials: false" in text
    assert "enable-cache: false" in text
    assert "uv sync --locked" in text
    assert ".venv/bin/python -I -m counterforge_regression.ci suite --report" in text
    assert "case --case-id" in text
    assert "if-no-files-found: error" in text
    assert "if: ${{ always() }}" in text


def summary_program():
    import textwrap

    text = WORKFLOW.read_text()
    block = text.split("# BEGIN VALIDATED SUMMARY\n", 1)[1].split("# END VALIDATED SUMMARY", 1)[0]
    return textwrap.dedent(block).rstrip()


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "bool",
        "reason",
        "expected",
        "ids",
        "matched",
        "raw",
        "cost",
        "duplicate",
        "oversize",
        "missing",
    ],
)
def test_workflow_summary_validates_before_writing(tmp_path, mutation):
    # Exercise the actual inline reader without GitHub or a YAML dependency.
    report = json.loads(json.dumps(asdict(ci.run_catalog())))
    path, summary = tmp_path / "suite.json", tmp_path / "summary.md"
    summary.write_text("previous\n")
    if mutation == "bool":
        report["checks"][1]["actual"]["exit_code"] = True
    elif mutation == "reason":
        report["checks"][0]["actual"]["reason_code"] = "raw PRIVATE payload"
    elif mutation == "expected":
        report["checks"][0]["expected"]["actual_count"] = 4
    elif mutation == "ids":
        report["selected_ids"][0] = "raw PRIVATE payload"
    elif mutation == "matched":
        report["checks"][0]["matched"] = False
    elif mutation == "raw":
        report["checks"][0]["actual"]["provenance"] = "raw PRIVATE payload"
    elif mutation == "cost":
        report["cost_usd"] = 1
    if mutation != "missing":
        data = json.dumps(report)
        if mutation == "duplicate":
            data = data[:-1] + ',"cost_usd":0}'
        if mutation == "oversize":
            data += " " * 16385
        path.write_text(data)
    environment = {
        "PATH": os.defpath,
        "REPORT_PATH": str(path),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    process = subprocess.run(
        [sys.executable, "-I", "-c", summary_program()],
        env=environment,
        capture_output=True,
        timeout=5,
    )
    assert process.stdout == process.stderr == b""
    if mutation is None:
        assert process.returncode == 0
        text = summary.read_text()
        assert "healthy" in text and "tampered" in text and "BUNDLE_SHA_MISMATCH" in text
        assert "expected" in text.lower() and "actual" in text.lower()
    else:
        assert process.returncode == 2
        assert summary.read_text() == "previous\n"


@pytest.mark.parametrize("failure", ["regression", "execution", "tamper-timeout", "lying-pass"])
def test_workflow_summary_reports_consistent_failures_and_rejects_false_pass(tmp_path, failure):
    report = json.loads(json.dumps(asdict(ci.run_catalog())))
    check = report["checks"][3 if failure == "tamper-timeout" else 0]
    actual = check["actual"]
    if failure in ("execution", "tamper-timeout"):
        actual.update(
            exit_code=2,
            verdict="EXECUTION_ERROR",
            reason_code="CHILD_TIMEOUT",
            expected_count=None,
            actual_count=None,
        )
        code = 2
    else:
        actual.update(
            exit_code=1, verdict="REGRESSION", reason_code="COUNT_MISMATCH", actual_count=4
        )
        code = 1
    check["matched"] = False
    if failure != "lying-pass":
        report.update(
            exit_code=code,
            verdict="EXECUTION_ERROR" if code == 2 else "REGRESSION",
            reason_code="SELF_TEST_MISMATCH",
        )
    path, summary = tmp_path / "suite.json", tmp_path / "summary.md"
    path.write_text(json.dumps(report))
    summary.write_text("previous\n")
    process = subprocess.run(
        [sys.executable, "-I", "-c", summary_program()],
        env={"PATH": os.defpath, "REPORT_PATH": str(path), "GITHUB_STEP_SUMMARY": str(summary)},
        capture_output=True,
        timeout=5,
    )
    assert process.stdout == process.stderr == b""
    assert process.returncode == (2 if failure == "lying-pass" else code)
    if failure == "lying-pass":
        assert summary.read_text() == "previous\n"
    else:
        text = summary.read_text()
        assert text.startswith("previous\n## Offline pipeline self-test\n")
        assert '"matched": false' in text
        assert actual["reason_code"] in text


def test_real_offline_child_and_catalog_ignore_parent_cwd_environment(
    bundle, tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MODEL_API_KEY", "dummy")
    monkeypatch.setenv("RUN_META_LIVE_TESTS", "1")
    monkeypatch.setenv("COUNTERFORGE_CI_CATALOG", "https://private.invalid/catalog.json")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    (tmp_path / "counterforge_regression.py").write_text("raise RuntimeError('cwd code executed')")
    report_path = tmp_path / "result.json"
    assert ci.main(["case", "--case-id", "healthy", "--report", str(report_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["verdict"] == "PASS"
    assert result["provider_calls"] == result["cost_usd"] == 0
    assert "private.invalid" not in json.dumps(result)


def test_unsafe_cli_id_not_echoed(tmp_path, capsys):
    raw = "https://private.invalid/path-token-value"
    assert ci.main(["case", "--case-id", raw, "--report", str(tmp_path / "result.json")]) == 2
    output = capsys.readouterr()
    assert json.loads(output.out)["reason_code"] == "CATALOG_INVALID"
    assert raw not in output.out and output.err == ""


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["relative", "fixture", "5"],
        ["/tmp", "live"],
        ["/tmp", "replay", "5"],
        ["/tmp", "fixture", "true"],
        ["/tmp", "fixture", "1001"],
        ["/tmp", "fixture", "5", "extra"],
    ],
)
def test_private_child_rejects_invalid_argv_safely(args, capsys):
    assert ci.main(["--_child", *args]) == 2
    output = capsys.readouterr()
    assert json.loads(output.out)["reason_code"] == "CHILD_EXECUTION_ERROR"
    assert output.err == ""


def test_parent_rehash_detects_corrupted_snapshot_before_child(bundle, monkeypatch):
    write = Path.write_bytes

    def corrupt(path, content):
        return write(path, content + b"corruption" if path.name == "original.png" else content)

    def forbidden(*args):
        pytest.fail("modified snapshot reached runner")

    monkeypatch.setattr(Path, "write_bytes", corrupt)
    monkeypatch.setattr(ci, "_invoke_child", forbidden)
    result = ci.evaluate_bundle(bundle, trusted_sha256=PIN, mode="fixture", fixture_count=5)
    assert result.exit_code == 2
    assert result.expected_count is result.actual_count is None


def test_manifest_duplicate_keys_remain_trusted_runner_contract(bundle):
    # Existing bundle verification accepts JSON duplicates; standalone.verify
    # rejects them. The wrapper does not change either v1 contract.
    def change(members):
        manifest = members["manifest.json"]
        members["manifest.json"] = manifest[:-1] + b',"format_version":"1.0.0"}'

    with ZipFile(io.BytesIO(bundle)) as archive:
        members = {n: archive.read(n) for n in archive.namelist()}
    change(members)
    members["sha256sums.txt"] = "".join(
        f"{hashlib.sha256(v).hexdigest()}  {n}\n"
        for n, v in sorted(members.items())
        if n != "sha256sums.txt"
    ).encode()
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        for n, v in members.items():
            archive.writestr(n, v)
    data = output.getvalue()
    ci.verify_bundle(data)
    result = ci.evaluate_bundle(
        data, trusted_sha256=hashlib.sha256(data).hexdigest(), mode="fixture", fixture_count=5
    )
    assert (result.exit_code, result.reason_code) == (2, "RUNNER_EXECUTION_ERROR")
    assert result.expected_count is result.actual_count is None
