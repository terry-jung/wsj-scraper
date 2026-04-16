#!/usr/bin/env python3
"""
WSJ Article Scraper & Summarizer
Uses patchright to bypass DataDome bot detection.
Logs into WSJ, searches a topic, summarizes with Claude, saves PDFs.
"""

import json
import os
import re
import sys
import time
import argparse
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from datetime import datetime, timedelta

try:
    from patchright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    print("patchright not installed. Run: pip install patchright && python -m patchright install chromium")
    sys.exit(1)

try:
    from pycookiecheat import chrome_cookies as _chrome_cookies
    HAS_PYCOOKIECHEAT = True
except ImportError:
    HAS_PYCOOKIECHEAT = False

try:
    import anthropic
except ImportError:
    print("anthropic not installed. Run: pip install anthropic")
    sys.exit(1)


# ── Config ────────────────────────────────────────────────────────────────────

OUTPUT_DIR = Path.home() / "Desktop" / "WSJ_Articles"
COOKIE_FILE = Path.home() / ".wsj_cookies.json"

PERIOD_MAP = {
    "1d":  timedelta(days=1),
    "3d":  timedelta(days=3),
    "1w":  timedelta(weeks=1),
    "2w":  timedelta(weeks=2),
    "1m":  timedelta(days=30),
    "3m":  timedelta(days=90),
    "6m":  timedelta(days=180),
    "1y":  timedelta(days=365),
    "all": None,
}

PERIOD_WSJ = {
    "1d": "1", "3d": "3", "1w": "7", "2w": "14",
    "1m": "30", "3m": "90", "6m": "180", "1y": "365", "all": "",
}

