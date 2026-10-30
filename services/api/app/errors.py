import logging
from uuid import uuid4

from counterforge_contracts.responses import ErrorBody, ErrorEnvelope
from counterforge_contracts.versions import ErrorCode

logger = logging.getLogger("counterforge.api")


class NotImplementedOperation(Exception):
    pass


class ServiceError(Exception):
    def __init__(self, code: ErrorCode, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def envelope(code: ErrorCode, message: str) -> ErrorEnvelope:
    correlation_id = str(uuid4())
    # Log only the code and correlation ID: no message bodies, URLs or secrets.
    logger.info("error_response code=%s correlation_id=%s", code, correlation_id)
    return ErrorEnvelope(error=ErrorBody(code=code, message=message, correlation_id=correlation_id))
