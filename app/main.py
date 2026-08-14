"""FastAPI application bootstrap for the ETF Trade System."""

from fastapi import FastAPI

from app.api.endpoints import router as api_router


app = FastAPI(
    title="ETF Trade System API",
    version="0.1.0",
    description=(
        "Swagger-first API skeleton for ETF order submission, retrieval, "
        "cancellation, and quota visibility."
    ),
)

app.include_router(api_router)


@app.get("/health", tags=["System"], summary="Health check")
async def health_check() -> dict:
    return {"status": "ok"}