WSJ_EMAIL = os.environ["WSJ_EMAIL"]
WSJ_PASSWORD = os.environ["WSJ_PASSWORD"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
EMAIL_TO = os.environ.get("WSJ_EMAIL_TO", "thjung91@gmail.com")


# ── Helpers ───────────────────────────────────────────────────────────────────

def sanitize(text: str, max_len: int = 60) -> str:
    return re.sub(r"[^\w\s-]", "", text).strip().replace(" ", "_")[:max_len]


def collect_inputs(args) -> tuple[str, str, int]:
    topic = args.topic
    if not topic:
        if sys.stdin.isatty():
            topic = input("Search topic (e.g. 'Federal Reserve'): ").strip()
        if not topic:
            print("Topic is required. Pass it as a CLI argument for non-interactive use.")
            sys.exit(1)
    if args.period not in PERIOD_MAP:
        print(f"Invalid period '{args.period}'. Choose from: {', '.join(PERIOD_MAP)}")
        sys.exit(1)
    return topic, args.period, args.max


# ── Session management ────────────────────────────────────────────────────────

def save_session(context):
    cookies = context.cookies()
    COOKIE_FILE.write_text(json.dumps(cookies, indent=2))
    print(f"Session saved → {COOKIE_FILE}")


def load_session(context) -> bool:
    """Load cookies: prefer fresh extraction from Chrome, fall back to saved file."""
    chrome_db = Path("/Users/terryjung/Library/Application Support/Google/Chrome/Profile 2/Cookies")
    if HAS_PYCOOKIECHEAT and chrome_db.exists():
        try:
            raw = []
            for url in ["https://wsj.com", "https://accounts.dowjones.com"]:
                raw += _chrome_cookies(url, cookie_file=chrome_db, as_cookies=True)
            pw_cookies = []
            seen = set()
            for c in raw:
                if (c.host_key, c.name) not in seen and c.value:
                    seen.add((c.host_key, c.name))
                    pw_cookies.append({"name": c.name, "value": c.value, "domain": c.host_key,
                                       "path": c.path or "/", "secure": bool(c.is_secure),
                                       "httpOnly": False, "sameSite": "None"})
            if pw_cookies:
                context.add_cookies(pw_cookies)
                print(f"Loaded {len(pw_cookies)} cookies from Chrome.")
                return True
        except Exception as e:
            print(f"Chrome cookie extraction failed: {e}")

    if not COOKIE_FILE.exists():
        return False
    try:
        context.add_cookies(json.loads(COOKIE_FILE.read_text()))
        print("Loaded saved session from file.")
        return True
    except Exception as e:
        print(f"Could not load session: {e}")
        return False


def is_logged_in(page) -> bool:
    page.goto("https://www.wsj.com/", timeout=30000)
    time.sleep(3)
    try:
        page.wait_for_selector("a:has-text('Sign In'), button:has-text('Sign In')", timeout=3000)
        return False
    except PlaywrightTimeout:
        return True


def wait_for_manual_login(page):
    """
    Poll until the user has completed login by checking the URL — never
    navigates away, so it doesn't interrupt a login in progress.
    Waits up to 5 minutes.
    """
    print("\nA browser window is open. Please log in to WSJ manually.")
    print("The script will continue automatically once you're logged in.\n")
    for _ in range(60):
        time.sleep(5)
        url = page.url
        # Logged in = not on SSO / login pages
        if "sso.accounts" not in url and "login" not in url.lower() and "wsj.com" in url:
            print("Login detected!")
            return True
    print("Timed out waiting for login.")
    return False


def do_login(context, headless=False):
    """Open browser → wait for user to log in manually → save session."""
    if headless:
        print("ERROR: No valid session and cannot do manual login in headless mode.")
        print("Run once without --headless to log in and save cookies, then retry.")
        sys.exit(1)

    page = context.new_page()
    page.goto("https://www.wsj.com/", timeout=30000)
    time.sleep(2)

    # If already on WSJ (e.g. SSO redirected us somewhere) check status
    if "sso.accounts" not in page.url and "login" not in page.url.lower():
        if is_logged_in(page):
            print("Already logged in!")
            save_session(context)
            page.close()
            return

    # Navigate to login page and let the user sign in
    page.goto("https://sso.accounts.dowjones.com/login-page", timeout=30000)
    time.sleep(2)
    if wait_for_manual_login(page):
        save_session(context)
    page.close()


def ensure_logged_in(context, headless=False):
    loaded = load_session(context)
    page = context.new_page()
    if loaded and is_logged_in(page):
        page.close()
        return
    page.close()
    print("No valid session — opening browser for login…")
    do_login(context, headless=headless)


# ── Section URL mapping ───────────────────────────────────────────────────────

TOPIC_PAGES = [
    ("artificial intelligence", "https://www.wsj.com/tech/ai"),
    ("ai", "https://www.wsj.com/tech/ai"),
    ("tech", "https://www.wsj.com/tech"),
    ("technology", "https://www.wsj.com/tech"),
    ("federal reserve", "https://www.wsj.com/economy/central-banking"),
    ("economy", "https://www.wsj.com/economy"),
    ("markets", "https://www.wsj.com/finance"),
    ("stocks", "https://www.wsj.com/finance"),
    ("politics", "https://www.wsj.com/politics"),
    ("world", "https://www.wsj.com/world"),
    ("business", "https://www.wsj.com/business"),
]

SKIP_PATTERNS = [
    "market-data", "subscribe", "/account", "sitemap", "legal",
    "terms", "privacy", "contact", "help", "advertise", "partners.wsj",
    "video/", "podcast", "puzzle", "crossword",
]

def _is_article_url(href: str) -> bool:
    if not href or "wsj.com" not in href:
        return False
    if any(p in href for p in SKIP_PATTERNS):
        return False
    # Must be a content section path (not root, not nav pages)
    import re
    return bool(re.search(r'wsj\.com/(tech|business|economy|finance|politics|world|lifestyle|opinion|real-estate|health|personal-finance|sports|style|arts|cio-journal|livecoverage)/', href))


# ── Search ────────────────────────────────────────────────────────────────────

def search_articles(page, topic: str, period: str, max_articles: int) -> list[dict]:
    print(f"Searching for '{topic}' (period={period}, max={max_articles})…")

    topic_lower = topic.lower()
    section_url = next(
        (url for kw, url in TOPIC_PAGES if kw in topic_lower),
        "https://www.wsj.com/tech/ai"
    )
    # Derive the path prefix for this section so we only collect on-topic articles
    from urllib.parse import urlparse
    section_path = urlparse(section_url).path.rstrip("/")  # e.g. "/tech/ai"

    articles = []
    seen = set()

    def harvest_page(url: str, path_prefix: str):
        try:
            page.goto(url, wait_until="load", timeout=60000)
        except Exception:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                print(f"  Page load error: {e}")
                return
        time.sleep(4)
        for _ in range(8):
            for link in page.query_selector_all("a[href]"):
                href = link.get_attribute("href") or ""
                if not href.startswith("http"):
                    href = "https://www.wsj.com" + href
                base = href.split("?")[0]
                title = link.inner_text().strip()
                parsed_path = urlparse(href).path
                # Only collect articles that belong to this section
                if (base not in seen
                        and _is_article_url(href)
                        and parsed_path.startswith(path_prefix + "/")
                        and len(title) > 15):
                    seen.add(base)
                    articles.append({"url": href, "title": title})
                if len(articles) >= max_articles:
                    return
            if len(articles) >= max_articles:
                break
            page.evaluate("window.scrollBy(0, 1200)")
            time.sleep(1.5)

    print(f"  Using section: {section_url}")
    harvest_page(section_url, section_path)

    print(f"Found {len(articles)} articles.")
    return articles[:max_articles]


# ── Article extraction ────────────────────────────────────────────────────────

def extract_article(page, url: str) -> str:
    page.goto(url, timeout=30000)
    time.sleep(2)

    for sel in [
        "div.article-content",
        "section.article__body",
        "div[data-module='ArticleBody']",
        "div[class*='article-body']",
        "div.wsj-snippet-body",
        "article",
        "main",
    ]:
        el = page.query_selector(sel)
        if el:
            text = el.inner_text()
            if len(text) > 200:
                return text[:8000]

    return page.inner_text("body")[:6000]


# ── Summarize ─────────────────────────────────────────────────────────────────

def summarize(client: anthropic.Anthropic, title: str, body: str) -> str:
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{"role": "user", "content": (
            f"Article title: {title}\n\n"
            f"Article content:\n{body}\n\n"
            "Provide a concise summary (3-5 sentences) highlighting key facts, "
            "implications, and important data points."
        )}],
    )
    return message.content[0].text.strip()


