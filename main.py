import os
import time
import shutil
import warnings
import logging

from dotenv import load_dotenv
import feedparser
import google.generativeai as genai

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ---------- config ----------
load_dotenv(".env")

RSS_URL = "https://feeds.feedburner.com/TheHackersNews"
LAST_TITLE_FILE = "last_blog_title.txt"

EMAIL = os.getenv("LINKEDIN_EMAIL")
PASSWORD = os.getenv("LINKEDIN_PASSWORD")
GEMINI_API_KEY = os.getenv("GENEMINIAPIKEY")  # as in your workflow secret

if not (EMAIL and PASSWORD and GEMINI_API_KEY):
    raise SystemExit("Missing one of LINKEDIN_EMAIL, LINKEDIN_PASSWORD or GENEMINIAPIKEY in environment.")

# configure Gemini client
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")

# logging
warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# ---------- helper functions ----------
def load_last_title(path: str) -> str | None:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return None


def save_last_title(path: str, title: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(title)


def fetch_rss_entries(url: str):
    feed = feedparser.parse(url)
    if not feed.entries:
        logging.info("No RSS entries found.")
        return []
    logging.info(f"Found {len(feed.entries)} entries.")
    return feed.entries


def get_new_posts(entries, last_title: str | None):
    new_posts = []
    for entry in entries:
        title = entry.get("title", "No title")
        summary = entry.get("summary", "")
        link = entry.get("link", "")
        if title == last_title:
            break
        new_posts.append({"title": title, "summary": summary, "link": link})
    return new_posts


def generate_linkedin_post(post):
    prompt = f"""
Turn the following short blog summary into an engaging LinkedIn post (100–150 words) aimed at professionals and general readers.

Title: {post['title']}
Summary: {post['summary']}
Link: {post['link']}
"""
    resp = model.generate_content(prompt)
    return resp.text.strip()


# ---------- Selenium helpers ----------
def build_chrome_driver():
    chrome_options = Options()

    # Important: point to the Chrome binary we installed in workflow
    chrome_options.binary_location = "/opt/google-chrome/chrome"

    # Mandatory flags for headless on GitHub Actions
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--remote-debugging-port=9222")
    chrome_options.add_argument("--disable-infobars")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-popup-blocking")
    chrome_options.add_argument("--start-maximized")
    # set a linux UA to reduce detection surprises
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")

    # Use chromedriver installed at /usr/bin/chromedriver by workflow
    service = Service("/usr/bin/chromedriver")

    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.set_page_load_timeout(60)
    return driver


def safe_click(driver, xpath_or_css, by=By.XPATH, timeout=15):
    wait = WebDriverWait(driver, timeout)
    el = wait.until(EC.element_to_be_clickable((by, xpath_or_css)))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    el.click()
    return el


def type_into_editor(driver, text):
    wait = WebDriverWait(driver, 20)
    # attempt normal send_keys first
    try:
        editor = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.ql-editor[contenteditable='true']")))
        editor.click()
        time.sleep(0.5)
        editor.send_keys(Keys.CONTROL + "a")
        editor.send_keys(text)
        logging.info("Used send_keys to type into editor.")
        return True
    except Exception as e:
        logging.warning("send_keys failed for editor: %s", e)

    # fallback: set innerHTML via JS and dispatch input events
    try:
        editor = driver.find_element(By.CSS_SELECTOR, "div.ql-editor[contenteditable='true']")
        driver.execute_script("""
            const el = arguments[0];
            el.innerText = arguments[1];
            el.dispatchEvent(new InputEvent('input', {bubbles: true}));
        """, editor, text)
        logging.info("Used JS fallback to set editor text.")
        return True
    except Exception as e:
        logging.error("JS fallback also failed: %s", e)
        return False


# ---------- Main posting sequence ----------
def post_to_linkedin(post_text):
    driver = build_chrome_driver()
    wait = WebDriverWait(driver, 20)
    try:
        logging.info("Opening LinkedIn login page...")
        driver.get("https://www.linkedin.com/login")
        # login
        wait.until(EC.presence_of_element_located((By.ID, "username"))).send_keys(EMAIL)
        driver.find_element(By.ID, "password").send_keys(PASSWORD)
        driver.find_element(By.XPATH, "//button[@type='submit']").click()
        logging.info("Logged in — waiting for feed to load...")

        # Give feed time to fully load JS assets
        time.sleep(6)
        driver.get("https://www.linkedin.com/feed/")
        time.sleep(5)

        # Try multiple selectors to open "Start a post" modal
        start_selectors = [
            (By.CSS_SELECTOR, "button.share-box-feed-entry__trigger"),
            (By.CSS_SELECTOR, "button[data-control-name='sharebox_trigger']"),
            (By.XPATH, "//button[contains(., 'Start a post')]"),
            (By.XPATH, "//button[contains(@aria-label,'Start a post')]")
        ]

        started = False
        for selector in start_selectors:
            try:
                el = WebDriverWait(driver, 8).until(EC.element_to_be_clickable(selector))
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                el.click()
                logging.info("Clicked start-post using selector: %s", selector)
                started = True
                break
            except Exception as e:
                logging.debug("Selector %s failed: %s", selector, e)

        if not started:
            raise RuntimeError("Could not open 'Start a post' modal (no selector worked).")

        time.sleep(2)

        # type into editor (with fallback)
        ok = type_into_editor(driver, post_text)
        if not ok:
            raise RuntimeError("Could not type into post editor.")

        time.sleep(1)

        # click Post button - try a couple of selectors
        post_selectors = [
            (By.XPATH, "//button[contains(@aria-label,'Post') and not(@disabled)]"),
            (By.XPATH, "//button[contains(., 'Post') and not(@disabled)]"),
            (By.CSS_SELECTOR, "button.share-actions__primary-action")
        ]

        posted = False
        for sel in post_selectors:
            try:
                btn = WebDriverWait(driver, 8).until(EC.element_to_be_clickable(sel))
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                btn.click()
                logging.info("Clicked Post button with selector: %s", sel)
                posted = True
                break
            except Exception as e:
                logging.debug("Post selector %s failed: %s", sel, e)

        if not posted:
            raise RuntimeError("Could not find/click Post button.")

        # short wait to ensure post action finishes
        time.sleep(4)
        logging.info("Post submitted successfully.")
    finally:
        driver.quit()


def main():
    entries = fetch_rss_entries(RSS_URL)
    last_title = load_last_title(LAST_TITLE_FILE)
    new_posts = get_new_posts(entries, last_title)

    if not new_posts:
        logging.info("No new posts. Exiting.")
        return

    post = new_posts[0]
    logging.info("Generating LinkedIn post for: %s", post["title"])
    post_text = generate_linkedin_post(post)
    logging.info("Generated post (preview): %s", post_text[:200])

    # attempt to post
    try:
        post_to_linkedin(post_text)
        save_last_title(LAST_TITLE_FILE, post["title"])
        logging.info("Saved last title to %s", LAST_TITLE_FILE)
    except Exception as e:
        logging.exception("Posting failed: %s", e)
        raise


if __name__ == "__main__":
    main()
