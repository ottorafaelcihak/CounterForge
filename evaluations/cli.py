"""No-money by default. Live execution requires explicit files and an in-memory key."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from .audit import AuditDecision, select_sample
from .dataset import DatasetManifest, freeze, validate_dataset
from .fixture import fixture_source
from .integrity import atomic_write, canonical, seal, verify
from .protocol import LiveAuthorization, Protocol, freeze_protocol, validate_protocol
from .report import summarize, verify_bundle, write_bundle


def read(path: Path) -> Any:
    if any(
        p == ".env" or p.startswith(".env.") or p.endswith("-live") for p in path.resolve().parts
    ):
        raise ValueError("Evaluation CLI does not read secrets or live artifact directories")
    return json.loads(path.read_bytes())


def write(path: Path, value: Any) -> None:
    body = canonical(value)
    if path.exists() and path.read_bytes() != body:
        raise ValueError("Immutable artifact exists; choose a new revision path")
    atomic_write(path, body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    fixture = commands.add_parser("fixture", help="NO MONEY: invented methodology ledger only")
    fixture.add_argument("--output", type=Path, default=Path("/tmp/counterforge-phase08-fixture"))
    for name in ("freeze-dataset", "validate-dataset"):
        item = commands.add_parser(name)
        item.add_argument("manifest", type=Path)
        item.add_argument("--assets", type=Path, required=True)
        if name == "freeze-dataset":
            item.add_argument("--output", type=Path, required=True)
    declaration = commands.add_parser("freeze-protocol")
    declaration.add_argument("manifest", type=Path)
    declaration.add_argument("--output", type=Path, required=True)
    report = commands.add_parser("report")
    report.add_argument("source", type=Path)
    report.add_argument("--sample", type=Path)
    report.add_argument("--decisions", type=Path)
    report.add_argument("--output", type=Path, required=True)
    packet = commands.add_parser("audit-export")
    packet.add_argument("source", type=Path)
    packet.add_argument("--seed", type=int, required=True)
    packet.add_argument("--output", type=Path, required=True)
    packet.add_argument("--images", type=Path, help="Dir with <candidate_id>.<ext> review images")
    packet.add_argument("--packet-dir", type=Path, help="Write verified blinded image packet here")
    packet.add_argument(
        "--private-mapping", type=Path, help="Private strategy mapping path (never in packet)"
    )
    append = commands.add_parser("audit-append")
    append.add_argument("source", type=Path)
    append.add_argument("decisions", type=Path)
    append.add_argument("decision", type=Path)
    bundle = commands.add_parser("verify-bundle")
    bundle.add_argument("directory", type=Path)
    run = commands.add_parser(
        "run", help="Existing Service + worker; LIVE requires separate authorization"
    )
    run.add_argument("--dataset", type=Path, required=True)
    run.add_argument("--assets", type=Path, required=True)
    run.add_argument("--protocol", type=Path, required=True)
    run.add_argument("--bindings", type=Path, required=True)
    run.add_argument("--project-id", type=UUID, required=True)
    run.add_argument("--database-url", required=True)
    run.add_argument("--artifact-root", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--live", action="store_true")
    run.add_argument("--live-authorization", type=Path)
    run.add_argument("--key-stdin", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command in (None, "fixture"):
            root = args.output if args.command else Path("/tmp/counterforge-phase08-fixture")
            source = fixture_source()
            write_bundle(root, source, summarize(source))
            verify_bundle(root)
            print(
                f"NO MONEY / METHODOLOGY_FIXTURE — no approved dataset, provider calls or model findings. {root}"
            )
        elif args.command == "freeze-dataset":
            write(args.output, freeze(read(args.manifest), args.assets).model_dump(mode="json"))
        elif args.command == "validate-dataset":
            validate_dataset(DatasetManifest.model_validate(read(args.manifest)), args.assets)
            print("Dataset hashes, independent human evidence and 8/4 membership verified.")
        elif args.command == "freeze-protocol":
            write(args.output, freeze_protocol(read(args.manifest)).model_dump(mode="json"))
        elif args.command == "audit-export":
            if args.packet_dir:
                out_dir, pkt_dir = args.output.resolve(), args.packet_dir.resolve()
                if pkt_dir == out_dir or out_dir in pkt_dir.parents or pkt_dir in out_dir.parents:
                    raise ValueError(
                        "Packet directory must be separate from the audit output "
                        "directory holding the private selection"
                    )
            source = read(args.source)
            sample = seal(
                {
                    **select_sample(source["candidates"], args.seed),
                    "population": sorted(source["candidates"], key=lambda c: c["candidate_id"]),
                }
            )
            write(args.output / "selection.json", sample)
            write(args.output / "blinded-review.json", sample["blinded_export"])
            if args.packet_dir:
                if not args.images:
                    raise ValueError("Image packet requires --images with explicit supplied files")
                media: dict[str, Path] = {}
                for row in sample["blinded_export"]:
                    cid = row["candidate_id"]
                    matches = sorted(args.images.glob(cid + ".*"))
                    if len(matches) != 1:
                        raise ValueError(f"Expected exactly one review image for {cid}")
                    media[cid] = matches[0]
                from .packet import build_packet, verify_packet

                private = args.private_mapping or (
                    args.packet_dir.parent / "private-strategy-mapping.json"
                )
                build_packet(sample, source["candidates"], media, args.packet_dir, private)
                verify_packet(args.packet_dir)
                print(
                    f"Blinded image packet verified at {args.packet_dir}; private mapping separate."
                )
        elif args.command == "audit-append":
            source = read(args.source)
            decisions = read(args.decisions) if args.decisions.exists() else []
            decision = AuditDecision.model_validate(read(args.decision))
            if decision.sha256 != "0" * 64:
                verify(decision.model_dump(mode="json"))
            signed = seal(decision.model_dump(mode="json"))
            source["audits"] = [*decisions, signed]
            summarize(source)  # validates binding, aware time, independent append chain
            atomic_write(args.decisions, canonical(source["audits"]))
        elif args.command == "report":
            source = read(args.source)
            if args.decisions:
                source["audits"] = read(args.decisions)
            report_sample = read(args.sample) if args.sample else None
            result = summarize(source, report_sample)
            write_bundle(args.output, source, result, report_sample)
            verify_bundle(args.output)
        elif args.command == "verify-bundle":
            verify_bundle(args.directory)
            print("Bundle file hashes and report-to-source reconciliation verified.")
        elif args.command == "run":
            asyncio.run(run_existing(args))
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Evaluation blocked: {exc}\n")
    return 0


async def run_existing(args: argparse.Namespace) -> None:
    from pydantic import SecretStr
    from sqlalchemy import create_engine

    from services.api.app.config import Settings
    from services.api.app.db import create_uow_factory

    from .adapter import ExistingExperimentAdapter
    from .runner import EvaluationRun

    dataset = validate_dataset(DatasetManifest.model_validate(read(args.dataset)), args.assets)
    protocol = Protocol.model_validate(read(args.protocol))
    validate_protocol(protocol)
    if dataset.sha256 != protocol.dataset_sha256:
        raise ValueError("Frozen dataset and protocol differ")
    authorization = None
    key = ""
    if args.live:
        if not args.live_authorization or not args.key_stdin:
            raise ValueError(
                "LIVE requires --live-authorization and --key-stdin supplied by the coordinator"
            )
        authorization = LiveAuthorization.model_validate(read(args.live_authorization))
        from datetime import UTC, datetime

        authorization.check(protocol, datetime.now(UTC))
        key = sys.stdin.readline().rstrip("\n")
        if not key.strip():
            raise ValueError("No in-memory live key supplied")
    elif args.live_authorization or args.key_stdin:
        raise ValueError("Live authority/key may only be supplied with explicit --live")
    if any(p.endswith("-live") or p.startswith(".env") for p in args.artifact_root.resolve().parts):
        raise ValueError("Use a dedicated evaluation artifact root")
    settings = Settings(
        app_env="test" if not args.live else "development",
        provider_mode="LIVE" if args.live else "FIXTURE",
        allow_synthetic_fixtures=not args.live,
        run_meta_live_tests=args.live,
        model_api_key=SecretStr(key),
        artifact_root=args.artifact_root,
        enqueue_jobs=False,
    )
    # Deliberately constructed from explicit CLI input; no dotenv/env loading.
    engine = create_engine(
        args.database_url, hide_parameters=True, connect_args={"connect_timeout": 3}
    )
    try:
        adapter = ExistingExperimentAdapter(
            create_uow_factory(engine),
            settings,
            args.project_id,
            read(args.bindings),
            protocol,
            dataset=dataset,
            authorization=authorization,
        )
        run = EvaluationRun(
            args.output,
            protocol,
            [dict(case_id=e.case_id, split=e.split) for e in dataset.entries],
            adapter,
        )
        try:
            await run.run()
        finally:
            ids = [
                a["experiment_id"]
                for s in run.state["seeds"]
                for a in s["arms"]
                if a["experiment_id"]
            ]
            if ids:
                source = adapter.source(ids)
                # New report revision on every snapshot; interrupted runs retain evidence.
                destination = args.output / "snapshots" / source_hash(source)
                write_bundle(destination, source, summarize(source))
    finally:
        engine.dispose()


def source_hash(source: dict[str, Any]) -> str:
    from .integrity import digest

    return digest(source)


if __name__ == "__main__":
    raise SystemExit(main())
