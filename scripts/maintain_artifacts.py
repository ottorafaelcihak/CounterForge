"""Explicit orphan maintenance. Defaults to dry-run; never deletes referenced evidence."""

import argparse
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import SQLAlchemyError

from services.api.app.artifacts import ArtifactStore
from services.api.app.config import Settings
from services.api.app.db import create_database_engine, create_uow_factory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delete", action="store_true", help="delete stale unreferenced files")
    parser.add_argument("--dry-run", action="store_true", help="report only (default)")
    parser.add_argument("--older-than-hours", type=int, default=24)
    args = parser.parse_args()
    if args.older_than_hours < 24 or (args.delete and args.dry_run):
        parser.error("use an age of at least 24 hours and choose either --delete or --dry-run")
    settings = Settings.from_env()
    engine = create_database_engine(settings)
    try:
        factory = create_uow_factory(engine)
        with factory() as uow:
            keys = ArtifactStore(uow, settings.artifact_root).maintain_orphans(
                datetime.now(UTC) - timedelta(hours=args.older_than_hours),
                delete=args.delete,
            )
            uow.commit()
        for key in keys:
            print(("deleted " if args.delete else "would delete ") + key)
        print(f"{'Deleted' if args.delete else 'Dry run:'} {len(keys)} stale orphan files.")
    except (OSError, ValueError, SQLAlchemyError):
        raise SystemExit(
            "Artifact maintenance unavailable; check service configuration and access."
        ) from None
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
