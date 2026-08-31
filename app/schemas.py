from typing import Any

from pydantic import BaseModel, Field, field_validator


class DNSRecordInput(BaseModel):
    record_type: str = Field(alias="type", min_length=1, max_length=16)
    name: str = Field(min_length=1, max_length=253)
    content: str = Field(min_length=1)
    ttl: int = Field(default=1, ge=1, le=86400)
    proxied: bool | None = None
    comment: str | None = Field(default=None, max_length=100)
    priority: int | None = Field(default=None, ge=0, le=65535)
    data: dict[str, Any] | None = None

    @field_validator("record_type")
    @classmethod
    def uppercase_type(cls, value: str) -> str:
        return value.upper()

    def cloudflare_payload(self) -> dict[str, Any]:
        payload = self.model_dump(by_alias=True, exclude_none=True)
        payload.pop("record_type", None)
        return payload
