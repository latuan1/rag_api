from typing import Any, Optional

from fastapi.responses import JSONResponse


def error_payload(code: str, message: str, details: Optional[dict[str, Any]] = None):
    error = {
        "code": code,
        "message": message,
    }
    if details is not None:
        error["details"] = details
    return {"error": error}


def rag_error(
    code: str,
    message: str,
    status_code: int,
    details: Optional[dict[str, Any]] = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_payload(code, message, details),
    )
