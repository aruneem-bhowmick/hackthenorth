import uuid
from datetime import datetime

from pydantic import BaseModel


class JobCreateResponse(BaseModel):
    job_id: uuid.UUID


class JobStatusResponse(BaseModel):
    job_id: uuid.UUID
    status: str
    mode: str
    filename: str
    page_count: int | None
    created_at: datetime
    completed_at: datetime | None
    expires_at: datetime
    # P0: no findings exist yet, so counts are always zero. Real counts land P1.
    summary: dict[str, int] = {}


class DependencyStatus(BaseModel):
    status: str  # "not_configured" | "verified" | "error"
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str  # "ok" (process is up) — does not imply all dependencies are verified
    dependencies: dict[str, DependencyStatus]
