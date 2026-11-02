"""Never let offline tests accidentally spend the attached user's API balance."""
import pytest

@pytest.fixture(autouse=True)
def no_live_credentials(monkeypatch):
    monkeypatch.delenv('AD_OPENAI_API_KEY',raising=False)
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
