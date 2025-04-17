# scraper_gui_final_fixes_v19.py - Switch TG to HTML Parse Mode, Log fixes check
import requests
from bs4 import BeautifulSoup
import time
import sqlite3
import logging
import configparser
import os
from datetime import datetime, timedelta, date
import re
import json
import uuid
import html # Import the html module for escaping
import sqlite3
import logging
from urllib.parse import urlparse, urlunparse
from datetime import datetime
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter # To respect usage limits
import time # For potential delays

# --- External Libraries (Ensure installed via pip) ---
try:
    from telegram import Bot
    from telegram.constants import ParseMode
except ImportError:
    print("ERROR: 'python-telegram-bot' library not found. Install using: pip install python-telegram-bot")
    exit()
# --- End External Libraries ---
from urllib.parse import urljoin
import asyncio
import threading
import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk, Spinbox, Listbox, Entry, Checkbutton, BooleanVar, StringVar, END, MULTIPLE, Toplevel, HORIZONTAL, VERTICAL
import webbrowser


# --- Configuration Files ---
CONFIG_FILE = 'config.ini'
STATS_FILE = 'scan_stats.json'
QUERY_CONFIG_FILE = 'queries.json'

# --- Configuration Loading & Defaults ---
config = configparser.ConfigParser()
DEFAULT_VALUES = {
    'Telegram': {'BotToken': '', 'ChatID': ''},
    'Database': {'Path': 'marktplaats_listings.db'},
    'Scraper': {'RequestDelay': '5', 'CheckIntervalMinutes': '10', 'UserAgent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36'},
    'Logging': {'LogFile': 'logs/scraper.log', 'LogLevel': 'INFO'},
    'GUIState': {'DarkMode': 'False'}
}
DEFAULT_QUERY_SETTINGS = {
    "active": True, "min_price": None, "max_price": None, "include_non_priced": True,
    "conditions": ["nieuw", "zo goed als nieuw", "gebruikt"], "max_age_days": 7,
    "required_keywords": [], "excluded_keywords": []
}
DEFAULT_TELEGRAM_BOT_TOKEN='';DEFAULT_TELEGRAM_CHAT_ID='';DEFAULT_DB_PATH='marktplaats_listings.db';DEFAULT_REQUEST_DELAY=5;DEFAULT_CHECK_INTERVAL_MINUTES=10;DEFAULT_USER_AGENT=DEFAULT_VALUES['Scraper']['UserAgent'];DEFAULT_LOG_FILE=DEFAULT_VALUES['Logging']['LogFile'];DEFAULT_LOG_LEVEL=DEFAULT_VALUES['Logging']['LogLevel']

#-- geo load --

# Initialize Nominatim API (OpenStreetMap data)
# Provide a unique user_agent string
geolocator = Nominatim(user_agent="marktplaats_scraper_geo")
# Apply rate limiting to avoid overwhelming the free service (1 request per second)
geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)

def load_configuration():
    global config, DEFAULT_TELEGRAM_BOT_TOKEN, DEFAULT_TELEGRAM_CHAT_ID, DEFAULT_DB_PATH, DEFAULT_REQUEST_DELAY, DEFAULT_CHECK_INTERVAL_MINUTES, DEFAULT_USER_AGENT, DEFAULT_LOG_FILE, DEFAULT_LOG_LEVEL
    config = configparser.ConfigParser()
    config.read_dict({k: v for k, v in DEFAULT_VALUES.items() if k != 'GUIState'})
    config['GUIState'] = {'DarkMode': DEFAULT_VALUES['GUIState']['DarkMode']}
    if os.path.exists(CONFIG_FILE):
        try: config.read(CONFIG_FILE); print(f"Loaded general config from {CONFIG_FILE}")
        except configparser.Error as e: print(f"Warn: Error reading {CONFIG_FILE}: {e}. Using defaults.")
    else: print(f"Warn: {CONFIG_FILE} not found, using defaults.")
    DEFAULT_TELEGRAM_BOT_TOKEN = config.get('Telegram', 'BotToken', fallback='')
    DEFAULT_TELEGRAM_CHAT_ID = config.get('Telegram', 'ChatID', fallback='')
    DEFAULT_DB_PATH = config.get('Database', 'Path', fallback=DEFAULT_VALUES['Database']['Path'])
    DEFAULT_REQUEST_DELAY = config.getint('Scraper', 'RequestDelay', fallback=int(DEFAULT_VALUES['Scraper']['RequestDelay']))
    DEFAULT_CHECK_INTERVAL_MINUTES = config.getint('Scraper', 'CheckIntervalMinutes', fallback=int(DEFAULT_VALUES['Scraper']['CheckIntervalMinutes']))
    DEFAULT_USER_AGENT = config.get('Scraper', 'UserAgent', fallback=DEFAULT_VALUES['Scraper']['UserAgent'])
    DEFAULT_LOG_FILE = config.get('Logging', 'LogFile', fallback=DEFAULT_VALUES['Logging']['LogFile'])
    DEFAULT_LOG_LEVEL = config.get('Logging', 'LogLevel', fallback=DEFAULT_VALUES['Logging']['LogLevel'])

# --- Query Configuration Handling (JSON) ---
def save_queries(queries, filename=QUERY_CONFIG_FILE):
    try:
        for q in queries: q.setdefault('id', f"query_{uuid.uuid4()}")
        with open(filename, 'w', encoding='utf-8') as f: json.dump(queries, f, indent=4)
        logging.info(f"Saved {len(queries)} queries to {filename}")
    except Exception as e:
        logging.error(f"Error saving queries to {filename}: {e}")
        try: messagebox.showerror("Save Error", f"Could not save query configs:\n{e}")
        except tk.TclError: print(f"Save Error: {e}")
def load_queries(filename=QUERY_CONFIG_FILE):
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f: queries = json.load(f)
            if not isinstance(queries, list): logging.error(f"{filename} is not list."); return []
            for q in queries: q.setdefault('id', f"query_{uuid.uuid4()}")
            logging.info(f"Loaded {len(queries)} queries from {filename}")
            return queries
        except json.JSONDecodeError as e: logging.error(f"JSON Decode Error: {e}"); messagebox.showerror("Load Error", f"Invalid format in {filename}."); return []
        except Exception as e: logging.error(f"Error loading queries: {e}"); return []
    else: logging.warning(f"{filename} not found."); return []

# --- Constants for Logging Tags ---
TAG_INFO, TAG_FOUND, TAG_WARNING, TAG_ERROR, TAG_CRITICAL, TAG_DEBUG, TAG_DUPLICATE, TAG_SPONSORED = "INFO", "FOUND", "WARNING", "ERROR", "CRITICAL", "DEBUG", "DUPLICATE", "SPONSORED"

# --- Custom Logging Handler ---
class DualTextAreaHandler(logging.Handler):
    def __init__(self, debug_area, found_area):
        logging.Handler.__init__(self); self.debug_area=debug_area; self.found_area=found_area
        self.formatter=logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', '%H:%M:%S'); self.setLevel(logging.DEBUG)
    def emit(self, record):
        msg_text=record.getMessage()
        # Check for the specific "NEW:" message at INFO level for the "Found" log
        is_found = record.levelno == logging.INFO and msg_text.startswith("NEW:")
        is_duplicate="Skipping (duplicate):" in msg_text; is_sponsored="Skipping (Sponsored Ad):" in msg_text
        if is_found and hasattr(self.found_area,'winfo_exists') and self.found_area.winfo_exists():
            msg=self.formatter.format(record); tag=TAG_FOUND
            try: self.found_area.after(0, self.update_text_area, self.found_area, msg, tag)
            except tk.TclError: pass
        if hasattr(self.debug_area,'winfo_exists') and self.debug_area.winfo_exists():
            logger_level = logging.getLogger().getEffectiveLevel()
            if record.levelno >= logger_level:
                msg=self.formatter.format(record); level=record.levelname
                if is_found: tag=TAG_FOUND
                elif is_duplicate: tag=TAG_DUPLICATE
                elif is_sponsored: tag=TAG_SPONSORED
                elif level=="WARNING": tag=TAG_WARNING
                elif level=="ERROR": tag=TAG_ERROR
                elif level=="CRITICAL": tag=TAG_CRITICAL
                elif level=="DEBUG": tag=TAG_DEBUG
                else: tag=TAG_INFO
                try: self.debug_area.after(0, self.update_text_area, self.debug_area, msg, tag)
                except tk.TclError: pass
    def update_text_area(self, text_area, msg, tag):
        try:
            if not hasattr(text_area,'winfo_exists') or not text_area.winfo_exists(): return
            state=text_area.cget("state"); text_area.config(state=tk.NORMAL)
            text_area.insert(tk.END, msg+'\n', tag); text_area.see(tk.END); text_area.config(state=state)
        except tk.TclError: pass
        except Exception as e: print(f"Error updating log: {e}")

# --- Database Setup ---
    # --- Database Setup ---
def init_db():
    """Initializes the database, adding hidden and favorite columns if they don't exist."""
    db_path = DEFAULT_DB_PATH
    conn = None
    try:
        if not db_path or db_path in [".","./"]:
            db_path="marktplaats_listings.db"
            logging.warning(f"Invalid DB path specified. Using default '{db_path}'.")

        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
             try:
                 os.makedirs(db_dir)
                 logging.info(f"Created database directory: {db_dir}")
             except OSError as e:
                 logging.error(f"Failed to create database directory '{db_dir}': {e}")
                 db_path=os.path.basename(db_path) # Fallback to current dir if create fails

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Create table if it doesn't exist, including new fields with defaults
        cursor.execute('''CREATE TABLE IF NOT EXISTS listings (
                    url TEXT PRIMARY KEY,
                    title TEXT,
                    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    price TEXT,
                    image_url TEXT,
                    location TEXT,   -- Original location text
                    condition TEXT,
                    latitude REAL,  -- Latitude coordinate
                    longitude REAL, -- Longitude coordinate
                    hidden INTEGER DEFAULT 0,    -- <<< ADDED: 0 = visible, 1 = hidden
                    favorite INTEGER DEFAULT 0   -- <<< ADDED: 0 = not favorite, 1 = favorite
                  )''')
        conn.commit()

        # --- Add columns if they are missing (for upgrades from older schema) ---
        logging.debug("Checking for missing 'hidden' column...")
        cursor.execute("PRAGMA table_info(listings)")
        columns = [column[1] for column in cursor.fetchall()]

        if 'hidden' not in columns:
            logging.info("Adding 'hidden' column to listings table with default 0.")
            cursor.execute('ALTER TABLE listings ADD COLUMN hidden INTEGER DEFAULT 0')
            conn.commit()
        else:
            logging.debug("'hidden' column already exists.")

        logging.debug("Checking for missing 'favorite' column...")
        # Re-fetch column info in case the first ALTER modified the table structure
        cursor.execute("PRAGMA table_info(listings)")
        columns = [column[1] for column in cursor.fetchall()]

        if 'favorite' not in columns:
            logging.info("Adding 'favorite' column to listings table with default 0.")
            cursor.execute('ALTER TABLE listings ADD COLUMN favorite INTEGER DEFAULT 0')
            conn.commit()
        else:
             logging.debug("'favorite' column already exists.")

        logging.info(f"Database initialized/verified successfully: {os.path.abspath(db_path)}")

    except sqlite3.Error as e:
        logging.error(f"Database initialization error ({db_path}): {e}", exc_info=True)
        try:
            messagebox.showerror("Database Error", f"Database initialization failed:\n{e}\nPath: {db_path}")
        except tk.TclError: # Handle case where GUI is not available
            print(f"CRITICAL: Database initialization failed - {e}")
        raise # Re-raise the exception to signal failure
    except Exception as e:
        logging.error(f"Unexpected database initialization error ({db_path}): {e}", exc_info=True)
        try:
            messagebox.showerror("Initialization Error", f"Unexpected database setup failure:\n{e}")
        except tk.TclError:
             print(f"CRITICAL: Unexpected database setup failure - {e}")
        raise
    finally:
        if conn:
            conn.close()

