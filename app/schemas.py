from pydantic import BaseModel
from typing import Any, Optional

class DocumentCreateResponse(BaseModel):
    id: int
    filename: str
    blob_url: str
    status: str

class DocumentDetailResponse(DocumentCreateResponse):
    json_data: Any | None
    error_message: str | None = None
