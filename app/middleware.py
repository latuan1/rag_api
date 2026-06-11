# app/middleware.py
import os
import jwt
from jwt import PyJWTError
from fastapi import Request
from datetime import datetime, timezone
from app.config import logger
from app.errors import rag_error


async def security_middleware(request: Request, call_next):
    async def next_middleware_call():
        return await call_next(request)

    if request.url.path in {"/docs", "/openapi.json", "/health"}:
        return await next_middleware_call()

    if request.url.path.startswith("/knowledge/"):
        return await next_middleware_call()

    jwt_secret = os.getenv("JWT_SECRET")
    if not jwt_secret:
        logger.warn("JWT_SECRET not found in environment variables")
        return await next_middleware_call()

    authorization = request.headers.get("Authorization")
    if not authorization or not authorization.startswith("Bearer "):
        logger.info(
            f"Unauthorized request with missing or invalid Authorization header to: {request.url.path}"
        )
        return rag_error(
            "unauthorized",
            "Missing or invalid Authorization header",
            401,
        )

    token = authorization.split(" ")[1]
    try:
        payload = jwt.decode(token, jwt_secret, algorithms=["HS256"])
        exp_timestamp = payload.get("exp")
        if exp_timestamp and datetime.now(tz=timezone.utc) > datetime.fromtimestamp(
            exp_timestamp, tz=timezone.utc
        ):
            logger.info(
                f"Unauthorized request with expired token to: {request.url.path}"
            )
            return rag_error(
                "unauthorized",
                "Token has expired",
                401,
            )

        request.state.user = payload
        logger.debug(f"{request.url.path} - {payload}")
    except PyJWTError as e:
        logger.info(
            f"Unauthorized request with invalid token to: {request.url.path}, reason: {str(e)}"
        )
        return rag_error(
            "unauthorized",
            f"Invalid token: {str(e)}",
            401,
        )

    return await next_middleware_call()
