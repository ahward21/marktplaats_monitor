# scraper.py
import requests
from bs4 import BeautifulSoup
import time
import schedule
import sqlite3
import logging
import configparser
import os
from datetime import datetime, timedelta
import re
from telegram import Bot
from telegram.constants import ParseMode
from urllib.parse import urljoin
import asyncio # Required for python-telegram-bot v20+

# --- Configuration Loading ---
config = configparser.ConfigParser()
config.read('config.ini')

# Marktplaats settings
SEARCH_URLS = config['Marktplaats']['SearchURLs'].strip().split('\n')
REQUIRED_KEYWORDS = [k.strip().lower() for k in config['Marktplaats']['RequiredKeywords'].split(',')]
EXCLUDED_KEYWORDS = [k.strip().lower() for k in config['Marktplaats']['ExcludedKeywords'].split(',')]
MAX_LISTING_AGE_DAYS = int(config['Marktplaats']['MaxListingAgeDays'])

# Telegram settings
TELEGRAM_BOT_TOKEN = config['Telegram']['BotToken']
TELEGRAM_CHAT_ID = config['Telegram']['ChatID']

# Database settings
DB_PATH = config['Database']['Path']

# Scraper settings
REQUEST_DELAY = int(config['Scraper']['RequestDelay'])
CHECK_INTERVAL_MINUTES = int(config['Scraper']['CheckIntervalMinutes'])
USER_AGENT = config['Scraper']['UserAgent']

# Logging settings
LOG_FILE = config['Logging']['LogFile']
LOG_LEVEL = config['Logging']['LogLevel']

# --- Logging Setup ---
log_dir = os.path.dirname(LOG_FILE)
if log_dir and not os.path.exists(log_dir):
    os.makedirs(log_dir)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler() # Also print logs to console
    ]
)

# --- Database Setup ---
def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        # Use URL as the unique identifier
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS listings (
                url TEXT PRIMARY KEY,
                title TEXT,
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
        logging.info("Database initialized successfully.")
    except sqlite3.Error as e:
        logging.error(f"Database error during initialization: {e}")
        raise # Re-raise the exception to stop the script if DB fails

def is_duplicate(url):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM listings WHERE url = ?", (url,))
        result = cursor.fetchone()
        conn.close()
        return result is not None
    except sqlite3.Error as e:
        logging.error(f"Database error checking duplicate for URL {url}: {e}")
        return True # Assume duplicate on error to prevent spam

def add_listing_to_db(url, title):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO listings (url, title) VALUES (?, ?)", (url, title))
        conn.commit()
        conn.close()
        logging.debug(f"Added listing to DB: {url}")
    except sqlite3.Error as e:
        logging.error(f"Database error adding URL {url}: {e}")

# --- Telegram Notification ---
async def send_telegram_notification(message):
    """Sends a message to the configured Telegram chat using async."""
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode=ParseMode.MARKDOWN)
        logging.info(f"Notification sent successfully.")
    except Exception as e:
        logging.error(f"Failed to send Telegram notification: {e}")

# Function to run async code from sync code
def run_async(coro):
     asyncio.run(coro)

