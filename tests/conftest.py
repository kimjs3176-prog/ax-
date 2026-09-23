import pytest

from agrisea.config import Settings
from agrisea.pipeline import load_sample
from agrisea.store import Store


@pytest.fixture()
def store():
    s = Store(":memory:")
    load_sample(s)
    return s


@pytest.fixture()
def settings(tmp_path):
    return Settings(api_key="TEST", data_dir=tmp_path)
