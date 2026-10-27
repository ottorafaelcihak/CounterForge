from counterforge_contracts.base import ContractModel
from counterforge_contracts.vtc import Limits


class Settings(ContractModel):
    """Safe defaults only. Secret loading and runtime services belong to phase 02."""

    app_env: str = "development"
    api_bind: str = "127.0.0.1"
    api_port: int = 8000
    run_meta_live_tests: bool = False
    max_concurrent_provider_calls: int = 1
    default_limits: Limits = Limits()
