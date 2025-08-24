import pytest

pytest_plugins = ["pytest_asyncio"]


@pytest.fixture(autouse=True)
def setup_asyncio_event_loop():
    """Set up the asyncio event loop for all tests."""
    pass
