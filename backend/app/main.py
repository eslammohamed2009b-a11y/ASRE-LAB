import logging
import sys

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.pipeline_router import router as pipeline_router
from app.module2_simulation.coupling_router import router as coupling_router
from app.module3_analysis.feedback_router import router as feedback_router
from app.module1_design.router import router as module1_router
from app.module1_design.jobs_router import router as module1_jobs_router
from app.module2_simulation.router import router as module2_router
from app.module2_simulation.router import simulations_router as module2_simulations_router
from app.module3_analysis.router import router as module3_router

logger = logging.getLogger("asre_lab")

app = FastAPI(
    title="ASRE-Lab Engine",
    description="Autonomous Smart Reverse Engineering Laboratory — computational backend",
    version="1.0.0",
    contact={"name": "ASRE-LAB Engineering", "email": "research@asre-lab.local"},
    license_info={
        "name": "Proprietary Source-Available License",
        "url": "https://github.com/eslammohamed2009b-a11y/ASRE-LAB/blob/main/LICENSE",
    },
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(module1_router)
app.include_router(module1_jobs_router)
app.include_router(module2_router)
app.include_router(module2_simulations_router)
app.include_router(module3_router)
app.include_router(pipeline_router)
app.include_router(coupling_router)
app.include_router(feedback_router)


@app.on_event("startup")
def validate_startup_environment() -> None:
    """Fail fast (production) or warn loudly (development) about configuration
    problems that would otherwise surface later as confusing 401/500s or a
    silently broken CORS setup."""
    problems: list[str] = []

    if not (settings.JWT_SECRET_KEY or settings.SUPABASE_JWT_SECRET):
        problems.append(
            "No JWT_SECRET_KEY or SUPABASE_JWT_SECRET is configured; every "
            "authenticated endpoint will fail with 500 on the first request."
        )
    if settings.ALLOWED_ORIGINS == ["*"]:
        problems.append(
            "ALLOWED_ORIGINS is '*' (wildcard) together with allow_credentials=True; "
            "browsers reject credentialed requests against a wildcard origin, and "
            "this is an insecure default for production. Set explicit origins."
        )
    if settings.ENV == "production" and settings.DEBUG:
        problems.append("DEBUG=True while ENV=production.")

    if not problems:
        return

    message = "Startup configuration problem(s):\n- " + "\n- ".join(problems)
    if settings.ENV == "production":
        logger.error(message)
        raise RuntimeError(message)
    logger.warning(message)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort handler: never leak a stack trace or internal exception
    text to the client. FastAPI's own HTTPException handling still takes
    precedence over this for deliberately raised HTTPExceptions."""
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok", "env": settings.ENV}


@app.get("/version", tags=["health"])
def version_info() -> dict[str, str]:
    return {
        "version": app.version,
        "env": settings.ENV,
        "python_version": sys.version.split()[0],
    }

