import sys
from pathlib import Path

import pytest

PLANNING_STORE = Path(__file__).resolve().parents[3] / 'app' / 'store.py'
DATA_ENV = ('SENSE_DATA_DIR', 'FLIGHT_DATA_DIR', 'FLIGHT_RECORDS_DIR', 'FLIGHT_ALGORITHM_DIR',
            'FLIGHT_STABILITY_DIR', 'LOWAIR_DATA_DIR')


@pytest.fixture(autouse=True)
def isolated_data(tmp_path_factory, monkeypatch):
    """The mounted planning store fixes its directory at import, so the environment alone cannot isolate it."""
    root = tmp_path_factory.mktemp('data')
    for name in DATA_ENV:
        monkeypatch.setenv(name, str(root / name.lower()))
    for module in list(sys.modules.values()):
        path = getattr(module, '__file__', None)
        if path and Path(path).resolve() == PLANNING_STORE:
            monkeypatch.setattr(module, 'DATA', root / 'sense_data_dir')
