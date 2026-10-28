"""Typed table access using PostgreSQL's migrated schema as the authority.

Reflection preserves every source column, PostgreSQL type, default, index and
constraint without maintaining a second, potentially divergent schema. Reflect
once when constructing a UOW factory, after migrations, never at import time.
"""

from dataclasses import dataclass, fields

from sqlalchemy import MetaData, Table
from sqlalchemy.engine import Connection


@dataclass(frozen=True)
class Tables:
    metadata: MetaData
    projects: Table
    artifact_assets: Table
    target_configs: Table
    seed_cases: Table
    seed_masks: Table
    visual_contracts: Table
    experiments: Table
    experiment_events: Table
    mutation_plans: Table
    mutations: Table
    mutation_validations: Table
    model_observations: Table
    findings: Table
    regression_exports: Table
    usage_events: Table
    run_operations: Table
    audit_events: Table
    background_jobs: Table
    public_asset_whitelist: Table
    deployment_allowance: Table
    seed_mask_revisions: Table
    seed_mask_acknowledgements: Table
    mask_edit_requests: Table


def load_tables(connection: Connection) -> Tables:
    metadata = MetaData()
    names = [field.name for field in fields(Tables) if field.name != "metadata"]
    metadata.reflect(bind=connection, only=names)
    return Tables(metadata=metadata, **{name: metadata.tables[name] for name in names})
