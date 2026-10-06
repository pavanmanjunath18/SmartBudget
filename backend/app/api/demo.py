"""Demo-only routes: sample CSV files, and resetting the shared demo account.

The sample files are public (they contain no real data). Resetting is limited to the demo
account and only works when DEMO_RESET_ENABLED is on.
"""

from datetime import date

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.organizations import to_org_read
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models import User
from app.schemas.demo import SampleDatasetRead
from app.schemas.organization import OrganizationRead
from app.services import demo_service, sample_data
from app.services.errors import ForbiddenError
from app.services.storage import FileStorage, get_storage

router = APIRouter(prefix="/demo", tags=["demo"])


@router.get("/samples", response_model=list[SampleDatasetRead])
def list_samples() -> list[SampleDatasetRead]:
    """The downloadable sample statements."""
    return [
        SampleDatasetRead(
            key=s.key,
            title=s.title,
            description=s.description,
            filename=s.filename,
            row_count=sample_data.row_count(s.key),
        )
        for s in sample_data.SAMPLES
    ]


@router.get("/samples/{key}.csv")
def download_sample(key: str) -> Response:
    """One sample statement as a CSV download, with dates in the last month."""
    sample = sample_data.get_sample(key)
    text = sample_data.render(key, date.today())
    return Response(
        content=text.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{sample.filename}"'},
    )


@router.post("/reset", response_model=OrganizationRead)
def reset_demo(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> OrganizationRead:
    """Wipe the demo account's data and rebuild it. Returns the new organization."""
    if not demo_service.is_demo_user(settings, user):
        raise ForbiddenError("Only the demo account can be reset.")
    return to_org_read(demo_service.reset_demo(db, storage, user))
