"""Explicit operator control for the shared pre-experiment provider allowance.

There is no default paid allowance: without this command, no pre-experiment job
can reserve a provider call. Updating limits never resets the consumed totals,
which are printed so an operator can see what has already been spent.

    python -m scripts.configure_job_allowance --show
    python -m scripts.configure_job_allowance --max-usd 0.50 --max-calls 2 \
        --expires-in-minutes 60 --actor "owner@workstation"

This command configures limits only. It never dispatches work, contacts a
provider or prints service credentials.
"""

import argparse
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy.exc import SQLAlchemyError

from services.api.app.accounting import Budget, DeploymentAllowance
from services.api.app.config import Settings
from services.api.app.db import create_database_engine, create_uow_factory
from services.api.app.errors import ServiceError


def report(allowance: DeploymentAllowance | None) -> None:
    if allowance is None:
        print("No deployment allowance is configured; pre-experiment dispatch stays blocked.")
        return
    print(
        "Deployment allowance: "
        f"max_usd={allowance.max_usd} consumed_usd={allowance.consumed_usd} "
        f"max_calls={allowance.max_calls} consumed_calls={allowance.consumed_calls} "
        f"expires_at={allowance.expires_at.isoformat()} configured_by={allowance.configured_by}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show", action="store_true", help="report the allowance and exit")
    parser.add_argument("--max-usd", help="finite dollar ceiling, e.g. 0.50")
    parser.add_argument("--max-calls", type=int, help="finite provider-call ceiling")
    parser.add_argument("--expires-in-minutes", type=int, help="finite expiry, at most 30 days")
    parser.add_argument("--actor", default="operator-cli", help="who authorized this allowance")
    args = parser.parse_args()
    required = (args.max_usd, args.max_calls, args.expires_in_minutes)
    if args.show and any(value is not None for value in required):
        parser.error("--show reports the current allowance and changes nothing")
    if not args.show and any(value is None for value in required):
        parser.error("configuring requires --max-usd, --max-calls and --expires-in-minutes")
    settings = Settings.from_env()
    engine = create_database_engine(settings)
    try:
        factory = create_uow_factory(engine)
        with factory() as uow:
            budget = Budget(uow)
            if args.show:
                report(budget.deployment_allowance())
                return
            allowance = budget.configure_deployment_allowance(
                max_usd=Decimal(args.max_usd),
                max_calls=args.max_calls,
                expires_at=datetime.now(UTC) + timedelta(minutes=args.expires_in_minutes),
                configured_by=args.actor,
            )
            uow.commit()
        report(allowance)
    except ServiceError as error:
        raise SystemExit(f"Allowance rejected: {error.message}") from None
    except InvalidOperation:
        raise SystemExit("--max-usd must be a decimal amount such as 0.50") from None
    except (OSError, ValueError, SQLAlchemyError):
        raise SystemExit(
            "Allowance configuration unavailable; check service configuration and access."
        ) from None
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
