from fastapi import FastAPI, Response

from app.api import applications, gmail, reviews, syncs

app = FastAPI(title="Application Tracker API")
app.include_router(applications.router)
app.include_router(gmail.router)
app.include_router(reviews.router)
app.include_router(syncs.router)


@app.options("/{path:path}")
def preflight(path: str) -> Response:
    return Response(status_code=204)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
