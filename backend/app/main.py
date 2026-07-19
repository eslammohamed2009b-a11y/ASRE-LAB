from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.pipeline_router import router as pipeline_router
from app.module1_design.router import router as module1_router
from app.module2_simulation.router import router as module2_router
from app.module3_analysis.router import router as module3_router

app = FastAPI(
    title="ASRE-Lab Engine",
    description="Autonomous Smart Reverse Engineering Laboratory — computational backend",
    version="1.0.0",
    contact={"name": "ASRE-LAB Engineering", "email": "research@asre-lab.local"},
    license_info={"name": "Apache-2.0"},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(module1_router)
app.include_router(module2_router)
app.include_router(module3_router)
app.include_router(pipeline_router)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok", "env": settings.ENV}
