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
from app.v2.router import router as v2_router
from app.v2.scientific_router import router as scientific_v2_router
from app.v2.execution_router import router as execution_v2_router
from app.v2.decision_output_router import router as decision_output_v2_router
from app.v2.account_router import router as account_v2_router
from app.study_router import router as study_router

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
app.include_router(v2_router)
app.include_router(scientific_v2_router)
app.include_router(execution_v2_router)
app.include_router(decision_output_v2_router)
app.include_router(account_v2_router)
app.include_router(study_router)


@app.on_event("startup")
def validate_startup_environment() -> None:
    """Fail fast (production) or warn loudly (development) about configuration
    problems that would otherwise surface later as confusing 401/500s or a
    silently broken CORS setup."""
    problems: list[str] = []

    production = settings.ENV.lower() == "production"

    if not (settings.JWT_SECRET_KEY or settings.SUPABASE_JWT_SECRET):
        problems.append(
            "No JWT_SECRET_KEY or SUPABASE_JWT_SECRET is configured; legacy "
            "HS256 tokens cannot be verified."
        )
    if settings.ALLOWED_ORIGINS == ["*"]:
        problems.append(
            "ALLOWED_ORIGINS is '*' (wildcard) together with allow_credentials=True; "
            "browsers reject credentialed requests against a wildcard origin, and "
            "this is an insecure default for production. Set explicit origins."
        )
    if production and settings.DEBUG:
        problems.append("DEBUG=True while ENV=production.")

    # Production must use remote durable services.  The SQLite/filesystem and
    # localhost-Redis defaults are deliberately useful for development and CI,
    # but accepting them in production would make data and artifacts depend on
    # a single application machine and leave asynchronous work undispatched.
    if production:
        if not settings.SUPABASE_URL:
            problems.append("SUPABASE_URL is required in production.")
        if not settings.SUPABASE_KEY:
            problems.append(
                "SUPABASE_KEY is required in production for durable PostgreSQL "
                "persistence and private Supabase Storage."
            )
        if settings.CELERY_BROKER_URL == "redis://localhost:6379/0":
            problems.append(
                "CELERY_BROKER_URL must point to the production Redis service, "
                "not the development localhost default."
            )
        if settings.CELERY_RESULT_BACKEND == "redis://localhost:6379/1":
            problems.append(
                "CELERY_RESULT_BACKEND must point to the production Redis service, "
                "not the development localhost default."
            )

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

