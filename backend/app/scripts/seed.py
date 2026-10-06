"""Create the demo business with about three months of realistic activity.

    python -m app.scripts.seed

Does nothing if the demo user already exists. The logic lives in app/services/demo_service.py.
"""

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.services.demo_service import seed_demo
from app.services.storage import LocalFileStorage


def main() -> None:
    configure_logging(get_settings().log_level)
    with SessionLocal() as db:
        seed_demo(db, LocalFileStorage(get_settings().upload_dir))


if __name__ == "__main__":
    main()
