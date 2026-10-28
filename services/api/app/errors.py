from uuid import uuid4

from counterforge_contracts.responses import ErrorBody, ErrorEnvelope
from counterforge_contracts.versions import ErrorCode


class NotImplementedOperation(Exception):
    pass


class ServiceError(Exception):
    def __init__(self, code: ErrorCode, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def envelope(code: ErrorCode, message: str) -> ErrorEnvelope:
    return ErrorEnvelope(error=ErrorBody(code=code, message=message, correlation_id=str(uuid4())))
