from fastapi import FastAPI, Response
from fastapi.exceptions import RequestValidationError

from app.api import applications, gmail, reviews, settings, syncs

app = FastAPI(title="Application Tracker API")
app.include_router(applications.router)
app.include_router(gmail.router)
app.include_router(reviews.router)
app.include_router(syncs.router)
app.include_router(settings.router)
app.add_exception_handler(
    RequestValidationError, settings.request_validation_error_handler
)


@app.options("/{path:path}")
def preflight(path: str) -> Response:
    return Response(status_code=204)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
