import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from wpl.storage.db import Database

SAMPLE = ("The committee met on Tuesday to review the quarterly findings. Their three proposals were discussed, "
          "and a decision was deferred until the next meeting.\n\nAttendance was high, which the chair described as encouraging.")


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()
