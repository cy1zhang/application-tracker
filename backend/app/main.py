from fastapi import FastAPI

app = FastAPI(title="Application Tracker API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
