from fastapi import FastAPI

from app.api import applications

app = FastAPI(title="Application Tracker API")
app.include_router(applications.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