# ── Email digest ─────────────────────────────────────────────────────────────

def send_digest(topic: str, period: str, results: list[dict]):
    """Send a single HTML digest email with all article summaries."""
    date_str = datetime.now().strftime("%B %d, %Y")
    subject = f"WSJ AI Digest — {topic.title()} ({period}) · {date_str}"

    rows = ""
    for i, r in enumerate(results, 1):
        clean_url = r["url"].split("?")[0]
        summary_html = r["summary"].replace("\n", "<br>")
        rows += f"""
        <tr>
          <td style="padding:24px 0;border-bottom:1px solid #e5e7eb;">
            <p style="margin:0 0 4px;font-size:12px;color:#6b7280;font-family:Arial,sans-serif;">
              {i} of {len(results)}
            </p>
            <h2 style="margin:0 0 10px;font-size:18px;font-family:Georgia,serif;color:#111827;">
              <a href="{clean_url}" style="color:#1d4ed8;text-decoration:none;">{r["title"]}</a>
            </h2>
            <p style="margin:0 0 10px;font-size:14px;line-height:1.7;color:#374151;font-family:Arial,sans-serif;">
              {summary_html}
            </p>
            <a href="{clean_url}" style="font-size:13px;color:#1d4ed8;font-family:Arial,sans-serif;text-decoration:none;">
              Read full article → {clean_url}
            </a>
          </td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f9fafb;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f9fafb;">
<tr><td align="center" style="padding:32px 16px;">
<table width="640" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1);">
  <tr>
    <td style="background:#1e3a5f;padding:24px 32px;">
      <p style="margin:0;font-size:12px;color:#93c5fd;font-family:Arial,sans-serif;text-transform:uppercase;letter-spacing:1px;">The Wall Street Journal</p>
      <h1 style="margin:6px 0 0;font-size:22px;color:#ffffff;font-family:Georgia,serif;">{topic.title()} · {period} digest</h1>
      <p style="margin:6px 0 0;font-size:12px;color:#93c5fd;font-family:Arial,sans-serif;">{date_str} · {len(results)} articles</p>
    </td>
  </tr>
  <tr>
    <td style="padding:0 32px;">
      <table width="100%" cellpadding="0" cellspacing="0">{rows}</table>
    </td>
  </tr>
  <tr>
    <td style="padding:20px 32px;background:#f3f4f6;text-align:center;">
      <p style="margin:0;font-size:11px;color:#9ca3af;font-family:Arial,sans-serif;">
        Generated by WSJ Scraper · Summaries by Claude
      </p>
    </td>
  </tr>
</table>
</td></tr>
</table>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = WSJ_EMAIL
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(WSJ_EMAIL, GMAIL_APP_PASSWORD)
        server.sendmail(WSJ_EMAIL, EMAIL_TO, msg.as_string())

    print(f"\nDigest emailed to {EMAIL_TO} — subject: {subject}")


# ── Main ──────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="WSJ Scraper & Summarizer")
    parser.add_argument("topic", nargs="?", default="", help="Search topic")
    parser.add_argument("--period", default="1w", choices=list(PERIOD_MAP.keys()))
    parser.add_argument("--max", type=int, default=5, help="Max articles to scrape")
    parser.add_argument("--relogin", action="store_true", help="Force fresh login")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode (for automation)")
    return parser.parse_args()


def main():
    args = parse_args()
    topic, period, max_articles = collect_inputs(args)
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    saved_files = []

    if args.relogin and COOKIE_FILE.exists():
        COOKIE_FILE.unlink()
        print("Cleared saved session.")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=args.headless)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            timezone_id="America/New_York",
        )

        try:
            ensure_logged_in(context, headless=args.headless)
            page = context.new_page()
            articles = search_articles(page, topic, period, max_articles)

            if not articles:
                print("No articles found. Try a different topic or time period.")
                return

            results = []
            for i, article in enumerate(articles, 1):
                print(f"\n[{i}/{len(articles)}] {article['title'][:80]}…")
                try:
                    body = extract_article(page, article["url"])
                    print("  Summarizing…")
                    summary = summarize(client, article["title"], body)
                    print(f"  {summary[:120]}…")
                    results.append({"title": article["title"], "url": article["url"], "summary": summary})
                except PlaywrightTimeout:
                    print("  Timeout — skipping.")
                except Exception as e:
                    print(f"  Error: {e} — skipping.")

        finally:
            browser.close()

    if results:
        print(f"\nSending digest of {len(results)} articles to {EMAIL_TO}…")
        send_digest(topic, period, results)
    else:
        print("No articles to send.")


if __name__ == "__main__":
    main()
