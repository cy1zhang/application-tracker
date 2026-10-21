from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api import applications, gmail, reviews, settings, syncs
from app.observability import category_of, get_logger, log_event

logger = get_logger(__name__)

app = FastAPI(title="Application Tracker API")
app.include_router(applications.router)
app.include_router(gmail.router)
app.include_router(reviews.router)
app.include_router(syncs.router)
app.include_router(settings.router)
app.add_exception_handler(
    RequestValidationError, settings.request_validation_error_handler
)


@app.middleware("http")
async def contain_unhandled_errors(request: Request, call_next):
    # Without this, Starlette re-raises and the Lambda runtime prints the
    # traceback, including the exception message.
    try:
        return await call_next(request)
    except Exception as error:  # noqa: BLE001 - never let a message reach the logs
        log_event(logger, "error", "api_unhandled_error", category=category_of(error))
        return JSONResponse({"detail": "Internal error"}, status_code=500)


@app.options("/{path:path}")
def preflight(path: str) -> Response:
    return Response(status_code=204)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
