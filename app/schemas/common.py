from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    detail: str = Field(description="Human-readable error message.", examples=["Record not found"])


class HealthResponse(BaseModel):
    status: str = Field(description="Service health status.", examples=["ok"])
