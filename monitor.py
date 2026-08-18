"""Read-only Prenot@mi DOV availability monitor with Telegram notification."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
from urllib import parse, request
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo
from datetime import datetime

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from detector import DetectionResult, RowSnapshot, detect_dov_status, normalize_text


SERVICES_URL = "https://prenotami.esteri.it/Services"
LOGIN_HOST = "iam.esteri.it"
PORTAL_HOST = "prenotami.esteri.it"


class MonitorError(RuntimeError):
    """Expected, sanitized error safe to show in a public Actions log."""


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise MonitorError(f"Missing GitHub Actions secret: {name}")
    return value


def _write_github_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")
    else:
        print(f"OUTPUT {name}={value}")


def _is_true(value: str | None) -> bool:
    return normalize_text(value or "") in {"1", "true", "yes", "on"}


def send_telegram(token: str, chat_id: str, message: str) -> None:
    """Send without logging the URL, because the bot token is part of it."""

    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = parse.urlencode(
        {
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")

    try:
        with request.urlopen(endpoint, data=payload, timeout=25) as response:
            body = json.loads(response.read().decode("utf-8"))
            if response.status != 200 or not body.get("ok"):
                raise MonitorError("Telegram rejected the notification")
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        # Do not include the original exception: it may contain the tokenized URL.
        raise MonitorError("Telegram notification failed; check token and chat ID") from None


def _chrome_options() -> Options:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1440,1000")
    options.add_argument("--lang=en-US")
    options.add_experimental_option("excludeSwitches", ["enable-logging"])

    chrome_binary = shutil.which("google-chrome") or shutil.which("chromium")
    if chrome_binary:
        options.binary_location = chrome_binary
    return options


def _new_driver() -> webdriver.Chrome:
    service = Service(log_output=os.devnull)
    driver = webdriver.Chrome(service=service, options=_chrome_options())
    driver.set_page_load_timeout(45)
    return driver


def _host(url: str) -> str:
    return (parse.urlparse(url).hostname or "").casefold()


def _visible_body_text(driver: webdriver.Chrome) -> str:
    try:
        return driver.find_element(By.TAG_NAME, "body").text
    except WebDriverException:
        return ""


def _access_challenge_state(driver: webdriver.Chrome) -> str:
    host = _host(driver.current_url)
    text = normalize_text(_visible_body_text(driver)[:12000])
    title = normalize_text(driver.title)
    combined = f"{title} {text}"

    interactive_or_hard_block_markers = (
        "verify you are human",
        "access denied",
        "unusual traffic",
    )
    if any(marker in combined for marker in interactive_or_hard_block_markers):
        return "blocked"

    automatic_validation_markers = (
        "checking your browser",
        "bot manager",
        "browser validation",
    )
    if host == "validate.perfdrive.com" or any(
        marker in combined for marker in automatic_validation_markers
    ):
        return "automatic"
    return "clear"


def _raise_if_blocked(driver: webdriver.Chrome) -> None:
    state = _access_challenge_state(driver)

    if state == "automatic":
        # Radware may briefly show a JavaScript browser-validation page. Let the
        # ordinary Chrome engine complete that first-party check. No CAPTCHA is
        # solved and no stealth/fingerprint modification is attempted.
        print("Prenot@mi is performing automatic browser validation; waiting safely.")
        try:
            WebDriverWait(driver, 35, poll_frequency=1).until(
                lambda current_driver: _access_challenge_state(current_driver) != "automatic"
            )
        except TimeoutException:
            raise MonitorError(
                "Prenot@mi automatic browser validation did not complete. "
                "The monitor will not bypass it."
            ) from None
        state = _access_challenge_state(driver)

    if state == "blocked":
        raise MonitorError(
            "Prenot@mi displayed an interactive anti-bot/access challenge. "
            "The monitor will not bypass it."
        )


def _open_login(driver: webdriver.Chrome) -> None:
    driver.get(SERVICES_URL)
    _raise_if_blocked(driver)

    if LOGIN_HOST in _host(driver.current_url):
        return

    if PORTAL_HOST not in _host(driver.current_url):
        raise MonitorError("Unexpected host while opening Prenot@mi")

    selectors = (
        "a[href*='iam.esteri.it/signin']",
        "a[href*='iam.esteri.it/login/oauth2/authorize']",
        "a[href*='LoginExternal']",
    )

    def find_login_link(current_driver: webdriver.Chrome):
        for selector in selectors:
            for element in current_driver.find_elements(By.CSS_SELECTOR, selector):
                if element.is_displayed() and element.get_attribute("href"):
                    return element
        return False

    try:
        link = WebDriverWait(driver, 20).until(find_login_link)
    except TimeoutException:
        # The user may already be authenticated and on the services page.
        if driver.find_elements(By.CSS_SELECTOR, "table tbody tr"):
            return
        raise MonitorError("Could not locate the official Prenot@mi login link") from None

    # Use the current generated OAuth/PKCE link, never the stale link from a message.
    driver.get(link.get_attribute("href"))
    _raise_if_blocked(driver)


def _login(driver: webdriver.Chrome, email: str, password: str) -> None:
    if LOGIN_HOST not in _host(driver.current_url):
        return

    wait = WebDriverWait(driver, 30)

    try:
        email_input = wait.until(
            lambda d: d.find_element(By.CSS_SELECTOR, "input[name='callback_1']")
        )
        password_input = driver.find_element(By.CSS_SELECTOR, "input[name='callback_2']")
        submit = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")

        email_input.clear()
        email_input.send_keys(email)
        password_input.clear()
        password_input.send_keys(password)
        submit.click()

        wait.until(lambda d: PORTAL_HOST in _host(d.current_url))
    except TimeoutException:
        _raise_if_blocked(driver)
        raise MonitorError(
            "Login did not complete. Change/check the password or inspect the failed Actions run."
        ) from None
    except WebDriverException:
        raise MonitorError("The IAM login form changed or could not be used") from None


def _booking_control_label(element) -> str:
    parts = [
        element.text,
        element.get_attribute("value") or "",
        element.get_attribute("aria-label") or "",
        element.get_attribute("title") or "",
    ]
    return " ".join(part.strip() for part in parts if part and part.strip())


def _is_enabled_booking_control(element) -> bool:
    if not element.is_displayed() or not element.is_enabled():
        return False
    classes = set(normalize_text(element.get_attribute("class") or "").split())
    if "disabled" in classes or element.get_attribute("disabled") is not None:
        return False
    if normalize_text(element.get_attribute("aria-disabled") or "") == "true":
        return False
    label = normalize_text(
        f"{_booking_control_label(element)} {element.get_attribute('href') or ''}"
    )
    return any(word in label for word in ("book", "booking", "prenota", "prenotazione"))


def _read_service_rows(driver: webdriver.Chrome) -> list[RowSnapshot]:
    driver.get(SERVICES_URL)
    _raise_if_blocked(driver)

    try:
        rows = WebDriverWait(driver, 25).until(
            lambda d: d.find_elements(By.CSS_SELECTOR, "table tbody tr") or False
        )
    except TimeoutException:
        _raise_if_blocked(driver)
        if LOGIN_HOST in _host(driver.current_url):
            raise MonitorError("The session returned to login before services could be read") from None
        raise MonitorError("The Prenot@mi services table did not load") from None

    snapshots: list[RowSnapshot] = []
    for row in rows:
        cells = row.find_elements(By.CSS_SELECTOR, "td")
        if len(cells) < 4:
            continue

        booking_cell = cells[3]
        controls = booking_cell.find_elements(
            By.CSS_SELECTOR,
            "a, button, input[type='button'], input[type='submit']",
        )
        enabled_controls = [item for item in controls if _is_enabled_booking_control(item)]
        control_text = " ".join(_booking_control_label(item) for item in enabled_controls)

        snapshots.append(
            RowSnapshot(
                row_text=row.text,
                booking_text=booking_cell.text,
                has_booking_control=bool(controls),
                has_enabled_booking_control=bool(enabled_controls),
                booking_control_text=control_text,
            )
        )
    return snapshots


def check_availability(email: str, password: str) -> DetectionResult:
    driver: webdriver.Chrome | None = None
    try:
        driver = _new_driver()
        _open_login(driver)
        _login(driver, email, password)
        return detect_dov_status(_read_service_rows(driver))
    except MonitorError:
        raise
    except WebDriverException:
        raise MonitorError("Chrome or the Prenot@mi page failed unexpectedly") from None
    finally:
        if driver is not None:
            try:
                driver.quit()
            except WebDriverException:
                pass


def _available_message(result: DetectionResult) -> str:
    now = datetime.now(ZoneInfo("Africa/Cairo")).strftime("%Y-%m-%d %H:%M")
    status = result.booking_text or "BOOK / PRENOTA"
    return (
        "🚨 فتح حجز DOV Only for universities على Prenot@mi!\n\n"
        f"الحالة الظاهرة: {status}\n"
        f"وقت الفحص (القاهرة): {now}\n\n"
        "افتح صفحة الخدمات واحجز بنفسك فورًا:\n"
        f"{SERVICES_URL}\n\n"
        "هذا البوت لم يضغط زر الحجز ولم ينفذ أي حجز تلقائي."
    )


def run() -> int:
    _write_github_output("available", "false")

    telegram_token = _required_env("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = _required_env("TELEGRAM_CHAT_ID")

    if _is_true(os.environ.get("TELEGRAM_TEST")):
        send_telegram(
            telegram_token,
            telegram_chat_id,
            "✅ اختبار ناجح: بوت تنبيه DOV متصل بتيليجرام. لم يتم فتح الموقع أو تنفيذ حجز.",
        )
        print("Telegram test sent successfully.")
        return 0

    email = _required_env("PRENOTAMI_EMAIL")
    password = _required_env("PRENOTAMI_PASSWORD")
    result = check_availability(email, password)

    if result.status == "available":
        send_telegram(telegram_token, telegram_chat_id, _available_message(result))
        _write_github_output("available", "true")
        print("DOV availability detected; Telegram alert sent.")
        return 0

    if result.status == "unavailable":
        print("DOV row found: booking calendar is not available.")
        return 0

    if result.status == "not_found":
        raise MonitorError(
            "The DOV-only-for-universities row was not found; the site wording/layout may have changed."
        )

    raise MonitorError(
        "The DOV row was found, but its Booking cell was unfamiliar; no alert was sent."
    )


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except MonitorError as exc:
        print(f"MONITOR ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        # Never dump a public traceback that might contain form or network details.
        print("MONITOR ERROR: unexpected safe failure; inspect code/site changes.", file=sys.stderr)
        raise SystemExit(1) from None
