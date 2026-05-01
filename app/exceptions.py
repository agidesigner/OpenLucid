from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        # Pass message to BaseException so str(exc) gives a clean
        # human-readable string rather than a raw tuple repr of args.
        # Matters for surfaces without our JSON exception handler:
        # MCP tools (FastMCP just calls str(exc)) and structured logs.
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status_code = status_code


class NotFoundError(AppError):
    def __init__(self, resource: str, resource_id: str):
        super().__init__(
            code="NOT_FOUND",
            message=f"{resource} with id '{resource_id}' not found",
            status_code=404,
        )


class ConflictError(AppError):
    def __init__(self, message: str):
        super().__init__(code="CONFLICT", message=message, status_code=409)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )
