"""Declare/report existing comparison intents. This command never runs a provider."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import UUID

from services.api.app.comparison import markdown, predeclare, report
from services.api.app.config import Settings
from services.api.app.db import create_database_engine, create_uow_factory
from services.api.app.service import Service


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    declaration = commands.add_parser("declare")
    declaration.add_argument("--adaptive", type=UUID, required=True)
    declaration.add_argument("--random", type=UUID, required=True)
    declaration.add_argument("--code-revision", required=True)
    declaration.add_argument("--output", type=Path, required=True)
    reporting = commands.add_parser("report")
    reporting.add_argument("--manifest", type=Path, required=True)
    reporting.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or (
        args.command == "report" and args.output.with_suffix(".md").exists()
    ):
        parser.error("Output already exists; choose a new report path")
    settings = Settings.from_env()
    engine = create_database_engine(settings)
    try:
        factory = create_uow_factory(engine)
        with factory() as uow:
            service = Service(uow, settings)
            if args.command == "declare":
                data = predeclare(service, args.adaptive, args.random, args.code_revision)
            else:
                data = report(service, json.loads(args.manifest.read_text()))
            # Never overwrite an existing declaration/report. Keep dispatch/enqueue
            # disabled until the declaration is durably saved.
            with args.output.open("x") as output:
                output.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
            if args.command == "report":
                with args.output.with_suffix(".md").open("x") as output:
                    output.write(markdown(data))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
