"""Pytest fixtures. Uses an isolated temp DB so tests never touch database.db."""
import os
import sys
import tempfile

# Make the project root importable even when pytest is launched as a bare
# console script (`pytest`), which — unlike `python -m pytest` — does not add
# the current directory to sys.path. Without this, `import db` fails on CI.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must be set BEFORE importing db.database (it reads the URL at import time).
_TMP_DB = os.path.join(tempfile.gettempdir(), "tpp_pytest.db")
os.environ["TPP_DATABASE_URL"] = f"sqlite:///{_TMP_DB}"

import pytest  # noqa: E402

from db.database import engine  # noqa: E402
from db.models import Base  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    """Give every test a clean schema."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
