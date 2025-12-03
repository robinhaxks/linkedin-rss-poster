from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

from dotenv import load_dotenv
import google.generativeai as genai
import feedparser
import logging
import warnings
import shutil
import time
import os
load_dotenv('.env')


# load_dotenv()

os.environ["GRPC_VERBOSITY"] = "ERROR"
os.environ["GLOG_minloglevel"] = "2"
warnings.filterwarnings("ignore")
logging.getLogger("absl").setLevel(logging.ERROR)
logging.getLogger("grpc").setLevel(logging.ERROR)



RSS_URL = "https://feeds.feedburner.com/TheHackersNews"
LAST_TITLE_FILE = "last_blog_title.txt"

EMAIL = os.getenv("LINKEDIN_EMAIL")
PASSWORD = os.getenv("LINKEDIN_PASSWORD")

GEMINI_API_KEY = os.getenv("GENEMINIAPIKEY")
if not GEMINI_API_KEY:
    raise ValueError("❌ Missing GEMINI_API_KEY in .env file!")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")




def load_last_title(file_path: str) -> str | None:
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return None


def fetch_rss_feed(url: str):
    feed = feedparser.parse(url)
    if not feed.entries:
        print("❌ No entries found or failed to fetch the feed.")
        return []
    print(f"✅ Found {len(feed.entries)} blog posts in the feed.\n")
    return feed.entries


def get_new_posts(entries, last_title: str | None):
    new_posts = []
    for entry in entries:
        title = entry.get("title", "No title")
        summary = entry.get("summary", "No summary").strip()
        link = entry.get("link", "No link")
        date = entry.get("published", "No date")

        if title == last_title:
            break

        new_posts.append({
            "title": title,
            "link": link,
            "summary": summary,
            "date": date
        })
    return new_posts


def save_last_title(file_path: str, title: str):
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(title)


def generate_linkedin_post(post):
    prompt = f"""
    Turn the following short blog summary into an engaging LinkedIn post (100–150 words) aimed at professionals and general readers.

    Requirements:
    - Start with a 1–2 sentence hook that grabs attention.
    - Use simple, clear, and human-friendly language (avoid jargon).
    - Summarize the key insight or takeaway in a relatable way.
    - Include one short CTA (e.g., comment, share, or learn more).
    - Naturally reference the link at the end as:
      🔗 {post['link']}
    - Add 10 relevant hashtags that fit the blog topic naturally.
    - Maintain a confident, positive, and professional tone.

    Title: {post['title']}
    Summary: {post['summary']}
    """

    response = model.generate_content(prompt)
    return response.text.strip()


last_title = load_last_title(LAST_TITLE_FILE)
entries = fetch_rss_feed(RSS_URL)
new_posts = get_new_posts(entries, last_title)

if not new_posts:
    print("📭 No new blog posts since last check.")
    exit()

post = new_posts[0]

response_text = generate_linkedin_post(post)
print("------------------------------------------------")
print(response_text)
print("------------------------------------------------")




print("Driver initializing...........................")

chrome_options = Options()
chrome_options.add_argument("--headless=new")        # REQUIRED FOR GITHUB ACTIONS
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--window-size=1920,1080")

# ----------------------------------------------------
# 🔥 Use the ChromeDriver installed by GitHub Actions
# ----------------------------------------------------
service = Service("/usr/bin/chromedriver")

driver = webdriver.Chrome(service=service, options=chrome_options)
wait = WebDriverWait(driver, 15)

# -----------------------------------------------------------------------------
# ✅ Login and Navigate to Feed
# -----------------------------------------------------------------------------
driver.get("https://www.linkedin.com/login")
print("🌐 Navigating to LinkedIn login...")

email_field = wait.until(EC.presence_of_element_located((By.NAME, "session_key")))
password_field = driver.find_element(By.NAME, "session_password")

email_field.send_keys(EMAIL)
password_field.send_keys(PASSWORD)
driver.find_element(By.XPATH, "//button[@type='submit']").click()
print("✅ Login submitted")

try:
    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )
    print("✅ Logged in successfully. Navigating to feed...")
    driver.get("https://www.linkedin.com/feed/")
    time.sleep(5)
except Exception as e:
    print("⚠️ Login navigation issue:", e)



try:
    start_post_btn = wait.until(
        EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Start a post')]"))
    )
    start_post_btn.click()
    print("✅ 'Start a post' button clicked successfully!")
except Exception as e:
    print("⚠️ Could not click 'Start a post' button:", e)

try:
    post_input = wait.until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, "div.ql-editor[contenteditable='true']")
        )
    )
    post_input.click()
    safe_response_text = ''.join(c for c in response_text if ord(c) <= 0xFFFF)
    post_input.send_keys(safe_response_text)
    print(safe_response_text)
    print("✅ Text typed successfully!")
    time.sleep(2)
except Exception as e:
    print("⚠️ Could not find or type in the post editor:", e)

try:
    post_button = wait.until(
        EC.element_to_be_clickable(
            (By.XPATH, "//button[contains(@class,'share-actions__primary-action') and not(@disabled)]")
        )
    )
    post_button.click()
    print("✅ Post submitted successfully!")
    save_last_title(LAST_TITLE_FILE, post["title"])

except Exception as e:
    print("⚠️ Could not click Post button:", e)

time.sleep(5)
driver.quit()
print("✅ Browser closed. Done!")
