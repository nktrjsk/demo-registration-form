"""AI-009: the internal-frontend displays 'Demo meeting form' as the title/header."""
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


EXPECTED_TITLE = "Demo meeting form"


def test_browser_tab_title(driver, internal_frontend_url):
    driver.get(internal_frontend_url)
    WebDriverWait(driver, 10).until(lambda d: d.title == EXPECTED_TITLE)
    assert driver.title == EXPECTED_TITLE


def test_h1_header(driver, internal_frontend_url):
    driver.get(internal_frontend_url)
    h1 = WebDriverWait(driver, 10).until(
        EC.visibility_of_element_located((By.TAG_NAME, "h1"))
    )
    assert h1.text == EXPECTED_TITLE
