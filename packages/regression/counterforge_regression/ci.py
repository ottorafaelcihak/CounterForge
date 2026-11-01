"""Trusted, offline-only consumer for reviewed v1 regression bundles."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import selectors
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Never, cast
from zipfile import ZipFile

from . import standalone
from .bundle import verify_bundle

Mode = Literal["fixture", "replay"]
Verdict = Literal["PASS", "REGRESSION", "EXECUTION_ERROR"]


@dataclass(frozen=True)
class ExpectedOutcome:
    exit_code: int
    verdict: Verdict
    reason_code: str
    expected_count: int | None
    actual_count: int | None


@dataclass(frozen=True)
class CiCatalogEntry:
    case_id: str
    bundle_path: Path
    expected_sha256: str
    mode: Mode
    fixture_count: int | None
    variant: Literal["none", "flip_first_byte"]
    expectation: ExpectedOutcome | None


@dataclass(frozen=True)
class CaseResult:
    schema_version: Literal[1]
    case_id: str | None
    verdict: Verdict
    exit_code: int
    reason_code: str
    expected_count: int | None
    actual_count: int | None
    bundle_sha256: str | None
    mode: Mode | None
    provenance: str | None
    provider_calls: Literal[0] = 0
    cost_usd: Literal[0] = 0


@dataclass(frozen=True)
class SelfTestCheck:
    expected: ExpectedOutcome
    actual: CaseResult
    matched: bool


@dataclass(frozen=True)
class SuiteReport:
    schema_version: Literal[1]
    kind: Literal["PIPELINE_SELF_TEST"]
    selected_ids: tuple[str, ...]
    checks: tuple[SelfTestCheck, ...]
    verdict: Verdict
    exit_code: int
    reason_code: str
    provider_calls: Literal[0] = 0
    cost_usd: Literal[0] = 0


_CATALOG = Path(__file__).resolve().parents[3] / "tests/fixtures/regression_ci/catalog.json"
_CONTROLS = {
    "healthy": ExpectedOutcome(0, "PASS", "OK", 5, 5),
    "broken": ExpectedOutcome(1, "REGRESSION", "COUNT_MISMATCH", 5, 4),
    "replay": ExpectedOutcome(1, "REGRESSION", "COUNT_MISMATCH", 5, 4),
    "tampered": ExpectedOutcome(2, "EXECUTION_ERROR", "BUNDLE_SHA_MISMATCH", None, None),
}
_REASONS = frozenset(
    {
        "OK",
        "COUNT_MISMATCH",
        "BUNDLE_SHA_MISMATCH",
        "BUNDLE_INVALID",
        "CATALOG_INVALID",
        "INVALID_INPUT",
        "REPORT_PATH_UNSAFE",
        "CHILD_TIMEOUT",
        "CHILD_EXECUTION_ERROR",
        "UNKNOWN_CHILD_OUTPUT",
        "RUNNER_EXECUTION_ERROR",
        "REPORT_WRITE_ERROR",
        "SELF_TEST_MISMATCH",
    }
)
_CHILD_SECONDS = 30.0
_CHILD_BYTES = 8192
_REPORT_BYTES = 16384
_MAX_ZIP = 100 * 1024 * 1024


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _json(data: bytes) -> object:
    return json.loads(data, object_pairs_hook=_unique)


def _id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) is not None


def _sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _count(value: object) -> bool:
    return type(value) is int and 0 <= value <= 1000


def _mode(mode: object, count: object) -> bool:
    return (mode == "fixture" and _count(count)) or (mode == "replay" and count is None)


def _no_symlinks(path: Path) -> bool:
    return path.is_absolute() and all(not p.is_symlink() for p in (path, *path.parents))


def _error(
    reason: str, *, case_id: str | None = None, mode: Mode | None = None, digest: str | None = None
) -> CaseResult:
    return CaseResult(
        1,
        case_id if _id(case_id) else None,
        "EXECUTION_ERROR",
        2,
        reason,
        None,
        None,
        digest,
        mode if mode in ("fixture", "replay") else None,
        None,
    )


def parse_catalog(data: bytes, *, catalog_dir: Path) -> tuple[CiCatalogEntry, ...]:
    """Validate a local catalog without executing it; failures contain no input text."""
    try:
        if not isinstance(data, bytes) or len(data) > _REPORT_BYTES:
            raise ValueError()
        if not isinstance(catalog_dir, Path):
            raise ValueError()
        root = catalog_dir.absolute()
        if not _no_symlinks(root) or not root.is_dir():
            raise ValueError()
        parsed = _json(data)
        if not isinstance(parsed, dict) or set(parsed) != {"schema_version", "cases"}:
            raise ValueError()
        if type(parsed["schema_version"]) is not int or parsed["schema_version"] != 1:
            raise ValueError()
        cases = parsed["cases"]
        if not isinstance(cases, list) or not 1 <= len(cases) <= 10:
            raise ValueError()
        result = []
        pins: dict[Path, str] = {}
        ids: set[str] = set()
        for item in cases:
            if not isinstance(item, dict):
                raise ValueError()
            required = {"case_id", "bundle_path", "expected_sha256", "mode"}
            if not required <= set(item) or set(item) - required - {
                "fixture_count",
                "variant",
                "expectation",
            }:
                raise ValueError()
            case_id, name, digest, mode = (
                item[k] for k in ("case_id", "bundle_path", "expected_sha256", "mode")
            )
            count, variant, expectation = (
                item.get("fixture_count"),
                item.get("variant", "none"),
                item.get("expectation"),
            )
            if not _id(case_id) or case_id in ids or not _sha(digest) or not _mode(mode, count):
                raise ValueError()
            if not isinstance(name, str) or not standalone.safe_name(name):
                raise ValueError()
            path = root / name
            if not _no_symlinks(path) or not path.is_file() or path.stat().st_size > _MAX_ZIP:
                raise ValueError()
            if path in pins and pins[path] != digest:
                raise ValueError()
            if variant not in ("none", "flip_first_byte") or (
                variant != "none" and case_id != "tampered"
            ):
                raise ValueError()
            expected = None
            if expectation is not None:
                if case_id not in _CONTROLS or not isinstance(expectation, dict):
                    raise ValueError()
                fixed = asdict(_CONTROLS[case_id])
                if set(expectation) != set(fixed) or any(
                    type(expectation[k]) is not type(v) or expectation[k] != v
                    for k, v in fixed.items()
                ):
                    raise ValueError()
                expected = _CONTROLS[case_id]
            ids.add(case_id)
            pins[path] = digest
            result.append(CiCatalogEntry(case_id, path, digest, mode, count, variant, expected))
        return tuple(result)
    except (OSError, ValueError, TypeError, RecursionError):
        raise ValueError("CATALOG_INVALID") from None


class _ChildFailure(Exception):
    def __init__(self, reason: str):
        self.reason = reason


def _invoke_child(
    root: Path, mode: Mode, count: int | None, home: Path
) -> tuple[int, bytes, bytes]:
    args = [sys.executable, "-I", "-m", "counterforge_regression.ci", "--_child", str(root), mode]
    if count is not None:
        args.append(str(count))
    deadline = time.monotonic() + _CHILD_SECONDS
    with subprocess.Popen(
        args,
        cwd=home,
        env={"PATH": os.defpath, "HOME": str(home)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as child:
        assert child.stdout is not None and child.stderr is not None
        captured = [bytearray(), bytearray()]
        try:
            with selectors.DefaultSelector() as selector:
                for index, stream in enumerate((child.stdout, child.stderr)):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ, index)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise _ChildFailure("CHILD_TIMEOUT")
                    for key, _ in selector.select(remaining):
                        part = os.read(key.fd, _CHILD_BYTES + 1 - len(captured[key.data]))
                        if not part:
                            selector.unregister(key.fileobj)
                            continue
                        captured[key.data].extend(part)
                        if len(captured[key.data]) > _CHILD_BYTES:
                            raise _ChildFailure("UNKNOWN_CHILD_OUTPUT")
            try:
                code = child.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                raise _ChildFailure("CHILD_TIMEOUT") from None
            return code, bytes(captured[0]), bytes(captured[1])
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()


def _protocol(
    code: int,
    output: bytes,
    mode: Mode,
    expected: int,
    provenance: str,
    case_id: str | None,
    digest: str,
    fixture_count: int | None,
) -> CaseResult:
    try:
        if len(output) > _CHILD_BYTES:
            raise ValueError()
        record = _json(output)
        fields = {"verdict", "reason_code", "expected_count", "actual_count", "mode", "provenance"}
        if not isinstance(record, dict) or set(record) != fields:
            raise ValueError()
        verdict, reason, exp, actual = (
            record[k] for k in ("verdict", "reason_code", "expected_count", "actual_count")
        )
        if record["mode"] != mode or (
            record["provenance"] != provenance and not (code == 2 and record["provenance"] is None)
        ):
            raise ValueError()
        if reason not in _REASONS or type(code) is not int:
            raise ValueError()
        if verdict == "EXECUTION_ERROR":
            if (
                code != 2
                or reason not in {"RUNNER_EXECUTION_ERROR", "CHILD_EXECUTION_ERROR"}
                or exp is not None
                or actual is not None
            ):
                raise ValueError()
        elif verdict in ("PASS", "REGRESSION"):
            if (
                not _count(exp)
                or type(actual) is not int
                or exp != expected
                or (mode == "fixture" and actual != fixture_count)
            ):
                raise ValueError()
            if verdict == "PASS" and (code != 0 or reason != "OK" or actual != exp):
                raise ValueError()
            if verdict == "REGRESSION" and (
                code != 1 or reason != "COUNT_MISMATCH" or actual == exp
            ):
                raise ValueError()
        else:
            raise ValueError()
        return CaseResult(
            1, case_id, verdict, code, reason, exp, actual, digest, mode, record["provenance"]
        )
    except (ValueError, TypeError, RecursionError):
        return _error("UNKNOWN_CHILD_OUTPUT", case_id=case_id, mode=mode, digest=digest)


def _restore(root: Path) -> None:
    root.chmod(0o700)
    for directory, dirs, files in os.walk(root):
        for name in dirs:
            (Path(directory) / name).chmod(0o700)
        for name in files:
            (Path(directory) / name).chmod(0o600)


def evaluate_bundle(
    zip_bytes: bytes,
    *,
    trusted_sha256: str,
    mode: Mode,
    fixture_count: int | None = None,
    case_id: str | None = None,
) -> CaseResult:
    """Verify reviewed bytes before invoking only the trusted installed offline runner."""
    if (
        not isinstance(zip_bytes, bytes)
        or not _sha(trusted_sha256)
        or not _mode(mode, fixture_count)
        or (case_id is not None and not _id(case_id))
    ):
        return _error("INVALID_INPUT", case_id=case_id, mode=mode)
    digest = hashlib.sha256(zip_bytes).hexdigest()
    if digest != trusted_sha256:
        return _error("BUNDLE_SHA_MISMATCH", case_id=case_id, mode=mode, digest=digest)
    try:
        verify_bundle(zip_bytes)
        with ZipFile(io.BytesIO(zip_bytes)) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        # Preserve v1 manifest parsing; duplicate rejection belongs to standalone.verify.
        manifest = json.loads(members["manifest.json"])
        if not isinstance(manifest, dict):
            raise ValueError()
        expected = manifest["expected"]["value"]
        provenance = manifest["provenance"]
        if not _count(expected) or provenance not in ("LOCAL_FIXTURE", "REAL_PROVIDER"):
            raise ValueError()
    except Exception:
        return _error("BUNDLE_INVALID", case_id=case_id, mode=mode, digest=digest)
    try:
        # An explicit system temp root avoids TMPDIR/environment-controlled checkout snapshots.
        with tempfile.TemporaryDirectory(prefix="counterforge-ci-", dir="/tmp") as temporary:
            owner = Path(temporary)
            root, home = owner / "snapshot", owner / "home"
            root.mkdir(mode=0o700)
            home.mkdir(mode=0o700)
            try:
                if not _no_symlinks(owner):
                    raise ValueError()
                for name, content in members.items():
                    destination = root / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(content)
                actual = {p.relative_to(root).as_posix(): p for p in root.rglob("*") if p.is_file()}
                if set(actual) != set(members) or any(
                    p.is_symlink()
                    or hashlib.sha256(p.read_bytes()).digest()
                    != hashlib.sha256(members[n]).digest()
                    for n, p in actual.items()
                ):
                    raise ValueError()
                for path in actual.values():
                    path.chmod(0o400)
                for path in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
                    path.chmod(0o500)
                root.chmod(0o500)
                code, output, stderr = _invoke_child(root, mode, fixture_count, home)
                if len(stderr) > _CHILD_BYTES:
                    raise _ChildFailure("UNKNOWN_CHILD_OUTPUT")
                return _protocol(
                    code, output, mode, expected, provenance, case_id, digest, fixture_count
                )
            finally:
                _restore(root)
    except _ChildFailure as exc:
        return _error(exc.reason, case_id=case_id, mode=mode, digest=digest)
    except Exception:
        return _error("CHILD_EXECUTION_ERROR", case_id=case_id, mode=mode, digest=digest)


class _CappedSink(io.TextIOBase):
    """Discard runner text without retaining bundle-controlled output."""

    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        pass


def _child(argv: list[str]) -> int:
    mode: Mode | None = None
    provenance: str | None = None
    expected: int | None = None
    actual: int | None = None
    verdict: Verdict = "EXECUTION_ERROR"
    reason = "CHILD_EXECUTION_ERROR"
    code = 2
    try:
        if len(argv) not in (2, 3):
            raise ValueError()
        root = Path(argv[0])
        mode = cast(Mode, argv[1])
        count = (
            int(argv[2]) if len(argv) == 3 and re.fullmatch(r"0|[1-9][0-9]{0,3}", argv[2]) else None
        )
        if (
            not _mode(mode, count)
            or (mode == "replay" and len(argv) != 2)
            or not _no_symlinks(root)
            or not root.is_dir()
        ):
            raise ValueError()
        manifest = standalone.verify(root)
        expected = manifest["expected"]["value"]
        provenance = manifest["provenance"]
        if not _count(expected) or provenance not in ("LOCAL_FIXTURE", "REAL_PROVIDER"):
            raise ValueError()
        sink = _CappedSink()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            try:
                standalone.run(root, mode, str(count) if count is not None else None)
            except AssertionError as exc:
                match = re.fullmatch(
                    r"expected=(0|[1-9][0-9]{0,3}) actual=(-?(?:0|[1-9][0-9]*)); mode=(fixture|replay) provenance=(LOCAL_FIXTURE|REAL_PROVIDER)",
                    str(exc),
                )
                if (
                    match is None
                    or int(match[1]) != expected
                    or match[3] != mode
                    or match[4] != provenance
                    or int(match[2]) == expected
                    or (mode == "fixture" and int(match[2]) != count)
                ):
                    raise ValueError() from None
                actual = int(match[2])
                verdict, code, reason = "REGRESSION", 1, "COUNT_MISMATCH"
            else:
                actual = expected
                verdict, code, reason = "PASS", 0, "OK"
    except standalone.ExecutionError:
        reason = "RUNNER_EXECUTION_ERROR"
    except BaseException:
        reason = "CHILD_EXECUTION_ERROR"
    if code == 2:
        expected = actual = None
    if mode not in ("fixture", "replay"):
        mode = None
    if provenance not in ("LOCAL_FIXTURE", "REAL_PROVIDER"):
        provenance = None
    print(
        json.dumps(
            {
                "verdict": verdict,
                "reason_code": reason,
                "expected_count": expected,
                "actual_count": actual,
                "mode": mode,
                "provenance": provenance,
            },
            separators=(",", ":"),
        )
    )
    return code


def matches_control(actual: CaseResult, expected: ExpectedOutcome) -> bool:
    observed = (
        actual.exit_code,
        actual.verdict,
        actual.reason_code,
        actual.expected_count,
        actual.actual_count,
    )
    wanted = (
        expected.exit_code,
        expected.verdict,
        expected.reason_code,
        expected.expected_count,
        expected.actual_count,
    )
    return all(type(a) is type(b) and a == b for a, b in zip(observed, wanted, strict=True))


def _load_catalog() -> tuple[CiCatalogEntry, ...]:
    if not _no_symlinks(_CATALOG) or _CATALOG.stat().st_size > _REPORT_BYTES:
        raise ValueError("CATALOG_INVALID")
    return parse_catalog(_CATALOG.read_bytes(), catalog_dir=_CATALOG.parent)


def _evaluate(entry: CiCatalogEntry) -> CaseResult:
    try:
        with entry.bundle_path.open("rb") as source:
            data = source.read(_MAX_ZIP + 1)
        if len(data) > _MAX_ZIP:
            return _error("BUNDLE_INVALID", case_id=entry.case_id, mode=entry.mode)
        if entry.variant == "flip_first_byte" and data:
            data = bytes([data[0] ^ 1]) + data[1:]
        return evaluate_bundle(
            data,
            trusted_sha256=entry.expected_sha256,
            mode=entry.mode,
            fixture_count=entry.fixture_count,
            case_id=entry.case_id,
        )
    except OSError:
        return _error("CATALOG_INVALID", case_id=entry.case_id, mode=entry.mode)


def _suite_error(reason: str, selected: tuple[str, ...] = ()) -> SuiteReport:
    return SuiteReport(1, "PIPELINE_SELF_TEST", selected, (), "EXECUTION_ERROR", 2, reason)


def run_catalog(case_ids: Sequence[str] | None = None) -> SuiteReport:
    if case_ids is not None and (
        not isinstance(case_ids, Sequence) or isinstance(case_ids, (str, bytes))
    ):
        return _suite_error("CATALOG_INVALID")
    selected = tuple(_CONTROLS) if case_ids is None else tuple(case_ids)
    if (
        not selected
        or any(not _id(i) or i not in _CONTROLS for i in selected)
        or len(set(selected)) != len(selected)
    ):
        return _suite_error("CATALOG_INVALID")
    try:
        entries = {e.case_id: e for e in _load_catalog()}
        if any(i not in entries or entries[i].expectation != _CONTROLS[i] for i in selected):
            raise ValueError()
    except (OSError, ValueError):
        return _suite_error("CATALOG_INVALID", selected)
    checks = tuple(
        SelfTestCheck(_CONTROLS[i], actual, matches_control(actual, _CONTROLS[i]))
        for i in selected
        for actual in [_evaluate(entries[i])]
    )
    if all(c.matched for c in checks):
        return SuiteReport(1, "PIPELINE_SELF_TEST", selected, checks, "PASS", 0, "OK")
    execution = any(not c.matched and c.actual.exit_code == 2 for c in checks)
    return SuiteReport(
        1,
        "PIPELINE_SELF_TEST",
        selected,
        checks,
        "EXECUTION_ERROR" if execution else "REGRESSION",
        2 if execution else 1,
        "SELF_TEST_MISMATCH",
    )


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise ValueError("INVALID_INPUT")


def _report_path(path: Path, inputs: Sequence[Path]) -> bool:
    path = path.absolute()
    if (
        path.suffix != ".json"
        or not _no_symlinks(path)
        or not path.parent.is_dir()
        or (path.exists() and not stat.S_ISREG(path.stat().st_mode))
    ):
        return False
    for source in inputs:
        if path == source.absolute() or (
            path.exists() and source.exists() and path.samefile(source)
        ):
            return False
    return True


def _emit(report: CaseResult | SuiteReport, path: Path | None, inputs: Sequence[Path]) -> int:
    if path is not None:
        try:
            safe = _report_path(path, inputs)
        except OSError:
            safe = False
        if not safe:
            report = (
                _suite_error("REPORT_PATH_UNSAFE", report.selected_ids)
                if isinstance(report, SuiteReport)
                else _error("REPORT_PATH_UNSAFE")
            )
            path = None
    payload = json.dumps(asdict(report), separators=(",", ":"), allow_nan=False).encode()
    if path is not None:
        pending: str | None = None
        try:
            if len(payload) > _REPORT_BYTES:
                raise ValueError()
            descriptor, pending = tempfile.mkstemp(
                prefix=".ci-report-", suffix=".json", dir=path.parent
            )
            with os.fdopen(descriptor, "wb") as destination:
                destination.write(payload)
            if not _report_path(path, inputs):
                raise ValueError()
            os.replace(pending, path)
        except (OSError, ValueError):
            report = (
                _suite_error("REPORT_WRITE_ERROR", report.selected_ids)
                if isinstance(report, SuiteReport)
                else _error("REPORT_WRITE_ERROR")
            )
            payload = json.dumps(asdict(report), separators=(",", ":")).encode()
        finally:
            if pending is not None:
                try:
                    Path(pending).unlink(missing_ok=True)
                except OSError:
                    pass
    print(payload.decode())
    return report.exit_code


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == "--_child":
        return _child(args[1:])
    parser = _Parser(add_help=False, allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    for command in ("case", "suite"):
        child = sub.add_parser(command, add_help=False, allow_abbrev=False)
        child.add_argument(
            "--case-id",
            action="append" if command == "suite" else "store",
            required=command == "case",
        )
        child.add_argument("--report", required=True)
    suite = bool(args and args[0] == "suite")
    try:
        parsed = parser.parse_args(args)
    except (ValueError, SystemExit):
        return _emit(_suite_error("INVALID_INPUT") if suite else _error("INVALID_INPUT"), None, ())
    inputs = [_CATALOG, _CATALOG.parent / "v1-fixture.zip"]
    try:
        entries = _load_catalog()
        inputs.extend(e.bundle_path for e in entries)
    except (OSError, ValueError):
        report: CaseResult | SuiteReport = (
            run_catalog(parsed.case_id) if suite else _error("CATALOG_INVALID")
        )
    else:
        if suite:
            report = run_catalog(parsed.case_id)
        else:
            entry = next((e for e in entries if e.case_id == parsed.case_id), None)
            report = _evaluate(entry) if entry is not None else _error("CATALOG_INVALID")
    return _emit(report, Path(parsed.report), inputs)


if __name__ == "__main__":
    raise SystemExit(main())
