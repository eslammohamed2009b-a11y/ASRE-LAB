from fastapi import FastAPI

app = FastAPI(title="ASRE-LAB API", version="1.0.0")


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}
