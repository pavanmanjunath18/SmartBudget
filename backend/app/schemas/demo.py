"""Response bodies for the demo routes."""

from pydantic import BaseModel


class SampleDatasetRead(BaseModel):
    key: str
    title: str
    description: str
    filename: str
    row_count: int  # data rows, including the deliberately bad ones