def is_duplicate(url):
    try: conn=sqlite3.connect(DEFAULT_DB_PATH);cursor=conn.cursor();cursor.execute("SELECT 1 FROM listings WHERE url=?",(url,));res=cursor.fetchone();conn.close();return res is not None
    except sqlite3.Error as e: logging.error(f"DB dup check err: {e}"); return True


def add_listing_to_db(url, title, price, image_url, location, condition, latitude, longitude):
    """Adds or ignores a listing in the database, including lat/lon and default hidden/favorite status."""
    db_path_local = DEFAULT_DB_PATH
    if not db_path_local:
        logging.error("DB Path not configured. Cannot add listing.")
        return

    conn = None
    try:
        conn = sqlite3.connect(db_path_local)
        cursor = conn.cursor()
        # Insert statement now includes hidden and favorite, providing 0 as the default value
        # Existing listings (based on URL PK) will be ignored by INSERT OR IGNORE
        cursor.execute("""
            INSERT OR IGNORE INTO listings
            (url, title, price, image_url, location, condition, latitude, longitude, hidden, favorite, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (url, title, price, image_url, location, condition, latitude, longitude,
              0,  # Default value for hidden (0 = not hidden)
              0,  # Default value for favorite (0 = not favorite)
              datetime.now())) # Explicitly provide scraped_at timestamp
        conn.commit()
        logging.debug(f"DB Add/Ignore attempt: {url} (Hidden=0, Favorite=0)")
    except sqlite3.Error as e:
        logging.error(f"DB add error for {url}: {e}", exc_info=True)
    except Exception as e:
        logging.error(f"Unexpected error adding to DB for {url}: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()
# --- End of replaced add_listing_to_db function ---
    """Adds or ignores a listing in the database with the new fields."""
    # Use the global DB path set by load_configuration()
    db_path_local = DEFAULT_DB_PATH
    if not db_path_local:
        logging.error("DB Path not set, cannot add listing.")
        return
    try:
        conn = sqlite3.connect(db_path_local)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO listings
            (url, title, price, image_url, location, condition, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (url, title, price, image_url, location, condition, datetime.now())) # Add scraped_at explicitly
        conn.commit()
        conn.close()
        logging.debug(f"DB Add/Ignore: {url}")
    except sqlite3.Error as e:
        logging.error(f"DB add error for {url}: {e}")
    except Exception as e:
        logging.error(f"Unexpected error adding to DB for {url}: {e}")


# --- NEW function required by webapp.py's /database route ---
def get_all_listings_from_db(limit=None):
    """
    Fetches ALL listings from the database, including hidden ones,
    for the Flask database view page.
    Includes hidden and favorite status.
    Returns a list of dictionaries.
    """
    conn = None
    db_path_local = DEFAULT_DB_PATH
    if not db_path_local:
        logging.error("Database path not configured for get_all_listings.")
        return []

    all_listings_data = []
    try:
        conn = sqlite3.connect(db_path_local)
        conn.row_factory = sqlite3.Row # Use Row factory
        cursor = conn.cursor()

        # Base query selects all relevant columns, including hidden and favorite
        query = """
            SELECT url, title, scraped_at, price, image_url, location, condition, latitude, longitude, hidden, favorite
            FROM listings
            ORDER BY scraped_at DESC
        """
        params = []

        # Append LIMIT clause if limit is provided
        if limit is not None and isinstance(limit, int) and limit > 0:
            query += " LIMIT ?"
            params.append(limit)

        cursor.execute(query, params)
        listings = cursor.fetchall()

        # Convert Row objects to simple dictionaries
        for row in listings:
            row_dict = dict(row)
            # Ensure expected keys exist (though SELECT should guarantee them)
            row_dict.setdefault('latitude', None)
            row_dict.setdefault('longitude', None)
            row_dict.setdefault('hidden', 0)
            row_dict.setdefault('favorite', 0)
            all_listings_data.append(row_dict)

        logging.debug(f"Fetched {len(all_listings_data)} total listings (limit={limit}) for webapp database view.")
        return all_listings_data

    except sqlite3.Error as e:
        logging.error(f"Database error fetching all listings from '{db_path_local}': {e}", exc_info=True)
        return []
    except Exception as e:
        logging.error(f"Unexpected error fetching all listings: {e}", exc_info=True)
        return []
    finally:
        if conn:
            conn.close()
# --- get_listings_from_db function in scraper.py ---
def get_listings_from_db(limit=100):
    """
    Fetches recent, non-hidden listings for the Flask dashboard/map.
    Includes favorite status. Filters out hidden listings.
    Returns a list of dictionaries.
    """
    conn = None
    db_path_local = DEFAULT_DB_PATH
    if not db_path_local:
        logging.error("Database path not configured for get_listings.")
        return []

    listings_data = []
    try:
         conn = sqlite3.connect(db_path_local)
         # Use Row factory to easily access columns by name
         conn.row_factory = sqlite3.Row
         cursor = conn.cursor()
         # SELECT favorite column and FILTER WHERE hidden = 0
         cursor.execute("""
             SELECT url, title, scraped_at, price, image_url, location, condition, latitude, longitude, favorite
             FROM listings
             WHERE hidden = 0 OR hidden IS NULL  -- Ensure only non-hidden items are shown
             ORDER BY scraped_at DESC
             LIMIT ?
         """, (limit,))
         listings = cursor.fetchall()

         # Convert Row objects to simple dictionaries, ensuring all expected keys exist
         for row in listings:
             row_dict = dict(row)
             # Ensure expected keys are present, default if necessary (though SELECT should guarantee them)
             row_dict.setdefault('latitude', None)
             row_dict.setdefault('longitude', None)
             row_dict.setdefault('favorite', 0) # Default favorite status if somehow missing
             row_dict.setdefault('hidden', 0) # Although filtered, include for consistency? Or omit? Let's omit as we filter.
             if 'hidden' in row_dict: del row_dict['hidden'] # Remove hidden field as it's always 0 here
             listings_data.append(row_dict)

         logging.debug(f"Fetched {len(listings_data)} non-hidden listings for webapp main view.")
         return listings_data

    except sqlite3.Error as e:
         logging.error(f"Database error fetching listings from '{db_path_local}': {e}", exc_info=True)
         return []
    except Exception as e:
         logging.error(f"Unexpected error fetching listings: {e}", exc_info=True)
         return []
    finally:
         if conn:
             conn.close()
# --- Telegram Notification (Using HTML Parse Mode) ---
async def send_telegram_notification_async(message):
    if not DEFAULT_TELEGRAM_BOT_TOKEN or not DEFAULT_TELEGRAM_CHAT_ID: logging.warning("TG Bot Token/Chat ID missing."); return
    try:
        bot = Bot(token=DEFAULT_TELEGRAM_BOT_TOKEN)
        # --- Use HTML Parse Mode ---
        await bot.send_message(chat_id=DEFAULT_TELEGRAM_CHAT_ID, text=message, parse_mode=ParseMode.HTML, read_timeout=20, write_timeout=20)
        logging.info(f"TG notify sent.")
    except Exception as e: logging.error(f"TG send failed: {type(e).__name__} - {e}") # Log the specific error

def send_telegram_notification(message):
     # Wrapper remains the same, calls the async function
     try:
         if DEFAULT_TELEGRAM_BOT_TOKEN: asyncio.run(send_telegram_notification_async(message))
     except RuntimeError as e:
         if "cannot run event loop" in str(e):
             try: loop=asyncio.get_running_loop(); loop.create_task(send_telegram_notification_async(message))
             except RuntimeError: logging.debug("No running loop, using asyncio.run"); asyncio.run(send_telegram_notification_async(message))
             except Exception as inner_e: logging.error(f"Async task creation error: {inner_e}")
         else: logging.error(f"Async notify runtime error: {e}")
     except Exception as e: logging.error(f"Async notify wrapper error: {e}")

# --- Date Parsing & Formatting ---
def get_ordinal_suffix(day):
    if 11<=day<=13: return 'th'
    return {1:'st',2:'nd',3:'rd'}.get(day%10,'th')
def format_date_nicely(date_obj):
    if not isinstance(date_obj, (datetime, date)): return str(date_obj)
    day=date_obj.day;suffix=get_ordinal_suffix(day);day_str=str(day)
    try: return date_obj.strftime(f"{day_str}{suffix} %B %Y")
    except ValueError: return date_obj.isoformat()
def parse_relative_date(date_str):
    if not isinstance(date_str, str) or not date_str: return None
    date_str = date_str.lower().strip().replace('.', ''); now = datetime.now(); today = now.date()
    if 'vandaag' in date_str: return today
    if 'gisteren' in date_str: return today - timedelta(days=1)
    if 'uur geleden' in date_str or 'min. geleden' in date_str: return today
    if 'dagen geleden' in date_str:
        try: days_ago=int(re.search(r'(\d+)', date_str).group(1)); return today - timedelta(days=days_ago)
        except: logging.debug(f"Failed 'days ago': {date_str}"); return None
    match_absolute = re.match(r'(\d{1,2})\s+([a-z]{3})\s*(?:\'?(\d{2}))?', date_str)
    if match_absolute:
        try:
            day=int(match_absolute.group(1)); month_str=match_absolute.group(2); year_suffix=match_absolute.group(3)
            month_map={'jan':1,'feb':2,'mrt':3,'apr':4,'mei':5,'jun':6,'jul':7,'aug':8,'sep':9,'okt':10,'nov':11,'dec':12}
            month=month_map.get(month_str);
            if not month: logging.warning(f"Unknown month: {month_str}"); return None
            if year_suffix:
                year=2000 + int(year_suffix); parsed_date=date(year,month,day)
                if parsed_date > today+timedelta(days=1):
                     try: parsed_date=date(year-1,month,day); logging.debug(f"Date adj year: {date_str}")
                     except ValueError: logging.warning(f"Date adjust fail: {date_str}"); return None
                return parsed_date
            else:
                year=today.year; parsed_date=date(year,month,day)
                if parsed_date>today+timedelta(days=1) and (today.month<month or (today.month==month and today.day<day)):
                     try: parsed_date=date(year-1,month,day); logging.debug(f"Date adj year (no year): {date_str}")
                     except ValueError: logging.warning(f"Date adjust fail: {date_str}"); return None
                return parsed_date
        except ValueError: logging.warning(f"Invalid date comps: '{date_str}'"); return None
        except Exception as e: logging.warning(f"Parse date err '{date_str}': {e}"); return None
    logging.debug(f"Unparseable date: {date_str}"); return None

# --- Helper Functions for Scraping ---
def is_non_priced(price_str_raw):
    if not price_str_raw or not isinstance(price_str_raw, str): return False
    price_str_lower = price_str_raw.lower()
    return 'bied' in price_str_lower or 'n.o.t.k' in price_str_lower or 'prijs' in price_str_lower or 'zie' in price_str_lower
def parse_price(price_str_raw):
    if not isinstance(price_str_raw, str) or not price_str_raw or price_str_raw == 'N/A' or is_non_priced(price_str_raw): return None
    price_str=price_str_raw.lower().replace('€','').replace('eur','').strip().replace('.','').replace(',','.').replace(' ','')
    if not any(c.isdigit() for c in price_str): return None
    try: match=re.search(r'(\d+(\.\d+)?)', price_str); return float(match.group(1)) if match else None
    except (ValueError, AttributeError): logging.debug(f"Price conv fail: '{price_str_raw}'"); return None
def extract_condition(item_soup):
    try:
        attributes_div = item_soup.find('div', class_='hz-Listing-attributes'); condition_text = None
        cond_keywords = ['nieuw', 'gebruikt', 'z.g.a.n', 'refurbished']
        if attributes_div:
             val_span = attributes_div.find('span', class_='hz-Attribute-value'); pot_cond = ''
             if val_span: pot_cond = val_span.text.strip().lower();
             if val_span and any(kw in pot_cond for kw in cond_keywords): condition_text = pot_cond
             elif not condition_text:
                  spans = attributes_div.find_all('span', class_='hz-Attribute');
                  if spans:
                      for span in spans:
                          potential_cond = span.text.strip().lower()
                          if any(kw in potential_cond for kw in cond_keywords):
                              condition_text = potential_cond; break
        if not condition_text:
            poss_txt = ['nieuw', 'gebruikt', 'zo goed als nieuw', 'z.g.a.n', 'refurbished']
            for text in poss_txt:
                 found = item_soup.find(lambda t: t.name in ['span','div'] and text in t.get_text(strip=True).lower())
                 if found: condition_text = found.get_text(strip=True).lower(); break
        if condition_text:
            if 'zo goed als nieuw' in condition_text or 'z.g.a.n' in condition_text: return 'zo goed als nieuw'
            elif 'nieuw' in condition_text: return 'nieuw'
            elif 'refurbished' in condition_text: return 'refurbished'
            elif 'gebruikt' in condition_text: return 'gebruikt'
            else: return condition_text
        return None
    except Exception as e: logging.warning(f"Condition extract error: {e}"); return None

# --- Web Scraping and Processing ---
def scrape_and_process(app_instance, query_config):
    """Fetches listings using dynamic postcode AND distance, filters, geocodes, saves."""
    base_url = query_config.get('url')
    query_name = query_config.get('name', base_url)
    query_postcode = query_config.get('postcode') # Get the postcode for this query
    query_distance_m = query_config.get('distanceMeters') # Get the distance for this query

    if not base_url:
        logging.error(f"Query '{query_name}' is missing a base URL.")
        return 0

    # --- Construct the final URL ---
    final_url = base_url
    try:
        url_parts = urlparse(base_url)
        fragment = url_parts.fragment

        # Parse existing fragment parameters into a dictionary
        filters_dict = {}
        if fragment:
             filter_pairs = fragment.split('|')
             for pair in filter_pairs:
                 if ':' in pair:
                     key, value = pair.split(':', 1)
                     filters_dict[key] = value
                 elif pair:
                     filters_dict[pair] = True

        # Add/override/remove postcode from query config
        if query_postcode:
            postcode_val = str(query_postcode).strip()
            if postcode_val: # Only add if not empty after stripping
                filters_dict['postcode'] = postcode_val
            elif 'postcode' in filters_dict:
                 del filters_dict['postcode'] # Remove if empty string provided
        elif 'postcode' in filters_dict:
             del filters_dict['postcode'] # Remove if null/missing in config but present in base

        # Add/override/remove distance from query config
        if query_distance_m is not None:
            try:
                 distance_val = int(query_distance_m)
                 if distance_val > 0:
                     filters_dict['distanceMeters'] = distance_val
                 elif 'distanceMeters' in filters_dict:
                      del filters_dict['distanceMeters'] # Remove if distance is 0 or negative
            except (ValueError, TypeError):
                 logging.warning(f"Invalid distanceMeters value '{query_distance_m}' for query '{query_name}'. Ignoring.")
                 if 'distanceMeters' in filters_dict: del filters_dict['distanceMeters']
        elif 'distanceMeters' in filters_dict:
             del filters_dict['distanceMeters'] # Remove if null/missing in config but present in base

        # Reconstruct the fragment string (example order, adjust if needed)
        new_fragment_parts = []
        # Add specific known keys first if order matters
        for key in ['q', 'f', 'postcode', 'distanceMeters', 'sortBy', 'sortOrder']:
            if key in filters_dict:
                new_fragment_parts.append(f"{key}:{filters_dict[key]}")
        # Add any other keys
        new_fragment_parts.extend([f"{k}:{v}" for k, v in filters_dict.items() if k not in ['q', 'f', 'postcode', 'distanceMeters', 'sortBy', 'sortOrder'] and v is not True])
        # Add valueless flags if any
        new_fragment_parts.extend([k for k, v in filters_dict.items() if v is True])

        new_fragment = "|".join(map(str, new_fragment_parts))

        # Rebuild the final URL
        final_url = urlunparse((
            url_parts.scheme, url_parts.netloc, url_parts.path,
            url_parts.params, url_parts.query, new_fragment
        ))
    except Exception as url_e:
        logging.error(f"Error constructing final URL for query '{query_name}': {url_e}. Using base URL: {base_url}")
        final_url = base_url # Fallback to base URL on construction error
    # --- End URL Construction ---

    logging.info(f"Processing query '{query_name}' with final URL: {final_url}")
    headers = {'User-Agent': DEFAULT_USER_AGENT}
    new_listings = 0
    is_debug = logging.getLogger().isEnabledFor(logging.DEBUG)

    # Load NON-URL filters (price, keywords etc.)
    min_p = query_config.get('min_price'); max_p = query_config.get('max_price')
    incl_non_p = query_config.get('include_non_priced', True)
    sel_conds = query_config.get('conditions', [])
    max_age = query_config.get('max_age_days', 7)
    req_kws = [k.lower() for k in query_config.get('required_keywords', [])]
    excl_kws = [k.lower() for k in query_config.get('excluded_keywords', [])]

    session = requests.Session()
    session.headers.update(headers)
    try:
        response = session.get(final_url, timeout=30) # Use the constructed final_url
        response.raise_for_status()
        logging.debug(f"Successfully fetched URL: {final_url}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Fetch fail for '{query_name}' URL '{final_url}': {e}")
        return 0
    except Exception as e:
        logging.error(f"Fetch error for '{query_name}' URL '{final_url}': {e}")
        return 0

    if not response.text or '<html' not in response.text.lower():
        logging.warning(f"Empty/non-HTML response for '{query_name}' URL '{final_url}'"); return 0
    try:
        soup = BeautifulSoup(response.text, 'html.parser')
    except Exception as e:
        logging.error(f"Parse error for '{query_name}': {e}"); return 0

    listings = soup.find_all('li', class_='hz-Listing--list-item') or soup.find_all('article', class_=re.compile(r'\bhz-Listing\b'))
    if not listings:
        logging.warning(f"No listings items found with selectors for '{query_name}'. URL: {final_url}"); return 0
    logging.debug(f"Found {len(listings)} potential items for '{query_name}'.")

    items_processed = 0
    for item_idx, item in enumerate(listings):
        items_processed += 1
        title, listing_url, price_raw, desc, date_str = 'N/A', 'N/A', 'N/A', '', ''
        cond_txt, img_url, loc_txt = 'N/A', 'N/A', 'N/A';
        list_date, fmt_date = None, 'N/A';
        latitude, longitude = None, None

        try:
            # --- Extract Fields (Keep your extraction logic) ---
            t_el = item.find('h3', class_='hz-Listing-title'); title = t_el.text.strip() if t_el else 'N/A'
            # --- URL Extraction (Keep your logic) ---
            url_el=None; url_meth="None" # ... (rest of your URL finding logic) ...
            wrapper=item.find('div',class_='hz-Listing-item-wrapper'); cov_link=item.find('a',class_='hz-Listing-coverLink',href=True)
            if cov_link: url_el=cov_link; url_meth="Cover"
            # ... (rest of URL extraction logic) ...
            href=None
            if url_el: href=url_el.get('href')
            if url_el and href:
                 if href.startswith('http'): listing_url = href
                 elif href.startswith('/'): listing_url = urljoin('https://www.marktplaats.nl/', href.lstrip('/'))
                 else: listing_url = 'N/A'
            else: listing_url = 'N/A'
            if listing_url=='N/A': logging.warning(f"[It:{item_idx+1}] No valid URL found."); continue
            if title=='N/A' or not title: logging.warning(f"[It:{item_idx+1}] No title found."); continue
            # --- Extract Price, Date, Desc, Condition, Image, Location Text (Keep your logic) ---
            p_el=item.find('span',class_='hz-Listing-price'); price_raw=p_el.text.strip().replace('\xa0',' ') if p_el else 'N/A'
            d_el=item.find('span',class_='hz-Listing-date') or item.find(['span','div','time'], string=re.compile(r'Vandaag|Gisteren|uur|min|dagen|\d{1,2}\s+[a-z]{3}\s*(?:\'?\d{2})?', re.I))
            if d_el: date_str=d_el.text.strip(); list_date=parse_relative_date(date_str); fmt_date=format_date_nicely(list_date) if list_date else date_str
            else: date_str, fmt_date = 'N/A', 'N/A'
            desc_el=item.find('span', class_='hz-Listing-description-value') or item.find('p', class_='hz-Listing-description'); desc=desc_el.text.strip() if desc_el else ""
            cond_txt=extract_condition(item) or 'N/A'
            img_url='N/A'; img_cont=item.find('figure',class_='hz-Listing-image-container');
            if img_cont: img_tag=img_cont.find('img');
            if img_cont and img_tag: img_url=img_tag.get('src') or img_tag.get('data-src','N/A');
            if img_url and img_url.startswith('//'): img_url='https:'+img_url
            elif not isinstance(img_url, str) or not img_url.startswith('http'): img_url = 'N/A'
            loc_txt='N/A'; loc_cont=item.find('span', class_='hz-Listing-location');
            if loc_cont: loc_txt = loc_cont.get_text(strip=True, separator=' ') or 'N/A'
            if loc_txt in ['N/A', '']: seller_info = item.find('div', class_='hz-Listing--sellerInfo');
            if 'seller_info' in locals() and seller_info: loc_span=seller_info.find('span', class_='hz-Listing-location');
            if 'loc_span' in locals() and loc_span: loc_txt = loc_span.get_text(strip=True, separator=' ') or 'N/A'

            # --- Geocoding Block (Keep your geocoding logic) ---
            if loc_txt and loc_txt != 'N/A':
                clean_loc = re.sub(r'\s*\([^)]*\)\s*$', '', loc_txt).strip()
                if clean_loc:
                    logging.debug(f"Attempting to geocode: '{clean_loc}'")
                    try: location_geo = geocode(f"{clean_loc}, Netherlands", timeout=10)
                    except Exception as geo_e: logging.warning(f"Geocoding error: {geo_e}"); location_geo = None
                    if location_geo: latitude = location_geo.latitude; longitude = location_geo.longitude; logging.debug(f"Geocoded to: {latitude}, {longitude}")
                    else: logging.debug(f"Geocoding failed.")
            # --- End Geocoding Block ---

            num_p = parse_price(price_raw) # Numeric price for filtering

            # --- Filtering Logic (Non-URL based filters) ---
            # (Keep your existing logic for duplicate, sponsor, keywords, age, price range, condition)
            if is_duplicate(listing_url): logging.debug(f"[It:{item_idx+1}] Skip(Duplicate)"); continue
            # Find any span that looks like a priority label
            # Use CSS selector: find spans whose class attribute CONTAINS 'hz-Listing-priority'
            priority_spans = item.select('span[class*="hz-Listing-priority"]')
            is_unwanted_sponsor = False
            if priority_spans:
                for span in priority_spans:
                    span_text = span.get_text(strip=True)
                    if "Topadvertentie" in span_text: # Check if the text indicates the type we want to skip
                        is_unwanted_sponsor = True
                    break # Found it, no need to check other spans in this item

            if is_unwanted_sponsor:
                logging.info(f"[It:{item_idx+1}] Skip(Sponsored Ad - Found Topadvertentie Text in Priority Span)")
                continue
            comb_txt=(title+' '+desc).lower()
            if req_kws and not any(kw in comb_txt for kw in req_kws): logging.debug(f"[It:{item_idx+1}] Skip(Req KW)"); continue
            if excl_kws and any(kw in comb_txt for kw in excl_kws): logging.debug(f"[It:{item_idx+1}] Skip(Excl KW)"); continue
            if max_age is not None and list_date and list_date < (date.today() - timedelta(days=max_age)): logging.debug(f"[It:{item_idx+1}] Skip(Age)"); continue
            is_non_p = is_non_priced(price_raw); price_filter_active = min_p is not None or max_p is not None
            if price_filter_active:
                if is_non_p:
                    if not incl_non_p: logging.debug(f"[It:{item_idx+1}] Skip(NonPriced)"); continue
                elif num_p is None: logging.debug(f"[It:{item_idx+1}] Skip(UnparsePrice)"); continue
                else:
                    if min_p is not None and num_p < min_p: logging.debug(f"[It:{item_idx+1}] Skip(MinPrice)"); continue
                    if max_p is not None and num_p > max_p: logging.debug(f"[It:{item_idx+1}] Skip(MaxPrice)"); continue
            if sel_conds and cond_txt not in sel_conds: logging.debug(f"[It:{item_idx+1}] Skip(Cond)"); continue
            # --- End Filtering ---

            # --- Process Passed Listing ---
            logging.info(f"NEW: '{title[:40]}'| P:{price_raw}| L:{loc_txt}| D:{fmt_date}| C:{cond_txt}")
            new_listings += 1
            # Call updated add_listing_to_db with lat/lon
            add_listing_to_db(listing_url, title, price_raw, img_url, loc_txt, cond_txt, latitude, longitude)

            # --- Update GUI and Send Telegram (as before) ---
            list_data = { "title": title, "price": price_raw, "date": fmt_date, "condition": cond_txt, "location": loc_txt, "url": listing_url, "image_url": img_url }
            if app_instance and hasattr(app_instance, 'add_result_to_treeview'): app_instance.master.after(0, app_instance.add_result_to_treeview, list_data)
            # (Telegram formatting and sending logic...)
            tg_query = html.escape(query_name or 'N/A'); tg_title = html.escape(title or 'N/A'); tg_price = html.escape(price_raw or 'N/A')
            tg_location = html.escape(loc_txt or 'N/A'); tg_date = html.escape(fmt_date or 'N/A'); tg_condition = html.escape(cond_txt or 'N/A')
            tg_url = html.escape(listing_url or 'N/A'); html_img_url = html.escape(img_url) if img_url != 'N/A' else None
            tg_message = f"<b>Found ({tg_query})</b>\n<b>T:</b> {tg_title}\n<b>P:</b> {tg_price}\n<b>L:</b> {tg_location}\n<b>D:</b> {tg_date}\n<b>C:</b> {tg_condition}\n<b>URL:</b> <a href=\"{tg_url}\">{tg_url}</a>"
            if html_img_url: tg_message += f"\n<a href=\"{html_img_url}\">Image</a>"
            threading.Thread(target=send_telegram_notification, args=(tg_message,), daemon=True).start()

        except Exception as e:
            logging.error(f"Error Processing Item {item_idx+1} ('{title[:30]}...'): {e}", exc_info=True)
            continue

    logging.info(f"Finished processing {items_processed} items for query '{query_name}'. Found {new_listings} new.")
    return new_listings
# --- End of replaced scrape_and_process function ---

# --- GUI Application Class ---
class ScraperApp:

    def __init__(self, master):
        self.master = master
        master.title("Marktplaats Scraper Enhanced - Query Manager")
        master.geometry("1150x800")

        self.running = False; self.scraper_thread = None
        load_configuration()
        self.queries = load_queries()
        self.trigger_file_path = "scan_trigger.now"
        self.status_file_path = "scan_status.txt"
        self.check_interval_ms = 5000

        self.update_status_file("idle")

        self.current_check_interval = DEFAULT_CHECK_INTERVAL_MINUTES
        try: self.current_check_interval = config.getint('Scraper', 'CheckIntervalMinutes', fallback=DEFAULT_CHECK_INTERVAL_MINUTES)
        except Exception as e: logging.warning(f"Using default interval due to config error: {e}")

        # Tkinter Variables for Editor
        self.dark_mode_var = tk.BooleanVar()
        self.interval_var = tk.IntVar(value=self.current_check_interval)
        self.edit_query_id = None
        self.edit_name_var = tk.StringVar()
        self.edit_url_var = tk.StringVar()
        self.edit_postcode_var = tk.StringVar()
        self.edit_distance_var = tk.StringVar() # Added
        self.edit_active_var = tk.BooleanVar()
        self.edit_min_price_var = tk.StringVar()
        self.edit_max_price_var = tk.StringVar()
        self.edit_include_non_priced_var = tk.BooleanVar()
        self.edit_max_age_var = tk.IntVar()
        self.edit_condition_vars = {"nieuw": tk.BooleanVar(), "zo goed als nieuw": tk.BooleanVar(), "gebruikt": tk.BooleanVar()}

        # UI Setup
        self.style = ttk.Style(master); self.setup_styles()
        dm_enabled = config.getboolean('GUIState', 'DarkMode', fallback=False); self.dark_mode_var.set(dm_enabled)

        self.notebook = ttk.Notebook(master, padding=10); self.notebook.pack(pady=10, padx=10, fill=tk.BOTH, expand=True)
        self.tab_run_log = ttk.Frame(self.notebook, padding="10", style='TFrame'); self.notebook.add(self.tab_run_log, text='Run & Logs')
        self.tab_results = ttk.Frame(self.notebook, padding="10", style='TFrame'); self.notebook.add(self.tab_results, text='Results')
        self.tab_query_management = ttk.Frame(self.notebook, padding="10", style='TFrame'); self.notebook.add(self.tab_query_management, text='Query Management')

        self.create_run_log_tab(); self.create_results_tab(); self.create_query_management_tab()

        self.setup_logging()
        self.apply_theme()

        master.after(self.check_interval_ms, self.check_for_trigger_file)
        master.protocol("WM_DELETE_WINDOW", self.on_closing)
        logging.info(f"GUI Initialized. Loaded {len(self.queries)} queries.")

    # --- Styling Methods ---
    def setup_styles(self):
        self.style = ttk.Style() # Initialize without master argument
        theme = 'vista' if os.name == 'nt' else 'clam';
        try: self.style.theme_use(theme)
        except tk.TclError: self.style.theme_use('default')
        self.style.configure('.', padding=(5, 5), font=('Segoe UI', 9))
        self.style.configure('TFrame', background='SystemButtonFace')
        self.style.configure('TButton', padding=(8, 5), font=('Segoe UI', 9))
        self.style.configure('TNotebook.Tab', padding=(10, 5), font=('Segoe UI', 10, 'bold'))
        self.style.configure('TLabelframe', padding=(5, 5))
        self.style.configure('TLabelframe.Label', padding=(5, 2), font=('Segoe UI', 9, 'bold'))
        self.style.configure('Treeview.Heading', font=('Segoe UI', 10, 'bold'))
        self.style.configure("Treeview", rowheight=25)
        self.style.map("Treeview", background=[('selected', '#0078D7')], foreground=[('selected', 'white')])

    def apply_theme(self):
        mode = 'dark' if self.dark_mode_var.get() else 'light'; logging.debug(f"Applying {mode} theme.")
        colors={'dark':{'bg':'#2E2E2E','fg':'#EAEAEA','entry_bg':'#3C3C3C','entry_fg':'#EAEAEA','button_bg':'#4A4A4A','button_fg':'#EAEAEA','button_active':'#5F5F5F','notebook_bg':'#2E2E2E','tab_bg':'#4A4A4A','tab_fg':'#EAEAEA','tab_active_bg':'#5F5F5F','tab_inactive_bg':'#3C3C3C','tree_bg':'#2E2E2E','tree_fg':'#EAEAEA','tree_heading_bg':'#4A4A4A','tree_selected':'#005A9E','text_bg':'#1E1E1E','text_fg':'#DCDCDC','text_insert':'#EAEAEA','label_fg':'#EAEAEA','labelframe_fg':'#CCCCCC','frame_bg':'#2E2E2E','listbox_bg':'#3C3C3C','listbox_fg':'#EAEAEA','listbox_select_bg':'#005A9E','listbox_select_fg':'white','log_info':'#EAEAEA','log_debug':'#AAAAAA','log_found':'#77DD77','log_dup':'#C8A2C8','log_warn':'#FFB347','log_err':'#FF6961','log_crit':'#FF6961','log_sponsor':'#ADD8E6'},
        'light':{'bg':'SystemButtonFace','fg':'SystemWindowText','entry_bg':'SystemWindow','entry_fg':'SystemWindowText','button_bg':'SystemButtonFace','button_fg':'SystemWindowText','button_active':'SystemButtonFace','notebook_bg':'SystemButtonFace','tab_bg':'SystemButtonFace','tab_fg':'SystemWindowText','tab_active_bg':'SystemHighlight','tab_inactive_bg':'SystemButtonFace','tree_bg':'SystemWindow','tree_fg':'SystemWindowText','tree_heading_bg':'SystemButtonFace','tree_selected':'SystemHighlight','text_bg':'SystemWindow','text_fg':'SystemWindowText','text_insert':'SystemWindowText','label_fg':'SystemWindowText','labelframe_fg':'SystemWindowText','frame_bg':'SystemButtonFace','listbox_bg':'SystemWindow','listbox_fg':'SystemWindowText','listbox_select_bg':'SystemHighlight','listbox_select_fg':'SystemHighlightText','log_info':'black','log_debug':'grey','log_found':'green','log_dup':'purple','log_warn':'orange','log_err':'red','log_crit':'red','log_sponsor':'blue'}}
        c = colors[mode]
        try:
            base_theme = 'clam';
            if mode == 'light': base_theme = 'vista' if os.name == 'nt' else 'clam'
            self.style.theme_use(base_theme)
            self.style.configure('.', background=c['bg'], foreground=c['fg'], fieldbackground=c['entry_bg'], insertcolor=c['text_insert'])
            self.style.configure('TFrame', background=c['frame_bg'])
            self.style.configure('TButton', background=c['button_bg'], foreground=c['button_fg'])
            self.style.map('TButton', background=[('active', c['button_active'])])
            self.style.configure('TEntry', fieldbackground=c['entry_bg'], foreground=c['entry_fg'], insertcolor=c['text_insert'])
            self.style.configure('TSpinbox', fieldbackground=c['entry_bg'], foreground=c['entry_fg'], insertcolor=c['text_insert'], background=c['bg'], arrowcolor=c['fg'])
            self.style.configure('TCheckbutton', background=c['bg'], foreground=c['label_fg'])
            self.style.map('TCheckbutton', indicatorbackground=[('selected', c['bg']), ('!selected', c['bg'])], indicatorforeground=[('selected', c['fg']), ('!selected', c['fg'])], background=[('active', c['bg'])])
            self.style.configure('TLabel', background=c['bg'], foreground=c['label_fg'])
            self.style.configure('TNotebook', background=c['notebook_bg'])
            self.style.configure('TNotebook.Tab', background=c['tab_inactive_bg'], foreground=c['tab_fg'])
            self.style.map('TNotebook.Tab', background=[('selected', c['tab_active_bg'])], foreground=[('selected', c['fg'])])
            self.style.configure('TLabelframe', background=c['bg'], bordercolor=c['fg'])
            self.style.configure('TLabelframe.Label', background=c['bg'], foreground=c['labelframe_fg'])
            for tree_attr in ['query_tree', 'results_tree']:
                 if hasattr(self, tree_attr) and getattr(self, tree_attr):
                      tree = getattr(self, tree_attr); style_name = tree.cget("style") or f"{tree_attr}.Treeview"
                      self.style.configure(style_name, background=c['tree_bg'], fieldbackground=c['tree_bg'], foreground=c['tree_fg'])
                      self.style.configure(f"{style_name}.Heading", background=c['tree_heading_bg'], foreground=c['fg'])
                      self.style.map(style_name, background=[('selected', c['tree_selected'])], foreground=[('selected', c['listbox_select_fg'])])
            # --- Apply Log Tag Colors AND Fonts ---
            for text_attr in ['debug_log_area', 'found_log_area']:
                 if hasattr(self, text_attr) and getattr(self, text_attr):
                     widget = getattr(self, text_attr); widget.config(background=c['text_bg'], foreground=c['text_fg'], insertbackground=c['text_insert'])
                     # Clear previous font tags before applying color/font
                     widget.tag_config(TAG_INFO, foreground=c['log_info'], font=("Consolas", 9))
                     widget.tag_config(TAG_DEBUG, foreground=c['log_debug'], font=("Consolas", 9))
                     widget.tag_config(TAG_FOUND, foreground=c['log_found'], font=("Consolas", 9, "bold"))
                     widget.tag_config(TAG_DUPLICATE, foreground=c['log_dup'], font=("Consolas", 9))
                     widget.tag_config(TAG_SPONSORED, foreground=c['log_sponsor'], font=("Consolas", 9))
                     widget.tag_config(TAG_WARNING, foreground=c['log_warn'], font=("Consolas", 9))
                     widget.tag_config(TAG_ERROR, foreground=c['log_err'], font=("Consolas", 9))
                     widget.tag_config(TAG_CRITICAL, foreground=c['log_crit'], font=("Consolas", 9, "bold"))
            # Style editor text boxes
            for text_attr in ['edit_req_keywords_text', 'edit_excl_keywords_text']:
                 if hasattr(self, text_attr) and getattr(self, text_attr):
                      getattr(self, text_attr).config(background=c['text_bg'], foreground=c['text_fg'], insertbackground=c['text_insert'])
            # --- End Log Color/Font ---
            if hasattr(self, 'interval_spinbox'):
                try: self.interval_spinbox.config(background=c['entry_bg'], foreground=c['entry_fg']) # Removed buttonbackground
                except tk.TclError as e: logging.warning(f"Style error interval_spinbox: {e}")
            if hasattr(self, 'edit_max_age_spinbox'):
                 try: self.edit_max_age_spinbox.config(background=c['entry_bg'], foreground=c['entry_fg']) # Removed buttonbackground
                 except tk.TclError as e: logging.warning(f"Style error max_age_spinbox: {e}")
            self.master.config(bg=c['bg'])
            if hasattr(self, 'tab_run_log'): self.tab_run_log.configure(style='TFrame')
            if hasattr(self, 'tab_results'): self.tab_results.configure(style='TFrame')
            if hasattr(self, 'tab_query_management'): self.tab_query_management.configure(style='TFrame')
        except tk.TclError as e: logging.error(f"Theme TclError: {e}")
        except Exception as e: logging.error(f"Theme error: {e}")

    def toggle_theme(self):
        self.apply_theme()
        try:
            global config; config.set('GUIState', 'DarkMode', str(self.dark_mode_var.get()))
            with open(CONFIG_FILE, 'w') as cf: config.write(cf)
            logging.debug(f"Dark mode setting saved.")
        except Exception as e: logging.error(f"Failed save dark mode: {e}")

    def save_general_settings_to_config(self):
        global config; logging.info(f"Saving general settings...")
        try:
            if not config.has_section('Scraper'): config.add_section('Scraper')
            if not config.has_section('GUIState'): config.add_section('GUIState')
            config.set('Scraper', 'CheckIntervalMinutes', str(self.interval_var.get()))
            config.set('GUIState', 'DarkMode', str(self.dark_mode_var.get()))
            if config.has_section('GUIState'):
                for key in list(config['GUIState']):
                     if key.lower() != 'darkmode': config.remove_option('GUIState', key)
            with open(CONFIG_FILE, 'w') as cf: config.write(cf)
            logging.info("General settings saved.")
        except Exception as e: logging.error(f"Failed save general settings: {e}")

    # --- Add this entire method inside the ScraperApp class ---
    def check_for_trigger_file(self):
        """Checks if the trigger file exists and starts a scan if needed."""
        # Check if the Tkinter app itself thinks a scan is running
        if self.running: # Check the instance's running flag
            # logging.debug("Trigger check skipped: Scan already running.")
            # Reschedule the check and return
            if hasattr(self.master, 'after') and self.master.winfo_exists(): # Check master exists
                self.master.after(self.check_interval_ms, self.check_for_trigger_file)
            return

        # If not scanning, check for the file
        trigger_exists = False
        try:
            if os.path.exists(self.trigger_file_path):
                trigger_exists = True
                logging.info(f"Trigger file '{self.trigger_file_path}' detected.")

                # Attempt to remove the file FIRST to prevent race conditions
                try:
                    os.remove(self.trigger_file_path)
                    logging.info(f"Trigger file '{self.trigger_file_path}' removed.")
                except OSError as e:
                    logging.error(f"Could not remove trigger file {self.trigger_file_path}: {e}")
                    trigger_exists = False # Don't proceed if removal failed

        except Exception as e:
            logging.error(f"Error during trigger file check: {e}")
            trigger_exists = False

        # If file existed and was successfully removed, try starting scan
        if trigger_exists:
            logging.info("Attempting to start scan triggered by file.")
            # Call the existing start scan method of the instance
            self.start_scan()

        # Always reschedule the check if the window still exists
        if hasattr(self.master, 'after') and self.master.winfo_exists():
            self.master.after(self.check_interval_ms, self.check_for_trigger_file)
    # --- End of check_for_trigger_file method ---
    # --- Add this entire method inside the ScraperApp class (Optional but Recommended) ---
    def update_status_file(self, status):
        """Writes the current scan status to a file for Flask to read."""
        try:
            with open(self.status_file_path, 'w') as f:
                f.write(status)
            # logging.debug(f"Updated status file to: {status}")
        except Exception as e:
            logging.error(f"Could not write to status file {self.status_file_path}: {e}")
    # --- End of update_status_file method ---
    # --- Tab Creation ---
    def create_run_log_tab(self):
        controls_frame=ttk.Frame(self.tab_run_log, style='TFrame'); controls_frame.pack(side=tk.TOP,fill=tk.X,pady=(0,10))
        self.start_button=ttk.Button(controls_frame,text="Start Scan",command=self.start_scan,width=12); self.start_button.pack(side=tk.LEFT,padx=5,pady=5)
        self.stop_button=ttk.Button(controls_frame,text="Stop Scan",command=self.stop_scan,state=tk.DISABLED,width=12); self.stop_button.pack(side=tk.LEFT,padx=5,pady=5)
        ttk.Label(controls_frame,text="Interval(min):").pack(side=tk.LEFT,padx=(15,5),pady=5);
        self.interval_spinbox=ttk.Spinbox(controls_frame,from_=1, to=1440, width=5, textvariable=self.interval_var, command=self.update_interval_from_spinbox, state=tk.NORMAL); self.interval_spinbox.pack(side=tk.LEFT,pady=5)
        ttk.Checkbutton(controls_frame,text="Dark Mode",variable=self.dark_mode_var,command=self.toggle_theme).pack(side=tk.LEFT,padx=(15,5),pady=5)
        found_frame=ttk.LabelFrame(self.tab_run_log,text="Found Listings Log",style='TLabelframe'); found_frame.pack(side=tk.TOP,fill=tk.BOTH,expand=False,pady=(10,5),padx=5)
        self.found_log_area=scrolledtext.ScrolledText(found_frame,state=tk.DISABLED,height=6,wrap=tk.WORD,font=("Consolas",9)); self.found_log_area.pack(fill=tk.BOTH,expand=True,padx=5,pady=5)
        debug_frame=ttk.LabelFrame(self.tab_run_log,text="Debug Console",style='TLabelframe'); debug_frame.pack(side=tk.BOTTOM,fill=tk.BOTH,expand=True,pady=(5,5),padx=5)
        self.debug_log_area=scrolledtext.ScrolledText(debug_frame,state=tk.DISABLED,height=15,wrap=tk.WORD,font=("Consolas",9)); self.debug_log_area.pack(fill=tk.BOTH,expand=True,padx=5,pady=5)
        # Tag configs now fully handled in apply_theme

    def create_results_tab(self):
        results_frame=ttk.Frame(self.tab_results, style='TFrame'); results_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        columns=("title","price","location","date","condition","url","image_url"); self.results_tree=ttk.Treeview(results_frame, columns=columns, show='headings', height=20, style="Results.Treeview")
        self.style.configure("Results.Treeview"); self.style.configure("Results.Treeview.Heading")

        # Setup headings and columns
        self.results_tree.heading("title", text="Title", anchor=tk.W); self.results_tree.column("title", width=280, stretch=tk.YES); self.results_tree.heading("price", text="Price", anchor=tk.W); self.results_tree.column("price", width=80, stretch=tk.NO); self.results_tree.heading("location", text="Location", anchor=tk.W); self.results_tree.column("location", width=150, stretch=tk.YES); self.results_tree.heading("date", text="Date Found", anchor=tk.W); self.results_tree.column("date", width=120, stretch=tk.NO); self.results_tree.heading("condition", text="Condition", anchor=tk.W); self.results_tree.column("condition", width=100, stretch=tk.NO); self.results_tree.heading("url", text="Listing URL", anchor=tk.W); self.results_tree.column("url", width=180, stretch=tk.YES); self.results_tree.heading("image_url", text="Image URL", anchor=tk.W); self.results_tree.column("image_url", width=180, stretch=tk.YES)
        vsb=ttk.Scrollbar(results_frame, orient=VERTICAL, command=self.results_tree.yview); hsb=ttk.Scrollbar(results_frame, orient=HORIZONTAL, command=self.results_tree.xview); self.results_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.results_tree.grid(row=0, column=0, sticky='nsew'); vsb.grid(row=0, column=1, sticky='ns'); hsb.grid(row=1, column=0, sticky='ew'); results_frame.grid_rowconfigure(0, weight=1); results_frame.grid_columnconfigure(0, weight=1)
        ttk.Button(results_frame, text="Clear Results", command=self.clear_results_tree).grid(row=2, column=0, columnspan=2, pady=(10, 0)); self.results_tree.bind("<Double-1>", self.on_treeview_double_click)

    # --- Query Management Tab ---
    def create_query_management_tab(self):
        paned_window = ttk.PanedWindow(self.tab_query_management, orient=tk.HORIZONTAL)
        paned_window.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Left: Query List
        query_list_frame = ttk.Frame(paned_window, padding=5, style='TFrame')
        paned_window.add(query_list_frame, weight=1)
        ttk.Label(query_list_frame, text="Saved Queries (Dbl-Click Active):", style='TLabelframe.Label').grid(row=0, column=0, columnspan=2, sticky='w', pady=(0,5))
        tree_cols = ("active", "name", "url")
        self.query_tree = ttk.Treeview(query_list_frame, columns=tree_cols, show='headings', selectmode='browse', style="Query.Treeview")
        self.style.configure("Query.Treeview"); self.style.configure("Query.Treeview.Heading")
        self.query_tree.heading("active", text="Active", anchor=tk.CENTER); self.query_tree.column("active", width=50, stretch=False, anchor=tk.CENTER)
        self.query_tree.heading("name", text="Query Name", anchor=tk.W); self.query_tree.column("name", width=150, stretch=True)
        self.query_tree.heading("url", text="Base URL", anchor=tk.W); self.query_tree.column("url", width=250, stretch=True)
        tree_scr_y = ttk.Scrollbar(query_list_frame, orient=tk.VERTICAL, command=self.query_tree.yview); tree_scr_x = ttk.Scrollbar(query_list_frame, orient=tk.HORIZONTAL, command=self.query_tree.xview)
        self.query_tree.configure(yscrollcommand=tree_scr_y.set, xscrollcommand=tree_scr_x.set)
        self.query_tree.grid(row=1, column=0, sticky='nsew'); tree_scr_y.grid(row=1, column=1, sticky='ns'); tree_scr_x.grid(row=2, column=0, sticky='ew')
        query_list_frame.grid_rowconfigure(1, weight=1); query_list_frame.grid_columnconfigure(0, weight=1)
        self.populate_query_tree(); self.query_tree.bind("<<TreeviewSelect>>", self.on_query_selected); self.query_tree.bind("<Double-1>", self.toggle_query_active)

        # Right: Query Editor
        self.query_detail_frame = ttk.LabelFrame(paned_window, text="Edit Selected Query", padding=10, style='TLabelframe')
        paned_window.add(self.query_detail_frame, weight=2)
        self.query_detail_frame.columnconfigure(1, weight=1) # Make entry column expandable

        current_row = 0
        # Row 0: Name & Active
        ttk.Label(self.query_detail_frame, text="Name:").grid(row=current_row, column=0, sticky=tk.W, padx=5, pady=3)
        ttk.Entry(self.query_detail_frame, textvariable=self.edit_name_var).grid(row=current_row, column=1, sticky=tk.EW, padx=5, pady=3)
        ttk.Checkbutton(self.query_detail_frame, text="Scan Active", variable=self.edit_active_var).grid(row=current_row, column=2, sticky=tk.W, padx=10, pady=3)
        current_row += 1
        # Row 1: Base URL
        ttk.Label(self.query_detail_frame, text="Base URL:").grid(row=current_row, column=0, sticky=tk.W, padx=5, pady=3)
        ttk.Entry(self.query_detail_frame, textvariable=self.edit_url_var).grid(row=current_row, column=1, columnspan=2, sticky=tk.EW, padx=5, pady=3)
        current_row += 1
        # Row 2: Postcode & Distance Frame
        loc_frame = ttk.Frame(self.query_detail_frame, style='TFrame')
        loc_frame.grid(row=current_row, column=0, columnspan=3, sticky=tk.EW, pady=4)
        ttk.Label(loc_frame, text="Postcode:").pack(side=tk.LEFT, padx=(5,2))
        ttk.Entry(loc_frame, textvariable=self.edit_postcode_var, width=10).pack(side=tk.LEFT, padx=(0,10))
        ttk.Label(loc_frame, text="Distance (km):").pack(side=tk.LEFT, padx=(10,2))
        ttk.Entry(loc_frame, textvariable=self.edit_distance_var, width=8).pack(side=tk.LEFT, padx=(0,5))
        current_row += 1
        # Row 3: Price Frame
        p_frame = ttk.Frame(self.query_detail_frame, style='TFrame'); p_frame.grid(row=current_row, column=0, columnspan=3, sticky=tk.EW, pady=4)
        ttk.Label(p_frame, text="Min Price:").pack(side=tk.LEFT, padx=(5, 2)); ttk.Entry(p_frame, textvariable=self.edit_min_price_var, width=8).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(p_frame, text="Max Price:").pack(side=tk.LEFT, padx=(5, 2)); ttk.Entry(p_frame, textvariable=self.edit_max_price_var, width=8).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Checkbutton(p_frame, text="Incl.'Bieden'", variable=self.edit_include_non_priced_var).pack(side=tk.LEFT, padx=5)
        current_row += 1
        # Row 4: Condition Frame
        c_frame = ttk.Frame(self.query_detail_frame, style='TFrame'); c_frame.grid(row=current_row, column=0, columnspan=3, sticky=tk.EW, pady=4)
        ttk.Label(c_frame, text="Conditions:").pack(side=tk.LEFT, padx=5)
        for cond, var in self.edit_condition_vars.items(): ttk.Checkbutton(c_frame, text=cond.replace("zo goed als nieuw", "ZGAN").capitalize(), variable=var).pack(side=tk.LEFT, padx=5)
        current_row += 1
        # Row 5: Age Frame
        a_frame = ttk.Frame(self.query_detail_frame, style='TFrame'); a_frame.grid(row=current_row, column=0, columnspan=3, sticky=tk.EW, pady=4)
        ttk.Label(a_frame, text="Max Age(days):").pack(side=tk.LEFT, padx=5)
        self.edit_max_age_spinbox = Spinbox(a_frame, from_=1, to=365, width=5, textvariable=self.edit_max_age_var, state=tk.NORMAL); self.edit_max_age_spinbox.pack(side=tk.LEFT, padx=5)
        current_row += 1
        # Row 6: Required Keywords
        rq_frame = ttk.Frame(self.query_detail_frame, style='TFrame'); rq_frame.grid(row=current_row, column=0, columnspan=3, sticky=tk.NSEW, pady=(8, 4)); rq_frame.columnconfigure(0, weight=1)
        ttk.Label(rq_frame, text="Required KWs (one per line):").pack(anchor=tk.W, padx=5)
        self.edit_req_keywords_text = scrolledtext.ScrolledText(rq_frame, height=4, width=40, wrap=tk.WORD, font=('Segoe UI', 9)); self.edit_req_keywords_text.pack(fill=tk.BOTH, expand=True, padx=5)
        self.query_detail_frame.rowconfigure(current_row, weight=1)
        current_row += 1
        # Row 7: Excluded Keywords
        ex_frame = ttk.Frame(self.query_detail_frame, style='TFrame'); ex_frame.grid(row=current_row, column=0, columnspan=3, sticky=tk.NSEW, pady=4); ex_frame.columnconfigure(0, weight=1)
        ttk.Label(ex_frame, text="Excluded KWs (one per line):").pack(anchor=tk.W, padx=5)
        self.edit_excl_keywords_text = scrolledtext.ScrolledText(ex_frame, height=4, width=40, wrap=tk.WORD, font=('Segoe UI', 9)); self.edit_excl_keywords_text.pack(fill=tk.BOTH, expand=True, padx=5)
        self.query_detail_frame.rowconfigure(current_row, weight=1)
        current_row += 1

        # Action Buttons Frame
        act_frame = ttk.Frame(self.tab_query_management, padding=(0, 10, 0, 0), style='TFrame')
        act_frame.pack(fill=tk.X, side=tk.BOTTOM)
        self.add_query_button = ttk.Button(act_frame, text="Add New", command=self.add_new_query_action); self.add_query_button.pack(side=tk.LEFT, padx=5)
        self.save_query_button = ttk.Button(act_frame, text="Save", command=self.save_query_action, state=tk.DISABLED); self.save_query_button.pack(side=tk.LEFT, padx=5)
        self.delete_query_button = ttk.Button(act_frame, text="Delete", command=self.delete_query_action, state=tk.DISABLED); self.delete_query_button.pack(side=tk.RIGHT, padx=5)

        self.apply_theme()
    # --- Methods for Query Management GUI ---

    def populate_query_tree(self):
        if not hasattr(self, 'query_tree'): return
        sel_iid = self.query_tree.focus()
        for item in self.query_tree.get_children():
            try: self.query_tree.delete(item)
            except tk.TclError: pass
        sorted_queries = sorted(self.queries, key=lambda q: q.get('name', '').lower())
        for index, query_dict in enumerate(sorted_queries):
            query_id = query_dict.get("id") or f"query_{uuid.uuid4()}"; query_dict["id"] = query_id
            name = query_dict.get("name", f"Query {index+1}"); url = query_dict.get("url", "N/A")
            active_status = "✓" if query_dict.get("active", False) else "✗"
            try: self.query_tree.insert("", tk.END, iid=query_id, values=(active_status, name, url))
            except tk.TclError as e: logging.error(f"Tree Insert Error (ID:{query_id}): {e}")
        if sel_iid and self.query_tree.exists(sel_iid):
            try: self.query_tree.focus(sel_iid); self.query_tree.selection_set(sel_iid)
            except tk.TclError: pass
        logging.debug(f"Populated query tree.")

    def clear_detail_frame(self):
        logging.debug("Clearing detail frame for new query.")
        self.edit_query_id = None
        self.edit_name_var.set("")
        self.edit_url_var.set("")
        self.edit_postcode_var.set("")
        self.edit_distance_var.set("") # Clear distance
        self.edit_active_var.set(True)
        self.edit_min_price_var.set("")
        self.edit_max_price_var.set("")
        # Set fields based on defaults
        self.edit_include_non_priced_var.set(DEFAULT_QUERY_SETTINGS.get("include_non_priced", True))
        try: self.edit_max_age_var.set(DEFAULT_QUERY_SETTINGS.get("max_age_days", 7))
        except: self.edit_max_age_var.set(7)
        default_conds = DEFAULT_QUERY_SETTINGS.get("conditions", [])
        for cond, var in self.edit_condition_vars.items(): var.set(cond in default_conds)
        if hasattr(self,'edit_req_keywords_text'): self.edit_req_keywords_text.delete('1.0',tk.END)
        if hasattr(self,'edit_excl_keywords_text'): self.edit_excl_keywords_text.delete('1.0',tk.END)

        # Reset button states for adding new query
        if hasattr(self,'save_query_button'): self.save_query_button.config(state=tk.NORMAL)
        if hasattr(self,'delete_query_button'): self.delete_query_button.config(state=tk.DISABLED)
        # Set focus to name field

    def on_query_selected(self, event=None):
        selected_item_ids = self.query_tree.selection()
        if not selected_item_ids:
            self.clear_detail_frame(); return

        selected_item_id = selected_item_ids[0]
        logging.debug(f"Query selected: {selected_item_id}")
        selected_query_dict = next((q for q in self.queries if q.get("id") == selected_item_id), None)

        if selected_query_dict:
            self.edit_query_id = selected_item_id
            self.edit_name_var.set(selected_query_dict.get("name", ""))
            self.edit_url_var.set(selected_query_dict.get("url", "")) # Base URL
            self.edit_active_var.set(selected_query_dict.get("active", True))
            self.edit_postcode_var.set(selected_query_dict.get("postcode", "") or "")

            # Load distance (convert meters to KM for display)
            distance_m = selected_query_dict.get("distanceMeters")
            distance_km_str = ""
            if distance_m is not None:
                try: distance_km_str = str(int(distance_m) // 1000)
                except (ValueError, TypeError): distance_km_str = ""
            self.edit_distance_var.set(distance_km_str)

            min_p=selected_query_dict.get("min_price"); self.edit_min_price_var.set(str(min_p) if min_p is not None else "")
            max_p=selected_query_dict.get("max_price"); self.edit_max_price_var.set(str(max_p) if max_p is not None else "")
            self.edit_include_non_priced_var.set(selected_query_dict.get("include_non_priced", True))
            try: self.edit_max_age_var.set(selected_query_dict.get("max_age_days", 7))
            except (ValueError, TypeError): self.edit_max_age_var.set(7)

            sel_conds = selected_query_dict.get("conditions", DEFAULT_QUERY_SETTINGS.get("conditions", []))
            for cond, var in self.edit_condition_vars.items(): var.set(cond in sel_conds)

            req_kws = selected_query_dict.get("required_keywords", []); excl_kws = selected_query_dict.get("excluded_keywords", [])
            if hasattr(self,'edit_req_keywords_text'): self.edit_req_keywords_text.delete('1.0',tk.END); self.edit_req_keywords_text.insert('1.0',"\n".join(map(str,req_kws)))
            if hasattr(self,'edit_excl_keywords_text'): self.edit_excl_keywords_text.delete('1.0',tk.END); self.edit_excl_keywords_text.insert('1.0',"\n".join(map(str,excl_kws)))

            if hasattr(self,'save_query_button'): self.save_query_button.config(state=tk.NORMAL)
            if hasattr(self,'delete_query_button'): self.delete_query_button.config(state=tk.NORMAL)
            logging.debug(f"Loaded query '{selected_query_dict.get('name')}' into editor.")
        else:
            logging.error(f"Query data not found for ID: {selected_item_id}")
            self.clear_detail_frame()

    def toggle_query_active(self, event=None):
        selected_item_id = self.query_tree.focus()
        if not selected_item_id: return
        logging.debug(f"Toggle active requested for {selected_item_id}")
        updated = False
        for query_dict in self.queries:
            if query_dict.get("id") == selected_item_id:
                query_dict["active"] = not query_dict.get("active", False)
                updated = True
                logging.info(f"Toggled '{query_dict.get('name')}' active to {query_dict['active']}.")
                break
        if updated: save_queries(self.queries); self.populate_query_tree()

    def add_new_query_action(self):
        logging.debug("Add New Query button clicked")
        self.query_tree.selection_set([])
        self.clear_detail_frame()
        self.edit_query_id = None
        self.save_query_button.config(state=tk.NORMAL)
        self.delete_query_button.config(state=tk.DISABLED)
        messagebox.showinfo("Add Query", "Enter details for the new query and click 'Save'.")


    def save_query_action(self):
        is_new_query = self.edit_query_id is None
        query_id = self.edit_query_id if not is_new_query else f"query_{uuid.uuid4()}"

        name = self.edit_name_var.get().strip() or f"Query_{query_id[:8]}"
        url = self.edit_url_var.get().strip() # Base URL

        if not url or not url.startswith('http'):
            messagebox.showerror("Invalid URL", "Base URL must start with http(s)://.")
            return

        postcode_raw = self.edit_postcode_var.get().strip()
        postcode = postcode_raw if postcode_raw else None

        # Get distance (km), convert to meters for storage
        distance_km_str = self.edit_distance_var.get().strip()
        distance_meters = None
        if distance_km_str:
            try:
                distance_km = int(distance_km_str)
                if distance_km <= 0: raise ValueError("Distance must be positive.")
                distance_meters = distance_km * 1000 # Convert KM to Meters
            except ValueError:
                messagebox.showerror("Invalid Input", f"Distance (km) must be a positive whole number. You entered: '{distance_km_str}'")
                return
        # --- End Distance Handling ---

        try:
            min_p_str=self.edit_min_price_var.get().replace(',','.')
            min_p=float(min_p_str) if min_p_str else None
            max_p_str=self.edit_max_price_var.get().replace(',','.')
            max_p=float(max_p_str) if max_p_str else None
            max_age=self.edit_max_age_var.get()
            if max_age < 1: raise ValueError("Max age must be 1 or greater.")
        except ValueError as e:
            messagebox.showerror("Invalid Input", f"Please check numeric fields (Price/Max Age).\nError: {e}")
            return
        except Exception as e:
            messagebox.showerror("Input Error", f"Error reading numeric fields: {e}")
            return

        req_kws = [line.strip().lower() for line in self.edit_req_keywords_text.get("1.0", tk.END).splitlines() if line.strip()]
        excl_kws = [line.strip().lower() for line in self.edit_excl_keywords_text.get("1.0", tk.END).splitlines() if line.strip()]
        sel_conds = [cond for cond, var in self.edit_condition_vars.items() if var.get()]

        # Create the dictionary including postcode and distanceMeters
        query_data = {
            "id": query_id, "name": name, "url": url,
            "active": self.edit_active_var.get(),
            "postcode": postcode,           # Postcode (or None)
            "distanceMeters": distance_meters, # Distance in Meters (or None)
            "min_price": min_p, "max_price": max_p,
            "include_non_priced": self.edit_include_non_priced_var.get(),
            "conditions": sel_conds, "max_age_days": max_age,
            "required_keywords": req_kws, "excluded_keywords": excl_kws
        }

        if is_new_query:
            self.queries.append(query_data)
            logging.info(f"Adding new query: {name}")
        else:
            found = False
            for i, q in enumerate(self.queries):
                if q.get("id") == query_id:
                    # Ensure all keys are updated
                    self.queries[i].update(query_data)
                    found = True; logging.info(f"Updating query: {name}"); break
            if not found:
                logging.error(f"Save failed: Query ID {query_id} not found."); messagebox.showerror("Save Error", f"Could not find query ID {query_id} to update."); return

        save_queries(self.queries) # Save the updated list to queries.json
        self.populate_query_tree() # Refresh the tree view

        if self.query_tree.exists(query_id):
            self.query_tree.focus(query_id); self.query_tree.selection_set(query_id)
            self.on_query_selected() # Update editor view

        logging.info("Query saved successfully.")
        messagebox.showinfo("Saved", f"Query '{name}' saved.")


    def delete_query_action(self):
        selected_item_id = self.query_tree.focus()
        if not selected_item_id: messagebox.showwarning("No Selection", "Select query to delete."); return
        query_to_delete = next((q for q in self.queries if q.get("id") == selected_item_id), None)
        if not query_to_delete: logging.error(f"Query ID {selected_item_id} not found."); return
        q_name = query_to_delete.get('name', selected_item_id)
        if messagebox.askyesno("Confirm Delete", f"Delete query:\n'{q_name}'?"):
            self.queries = [q for q in self.queries if q.get("id") != selected_item_id]
            save_queries(self.queries); self.populate_query_tree(); self.clear_detail_frame()
            logging.info(f"Deleted query: {q_name}")

    # --- Core App Methods ---
    def update_interval_from_spinbox(self):
        try:
            interval_value = self.interval_var.get()
            if interval_value >= 1: self.current_check_interval = interval_value; logging.debug(f"Interval GUI updated to {interval_value} min.")
            else: logging.warning(f"Invalid interval {interval_value}."); self.interval_var.set(self.current_check_interval)
        except (tk.TclError, ValueError) as e: logging.warning(f"Error reading interval: {e}. Resetting."); self.interval_var.set(self.current_check_interval)
        except Exception as e: logging.error(f"Unexpected error updating interval: {e}"); self.interval_var.set(self.current_check_interval)

    def start_scan(self):
        if not self.running:
            self.setup_logging(); self.update_interval_from_spinbox()
            active_queries_to_run = [q for q in self.queries if q.get("active", False)]
            if not active_queries_to_run: messagebox.showwarning("No Active Queries", "Activate queries via double-click."); return
            logging.info(f"Found {len(active_queries_to_run)} active queries.")
            if not DEFAULT_TELEGRAM_BOT_TOKEN or not DEFAULT_TELEGRAM_CHAT_ID:
                 if not messagebox.askokcancel("Telegram Warning", "TG settings missing. Continue anyway?"): return
            self.running = True; self.start_button.config(state=tk.DISABLED); self.stop_button.config(state=tk.NORMAL)
            if hasattr(self, 'interval_spinbox'): self.interval_spinbox.config(state=tk.DISABLED)
            current_interval = self.interval_var.get()
            self.update_status_file("running")
            logging.info(f"Starting scraper thread (Interval: {current_interval} min)...")
            self.scraper_thread = threading.Thread(target=self.run_scraper_thread, args=(self, active_queries_to_run, current_interval), daemon=True)
            self.scraper_thread.start()

    def stop_scan(self):
        if self.running: logging.info("Stop requested..."); self.running = False; self.stop_button.config(state=tk.DISABLED)

# --- Replace entire run_scraper_thread function in scraper.py (inside ScraperApp class) ---
    def run_scraper_thread(self, app_instance, active_queries, interval_minutes):
        """Scraper loop: runs scans, saves results, and saves stats to JSON file."""
        global config, DEFAULT_REQUEST_DELAY, STATS_FILE # Ensure access to needed globals

        try:
            init_db()
        except Exception as e:
            logging.critical(f"Thread DB init error: {e}")
            self.update_status_file("error_db_init") # Update status on critical error
            if hasattr(self.master, 'after'): self.master.after(0, self.handle_thread_exit, False)
            return

        active_query_count_for_run = len(active_queries) # Get count for this specific run

        while self.running: # Check the instance flag controlled by start/stop scan
            cycle_start_time = time.time()
            logging.info(f"--- Starting Scan Cycle ({datetime.now():%H:%M:%S}) ---")
            self.update_status_file("running") # Set status to running

            # Reload queries within the loop in case they were changed externally (optional but safer)
            # active_queries = [q for q in load_queries() if q.get("active", False)]
            # active_query_count_for_run = len(active_queries)
            # --- Re-evaluate if queries list should be dynamic or fixed per start_scan ---

            if not active_queries:
                logging.warning("Scan cycle running but has no active queries.")
                # Use a shorter sleep if no queries are active, to check more often
                wait_seconds_no_query = min(interval_minutes * 60, 60) # Check every min if no queries
                wait_until = time.time() + wait_seconds_no_query
                while time.time() < wait_until and self.running: time.sleep(1)
                continue # Skip to next loop iteration if no queries active

            total_new_in_cycle = 0
            for query_index, query_config in enumerate(active_queries):
                if not self.running: break # Check if stop was requested

                q_name = query_config.get('name', f'Query {query_index+1}')
                logging.info(f"--- Processing Query {query_index+1}/{len(active_queries)}: '{q_name}' ---")
                q_start = time.time()
                try:
                    # Ensure scrape_and_process returns the count of new listings
                    new_listings_for_query = scrape_and_process(app_instance, query_config)
                    total_new_in_cycle += new_listings_for_query
                    logging.info(f"--- Query '{q_name}' done ({time.time() - q_start:.2f}s). New: {new_listings_for_query} ---")
                except Exception as e:
                    logging.critical(f"Error processing query '{q_name}': {e}", exc_info=True)
                    # Continue even if one query fails

                # Delay between queries
                if self.running and query_index < len(active_queries) - 1:
                    delay = DEFAULT_REQUEST_DELAY # Use the globally loaded value
                    logging.debug(f"Waiting {delay}s before next query...")
                    wait_end = time.time() + delay
                    while time.time() < wait_end and self.running:
                        time.sleep(min(1, wait_end - time.time()))

            if not self.running: break # Check if stopped

            # --- Cycle Finished: Log, Calculate Wait, and Save Stats ---
            cycle_finish_time = datetime.now()
            cycle_duration = time.time() - cycle_start_time
            logging.info(f"--- Scan Cycle finished at {cycle_finish_time:%Y-%m-%d %H:%M:%S}. Duration: {cycle_duration:.2f}s. Total new in cycle: {total_new_in_cycle} ---")

            # --- *** Prepare and Save Stats to JSON File *** ---
            stats_data = {
                'last_fetch_time': cycle_finish_time.isoformat(), # Use standard ISO format string
                'last_found_count': total_new_in_cycle,
                'active_query_count': active_query_count_for_run # Queries active during *this specific run*
            }
            try:
                # Use the STATS_FILE constant defined at the top
                with open(STATS_FILE, 'w', encoding='utf-8') as f_stats:
                    json.dump(stats_data, f_stats, indent=4)
                logging.info(f"Scan statistics saved to {STATS_FILE}") # Log success
            except IOError as e:
                logging.error(f"Could not write statistics to {STATS_FILE}: {e}")
            except Exception as e:
                logging.error(f"Unexpected error saving stats to {STATS_FILE}: {e}")
            # --- *** End Save Stats *** ---

            # Calculate wait time for next cycle
            wait_seconds = max(10, (interval_minutes * 60) - cycle_duration)
            logging.info(f"--- Waiting {wait_seconds:.0f}s for next cycle ({interval_minutes}m interval)... ---")
            self.update_status_file("idle") # Update status to idle while waiting
            wait_until = time.time() + wait_seconds
            while time.time() < wait_until and self.running: # Check running flag frequently
                time.sleep(1)

        # --- Loop Exited ---
        logging.info("Scraper thread processing loop finished.")
        if hasattr(self.master, 'after'): self.master.after(0, self.handle_thread_exit, True)
    # --- End of replaced run_scraper_thread function ---

    def handle_thread_exit(self, normal_exit=True):
        self.update_status_file("idle")
        self.running = False; self.scraper_thread = None; self.start_button.config(state=tk.NORMAL); self.stop_button.config(state=tk.DISABLED)
        if hasattr(self, 'interval_spinbox'): self.interval_spinbox.config(state=tk.NORMAL)
        if normal_exit: logging.info("Scan stopped.")
        else: logging.error("Scraper thread error."); messagebox.showerror("Thread Error", "Scraper thread error. Check log.")

    def on_closing(self):
        if self.running:
            if messagebox.askokcancel("Quit", "Scraper running. Stop & Quit?"): logging.info("Stop via close."); self.stop_scan(); self.master.after(500, self.finalize_exit)
            else: return
        else: self.finalize_exit()

    def finalize_exit(self):
         logging.info("Saving configs before closing."); save_queries(self.queries); self.save_general_settings_to_config()
         logging.info("Exiting."); self.master.destroy()
         self.update_status_file("offline")
         if os.path.exists(self.trigger_file_path):
            try:
                os.remove(self.trigger_file_path)
                logging.info("Cleaned up trigger file on exit.")
            except OSError as e:
                logging.warning(f"Could not remove trigger file on exit: {e}")


    # --- Results Table Methods ---
    def add_result_to_treeview(self, listing_data):
        if not hasattr(self, 'results_tree') or not self.results_tree.winfo_exists(): return
        try: values = ( listing_data.get("title", "N/A"), listing_data.get("price", "N/A"), listing_data.get("location", "N/A"), listing_data.get("date", "N/A"), listing_data.get("condition", "N/A"), listing_data.get("url", "N/A"), listing_data.get("image_url", "N/A") ); self.results_tree.insert("", 0, values=values)
        except Exception as e: logging.error(f"Failed add result: {e}")
    def clear_results_tree(self):
        if hasattr(self, 'results_tree'): [self.results_tree.delete(item) for item in self.results_tree.get_children()]; logging.info("Results cleared.")
    def on_treeview_double_click(self, event):
        try:
            widget = event.widget
            if hasattr(self, 'results_tree') and widget == self.results_tree:
                if self.results_tree.identify("region", event.x, event.y) != "cell": return
                col_id = self.results_tree.identify_column(event.x); col_idx = int(col_id.replace('#','')) - 1
                item_id = self.results_tree.identify_row(event.y);
                if not item_id: return
                vals = self.results_tree.item(item_id)['values']; url_to_open = None
                if col_idx == 5: url_to_open = vals[5]
                elif col_idx == 6: url_to_open = vals[6]
                if url_to_open and url_to_open != 'N/A' and url_to_open.startswith('http'): webbrowser.open_new_tab(url_to_open); logging.info(f"Opened URL: {url_to_open}")
                elif url_to_open and url_to_open != 'N/A': logging.warning(f"Invalid URL: {url_to_open}")
        except Exception as e: logging.error(f"Error opening URL: {e}")

    # --- Logging Setup ---
    def setup_logging(self):
        log_dir = os.path.dirname(DEFAULT_LOG_FILE)
        if log_dir and not os.path.exists(log_dir):
            try: os.makedirs(log_dir); logging.info(f"Created log directory: {log_dir}")
            except OSError as e: logging.error(f"Failed create log dir {log_dir}: {e}")
        logger = logging.getLogger(); logger.setLevel(logging.DEBUG)
        has_dual_handler = any(isinstance(h, DualTextAreaHandler) for h in logger.handlers)
        if not has_dual_handler and hasattr(self, 'debug_log_area') and hasattr(self, 'found_log_area'):
            self.dual_text_handler = DualTextAreaHandler(self.debug_log_area, self.found_log_area)
            logger.addHandler(self.dual_text_handler)
        elif not has_dual_handler: logging.warning("Log text areas not found during setup.")
        try:
            has_file_handler = any(isinstance(h, logging.FileHandler) and h.baseFilename == os.path.abspath(DEFAULT_LOG_FILE) for h in logger.handlers)
            if not has_file_handler:
                file_handler = logging.FileHandler(DEFAULT_LOG_FILE, encoding='utf-8', mode='a')
                file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)-8s - %(message)s'))
                file_log_level = getattr(logging, DEFAULT_LOG_LEVEL.upper(), logging.INFO)
                file_handler.setLevel(file_log_level)
                logger.addHandler(file_handler)
                logging.info(f"File logging initialized: {DEFAULT_LOG_FILE} (Level: {DEFAULT_LOG_LEVEL.upper()})")
        except Exception as e: logging.error(f"Failed file logging: {e}")
        if hasattr(self, 'debug_log_area'): self.debug_log_area.config(state=tk.DISABLED)
        if hasattr(self, 'found_log_area'): self.found_log_area.config(state=tk.DISABLED)


# --- Main Execution ---
if __name__ == "__main__":
    log_format = '%(asctime)s - %(levelname)-8s - [%(threadName)s] %(message)s'
    root_logger = logging.getLogger()
    if not root_logger.hasHandlers(): # Only configure basic if no handlers exist
         logging.basicConfig(level=logging.INFO, format=log_format)
         print("Basic logging # Ensure root logger level allows messages through")

    load_configuration()
    log_dir_main = os.path.dirname(DEFAULT_LOG_FILE)
    if log_dir_main and not os.path.exists(log_dir_main):
        try: os.makedirs(log_dir_main)
        except OSError as e: print(f"ERROR: Could not create log directory '{log_dir_main}': {e}")

    root = tk.Tk()
    app = ScraperApp(root)
    root.mainloop()
