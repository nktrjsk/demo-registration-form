import os
import pytest
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service


INTERNAL_FRONTEND_HOST = os.environ.get(
    "INTERNAL_FRONTEND_HOST",
    "demo-meeting-form-internal-frontend-0f13-live-dev",
)
INTERNAL_FRONTEND_URL = f"http://{INTERNAL_FRONTEND_HOST}:8080/admin/"


@pytest.fixture(scope="session")
def driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,800")
    service = Service(executable_path="/usr/bin/chromedriver")
    d = webdriver.Chrome(service=service, options=options)
    try:
        yield d
    finally:
        d.quit()


@pytest.fixture
def internal_frontend_url():
    return INTERNAL_FRONTEND_URL
