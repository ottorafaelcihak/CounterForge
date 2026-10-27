from uuid import uuid4

from counterforge_contracts.responses import ErrorBody, ErrorEnvelope
from counterforge_contracts.versions import ErrorCode


class NotImplementedOperation(Exception):
    pass


def envelope(code: ErrorCode, message: str) -> ErrorEnvelope:
    return ErrorEnvelope(error=ErrorBody(code=code, message=message, correlation_id=str(uuid4())))