# --- Date Parsing ---
def parse_relative_date(date_str):
    """Parses Marktplaats' relative date strings into datetime objects."""
    date_str = date_str.lower().strip()
    now = datetime.now()

    if 'vandaag' in date_str:
        return now.date()
    elif 'gisteren' in date_str:
        return (now - timedelta(days=1)).date()
    elif 'uur geleden' in date_str or 'min. geleden' in date_str:
         return now.date() # Treat recent posts as today
    elif 'dagen geleden' in date_str:
        try:
            days = int(re.search(r'(\d+)', date_str).group(1))
            return (now - timedelta(days=days)).date()
        except (AttributeError, ValueError):
            return None # Could not parse number of days
    else:
        # Try parsing absolute date format (e.g., '15 jan.', '2 feb. '23') - Adjust if needed!
        # This part is highly dependent on Marktplaats's exact format for older posts
        # You might need a more sophisticated date parsing library or custom logic
        # For simplicity, we'll return None if it's not clearly recent
        # Example rudimentary parsing (needs refinement):
        try:
            # Assuming format like "15 jan." or "15 jan. '24"
            parts = date_str.replace('.', '').split()
            day = int(parts[0])
            month_str = parts[1][:3] # Use first 3 letters for month abbreviation
            month_map = {'jan': 1, 'feb': 2, 'mrt': 3, 'apr': 4, 'mei': 5, 'jun': 6,
                         'jul': 7, 'aug': 8, 'sep': 9, 'okt': 10, 'nov': 11, 'dec': 12}
            month = month_map.get(month_str)
            if not month: return None

            year = now.year
            if len(parts) > 2 and parts[2].startswith("'"): # Check for year like '24
                 year_suffix = int(parts[2][1:])
                 year = 2000 + year_suffix # Assuming 21st century

            parsed_date = datetime(year, month, day)

            # Check if the parsed date seems reasonable (e.g., not in the future)
            # and handle potential year wrap-around (e.g., listing from Dec shown in Jan)
            if parsed_date.date() > now.date():
                 # If parsed date is in the future, assume it was last year
                 parsed_date = datetime(year - 1, month, day)

            return parsed_date.date()
        except Exception:
            logging.warning(f"Could not parse date string: {date_str}")
            return None # Could not parse

# --- Web Scraping and Parsing ---
def scrape_and_process():
    logging.info("Starting scraping cycle...")
    headers = {'User-Agent': USER_AGENT}
    new_listings_found_count = 0

    for url in SEARCH_URLS:
        logging.info(f"Scraping URL: {url}")
        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to fetch URL {url}: {e}")
            continue # Skip this URL and continue with the next one

        soup = BeautifulSoup(response.text, 'html.parser')

        # *** IMPORTANT: SELECTORS NEED REGULAR UPDATES ***
        # Inspect Marktplaats HTML source to find the correct selectors.
        # These selectors WILL change over time. Use browser developer tools.
        # Common patterns: Look for <article>, <div> with class="hz-Listing", etc.
        # listings = soup.find_all('div', class_='listing-container') # Example selector - ADJUST!
        listings = soup.find_all('li', class_='hz-Listing--list-item') # Try this common selector pattern

        if not listings:
             logging.warning(f"No listings found using the specified selector on {url}. Check HTML structure.")
             # Try to find potential listing containers if the primary fails
             potential_containers = soup.select('[class*="Listing"], [class*="listing"]')
             if potential_containers:
                 logging.warning(f"Found potential alternative containers: {[el.name + '.' + '.'.join(el.get('class', [])) for el in potential_containers[:5]]}")
             else:
                 logging.warning("Could not find any elements matching 'Listing' or 'listing' in class names.")
             # Consider saving the HTML for debugging:
             # with open(f"marktplaats_debug_{datetime.now():%Y%m%d_%H%M%S}.html", "w", encoding="utf-8") as f:
             #    f.write(response.text)

        logging.info(f"Found {len(listings)} potential listing elements on page.")

        for item in listings:
            try:
                # --- Extract Data ---
                # Title (usually within an <a> tag or specific heading)
                title_element = item.find('h3', class_='hz-Listing-title') # Example - ADJUST!
                title = title_element.text.strip() if title_element else 'N/A'

                # URL (often the href attribute of a link wrapping the title or image)
                url_element = item.find('a', class_='hz-Link') # Example - ADJUST!
                relative_url = url_element['href'] if url_element and url_element.has_attr('href') else None
                if relative_url:
                     # Handle potential ads or external links if needed
                    if relative_url.startswith('http'):
                        listing_url = relative_url
                    else:
                        # Construct absolute URL if it's relative
                        listing_url = urljoin('https://www.marktplaats.nl', relative_url)
                else:
                    listing_url = 'N/A'


                # Price (look for elements with class containing 'price')
                price_element = item.find('span', class_=lambda x: x and 'Price' in x) # Example - ADJUST!
                price = price_element.text.strip().replace('\xa0', ' ') if price_element else 'N/A' # \xa0 is non-breaking space

                # Description Snippet (if available)
                # desc_element = item.find('div', class_='listing-description') # Example - ADJUST!
                desc_element = item.find('span', class_=lambda x: x and 'Listing-description' in x)
                description = desc_element.text.strip() if desc_element else ''

                # Date (often in a specific div/span)
                # date_element = item.find('div', class_='listing-date') # Example - ADJUST!
                date_element = item.find('span', class_=lambda x: x and 'Listing-date' in x)
                date_str = date_element.text.strip() if date_element else ''

                # --- Basic Validation ---
                if listing_url == 'N/A' or title == 'N/A':
                    logging.warning(f"Skipping item due to missing title or URL. Check selectors.")
                    # Log the HTML of the problematic item for debugging
                    # logging.debug(f"Problematic item HTML: {item.prettify()}")
                    continue

                # --- Filtering ---
                combined_text = (title + ' ' + description).lower()

                # Keyword filtering
                if REQUIRED_KEYWORDS and not any(keyword in combined_text for keyword in REQUIRED_KEYWORDS):
                    logging.debug(f"Skipping (required keyword missing): {title}")
                    continue
                if EXCLUDED_KEYWORDS and any(keyword in combined_text for keyword in EXCLUDED_KEYWORDS):
                    logging.debug(f"Skipping (excluded keyword found): {title}")
                    continue

                # Date filtering
                listing_date = parse_relative_date(date_str)
                if listing_date:
                    age_threshold = datetime.now().date() - timedelta(days=MAX_LISTING_AGE_DAYS)
                    if listing_date < age_threshold:
                        logging.debug(f"Skipping (too old: {date_str} / {listing_date}): {title}")
                        continue # Skip old listings
                else:
                    logging.warning(f"Could not parse date '{date_str}' for listing: {title}. Processing anyway, but date filtering might be inaccurate.")
                    # Decide if you want to process items with unparseable dates or skip them


                # --- Duplicate Check ---
                if is_duplicate(listing_url):
                    logging.debug(f"Skipping (duplicate): {title}")
                    continue

                # --- Process New Listing ---
                logging.info(f"NEW Listing Found: {title} | Price: {price} | Date: {date_str}")
                new_listings_found_count += 1

                # Add to database *before* sending notification
                add_listing_to_db(listing_url, title)

                # Send notification
                message = f"*New Marktplaats Listing!*\n\n" \
                          f"*Title:* {title}\n" \
                          f"*Price:* {price}\n" \
                          f"*Date:* {date_str}\n" \
                          f"*URL:* {listing_url}"
                # Use run_async to call the async telegram function
                run_async(send_telegram_notification(message))

            except Exception as e:
                logging.error(f"Error processing an individual listing: {e}")
                logging.debug(f"Problematic item HTML snippet: {str(item)[:500]}") # Log beginning of item HTML
                continue # Skip this item and proceed with the next

        logging.info(f"Finished processing {url}. Found {new_listings_found_count} new listings in this cycle so far.")
        # Respectful delay between requests
        time.sleep(REQUEST_DELAY)

    logging.info(f"Scraping cycle finished. Total new listings found: {new_listings_found_count}")


# --- Main Execution ---
if __name__ == "__main__":
    logging.info("Script started.")
    init_db() # Initialize DB at the start

    # Run the job once immediately
    try:
        scrape_and_process()
    except Exception as e:
         logging.critical(f"Unhandled exception during initial run: {e}", exc_info=True)

    # Schedule the job
    logging.info(f"Scheduling job to run every {CHECK_INTERVAL_MINUTES} minutes.")
    schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(scrape_and_process)

    while True:
        try:
            schedule.run_pending()
            time.sleep(60) # Check every minute if a scheduled job is due
        except KeyboardInterrupt:
            logging.info("Script interrupted by user. Exiting.")
            break
        except Exception as e:
            logging.critical(f"Unhandled exception in main loop: {e}", exc_info=True)
            # Optional: Add a delay before restarting the loop after a critical error
            time.sleep(300) # Wait 5 minutes before trying again