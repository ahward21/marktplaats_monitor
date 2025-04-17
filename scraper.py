# scraper_gui_final_fixes_v19.py - Switch TG to HTML Parse Mode, Log fixes check
# ADDED Hide/Favorite DB Support
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
geolocator = Nominatim(user_agent="marktplaats_scraper_geo_v2") # Unique agent
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

# --- Database Setup (MODIFIED init_db) ---
def init_db():
    db_path = DEFAULT_DB_PATH
    conn = None # Initialize conn to None
    try:
        if not db_path or db_path in [".","./"]:
            db_path="marktplaats_listings.db"
            logging.warning(f"Invalid DB path. Using '{db_path}'.")
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
             try:
                 os.makedirs(db_dir)
                 logging.info(f"Created DB dir: {db_dir}")
             except OSError as e:
                 logging.error(f"DB dir creation fail '{db_dir}': {e}")
                 # If directory creation fails, attempt to use basename in current dir
                 db_path=os.path.basename(db_path)
                 logging.warning(f"Falling back to DB path: {db_path}")

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS listings (
                    url TEXT PRIMARY KEY,
                    title TEXT,
                    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    price TEXT,
                    image_url TEXT,
                    location TEXT,
                    condition TEXT,
                    latitude REAL,
                    longitude REAL,
                    hidden INTEGER DEFAULT 0 NOT NULL,    -- Added: 0=visible, 1=hidden
                    favorite INTEGER DEFAULT 0 NOT NULL  -- Added: 0=not favorite, 1=favorite
                  )''')

        # Add 'hidden' column if it doesn't exist (for existing databases)
        cursor.execute("PRAGMA table_info(listings)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'hidden' not in columns:
            logging.info("Adding 'hidden' column to listings table.")
            cursor.execute("ALTER TABLE listings ADD COLUMN hidden INTEGER DEFAULT 0 NOT NULL")
        if 'favorite' not in columns:
            logging.info("Adding 'favorite' column to listings table.")
            cursor.execute("ALTER TABLE listings ADD COLUMN favorite INTEGER DEFAULT 0 NOT NULL")

        conn.commit()
        logging.info(f"DB initialized/checked: {os.path.abspath(db_path)}")
    except sqlite3.Error as e:
        logging.error(f"DB init/alter error ({db_path}): {e}")
        try: messagebox.showerror("DB Error", f"Init/Alter fail:\n{e}\nPath:{db_path}")
        except tk.TclError: pass # If GUI not running
        raise
    except Exception as e:
        logging.error(f"Unexpected DB init/alter err({db_path}): {e}")
        try: messagebox.showerror("Init Error", f"DB setup/alter fail:\n{e}")
        except tk.TclError: pass
        raise
    finally:
        if conn:
            conn.close()


def is_duplicate(url):
    try:
        conn=sqlite3.connect(DEFAULT_DB_PATH)
        cursor=conn.cursor()
        # Check if URL exists, regardless of hidden status
        cursor.execute("SELECT 1 FROM listings WHERE url=?",(url,))
        res=cursor.fetchone()
        conn.close()
        return res is not None
    except sqlite3.Error as e:
        logging.error(f"DB duplicate check error: {e}")
        return True # Assume duplicate on error to be safe


def add_listing_to_db(url, title, price, image_url, location, condition, latitude, longitude):
    """Adds or ignores a listing in the database including lat/lon. Hidden/Favorite default to 0."""
    db_path_local = DEFAULT_DB_PATH
    if not db_path_local:
        logging.error("DB Path not set, cannot add listing.")
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path_local)
        cursor = conn.cursor()
        # hidden and favorite will use their DEFAULT 0 values on INSERT
        cursor.execute("""
            INSERT OR IGNORE INTO listings
            (url, title, price, image_url, location, condition, latitude, longitude, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (url, title, price, image_url, location, condition, latitude, longitude, datetime.now()))
        conn.commit()
        logging.debug(f"DB Add/Ignore: {url} with coords ({latitude}, {longitude})")
    except sqlite3.Error as e:
        logging.error(f"DB add error for {url}: {e}")
    except Exception as e:
        logging.error(f"Unexpected error adding to DB for {url}: {e}")
    finally:
        if conn:
            conn.close()


# --- get_listings_from_db function in scraper.py (MODIFIED) ---
def get_listings_from_db(limit=100):
    """Fetches non-hidden listings, sorted by favorite status then date."""
    conn = None
    db_path_local = DEFAULT_DB_PATH
    if not db_path_local:
        logging.error("Database path not configured for get_listings.")
        return []
    listings_data = []
    try:
         conn = sqlite3.connect(db_path_local)
         conn.row_factory = sqlite3.Row # Return rows that behave like dicts
         cursor = conn.cursor()
         # Select non-hidden, order by favorite (desc), then date (desc)
         cursor.execute("""
             SELECT url, title, scraped_at, price, image_url, location, condition, latitude, longitude, favorite, hidden
             FROM listings
             WHERE hidden = 0
             ORDER BY favorite DESC, scraped_at DESC
             LIMIT ?
         """, (limit,))
         listings = cursor.fetchall()
         logging.debug(f"Fetched {len(listings)} non-hidden listings from DB.")
         # Convert Row objects to simple dictionaries
         for row in listings:
             listings_data.append(dict(row))
         return listings_data
    except sqlite3.Error as e:
         logging.error(f"Database error fetching listings from '{db_path_local}': {e}")
         return []
    except Exception as e:
         logging.error(f"Unexpected error fetching listings: {e}")
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
            else: return condition_text # Return potentially specific but unrecognized condition
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
                 elif pair: # Handle flags like 'offeredSince:Vandaag'
                    if ':' in pair: # Recheck if value was just True before
                       key, value = pair.split(':', 1)
                       filters_dict[key] = value
                    else: # Truly a flag
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

        # Reconstruct the fragment string (careful order)
        new_fragment_parts = []
        # Specific order might matter for Marktplaats, list known keys
        known_keys = ['q', 'f', 'postcode', 'distanceMeters', 'PriceCentsFrom', 'PriceCentsTo', 'offeredSince', 'sortBy', 'sortOrder']
        # Add known keys first if they exist in our dict
        for key in known_keys:
            if key in filters_dict:
                value = filters_dict[key]
                if value is True: # Handle flags
                    new_fragment_parts.append(key)
                else:
                    new_fragment_parts.append(f"{key}:{value}")

        # Add any other keys not in the known list
        for key, value in filters_dict.items():
            if key not in known_keys:
                if value is True:
                    new_fragment_parts.append(key)
                else:
                    new_fragment_parts.append(f"{key}:{value}")

        new_fragment = "|".join(map(str, new_fragment_parts)) # Join with pipe

        # Rebuild the final URL
        final_url = urlunparse((
            url_parts.scheme, url_parts.netloc, url_parts.path,
            url_parts.params, url_parts.query, new_fragment # Use new fragment
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
        # Check for common 'no results' messages if debug is enabled
        if is_debug:
            no_results_msg = soup.find(text=re.compile("Helaas.*geen resultaten|Er zijn geen resultaten gevonden", re.I))
            if no_results_msg:
                logging.debug(f"Marktplaats reported no results for '{query_name}'. URL: {final_url}")
            else:
                logging.warning(f"No listings items found with standard selectors for '{query_name}'. URL: {final_url}")
        else:
             logging.warning(f"No listing items found for '{query_name}'. URL: {final_url}")
        return 0
    logging.debug(f"Found {len(listings)} potential items for '{query_name}'.")

    items_processed = 0
    for item_idx, item in enumerate(listings):
        items_processed += 1
        title, listing_url, price_raw, desc, date_str = 'N/A', 'N/A', 'N/A', '', ''
        cond_txt, img_url, loc_txt = 'N/A', 'N/A', 'N/A';
        list_date, fmt_date = None, 'N/A';
        latitude, longitude = None, None

        try:
            # --- Extract Fields ---
            t_el = item.find('h3', class_='hz-Listing-title')
            title = t_el.text.strip() if t_el else 'N/A'

            # --- URL Extraction ---
            # Prefer the cover link first, then look for title link, then any link within wrapper
            url_el = None
            url_meth = "None"
            cov_link = item.find('a', class_='hz-Listing-coverLink', href=True)
            if cov_link:
                url_el = cov_link
                url_meth = "Cover"
            elif t_el and t_el.find('a', href=True): # Check if title itself is a link
                url_el = t_el.find('a', href=True)
                url_meth = "Title"
            else: # Fallback to any link inside the main item wrapper if specific ones fail
                 wrapper = item.find(['div','article'], class_=re.compile(r'hz-Listing-item-wrapper|hz-Listing-card'))
                 if wrapper:
                     first_link = wrapper.find('a', href=True)
                     if first_link:
                         url_el = first_link
                         url_meth = "WrapperFirst"

            href = None
            if url_el: href = url_el.get('href')

            # --- Clean and Complete URL ---
            if href:
                # Remove tracking parameters (example: ?previousPage=...)
                parsed_href = urlparse(href)
                cleaned_href = urlunparse((parsed_href.scheme, parsed_href.netloc, parsed_href.path, '', '', ''))
                if cleaned_href.startswith('http'):
                    listing_url = cleaned_href
                elif cleaned_href.startswith('/'):
                    # Use urljoin for robustness
                    listing_url = urljoin('https://www.marktplaats.nl/', cleaned_href.lstrip('/'))
                else:
                    listing_url = 'N/A' # Unexpected format
            else:
                listing_url = 'N/A'

            if listing_url == 'N/A':
                logging.warning(f"[Query:'{query_name}'|Item:{item_idx+1}] No valid URL found (Method:{url_meth}). Skipping.")
                continue
            if title == 'N/A' or not title:
                logging.warning(f"[Query:'{query_name}'|Item:{item_idx+1}] No title found. Skipping.")
                continue

            # --- Extract Other Fields ---
            p_el = item.find('span', class_='hz-Listing-price')
            price_raw = p_el.text.strip().replace('\xa0', ' ') if p_el else 'N/A' # Handle non-breaking space

            # Improved Date finding
            d_el = item.find('span', class_='hz-Listing-date')
            if not d_el: # Try more general time elements or text patterns
                 d_el = item.find(['span', 'div', 'time'], string=re.compile(
                     r'Vandaag|Gisteren|\d+\s+uur|\d+\s+min|\d+\s+dag|\d{1,2}\s+[a-z]{3}', re.I))
            if d_el:
                date_str = d_el.text.strip()
                list_date = parse_relative_date(date_str)
                fmt_date = format_date_nicely(list_date) if list_date else date_str
            else:
                date_str, fmt_date = 'N/A', 'N/A'
                logging.debug(f"[Query:'{query_name}'|Item:{item_idx+1}|URL:{listing_url}] Date string not found.")

            desc_el = item.find('span', class_='hz-Listing-description-value') or \
                      item.find('p', class_='hz-Listing-description')
            desc = desc_el.text.strip() if desc_el else ""

            cond_txt = extract_condition(item) or 'N/A'

            # Image URL - handle figure/picture elements and lazy loading
            img_url = 'N/A'
            img_cont = item.find('figure', class_='hz-Listing-image-container') or \
                       item.find('div', class_='hz-Listing-image') # Alternative container
            if img_cont:
                img_tag = img_cont.find('img')
                if img_tag:
                    # Prefer 'src', fallback to 'data-src'
                    img_url = img_tag.get('src') or img_tag.get('data-src', 'N/A')
                else: # Check for <picture><source srcset="..."><img ...></picture>
                    source_tag = img_cont.find('source', srcset=True)
                    if source_tag:
                        # Basic extraction: take the first URL from srcset
                        srcset = source_tag['srcset']
                        img_url = srcset.split(',')[0].split(' ')[0] # Get first URL part

            # Clean up image URL
            if img_url and isinstance(img_url, str):
                if img_url.startswith('//'):
                    img_url = 'https:' + img_url
                elif not img_url.startswith('http'):
                    img_url = 'N/A' # Reset if it's relative/invalid after checks
            else:
                 img_url = 'N/A' # Ensure it's 'N/A' if not found or not string

            # Location finding - be more specific
            loc_cont = item.find('span', class_='hz-Listing-location-area') or \
                       item.find('div', class_='hz-Listing-location') or \
                       item.find('span', class_='hz-Listing-location') # Broader fallback
            if loc_cont:
                loc_txt = loc_cont.get_text(strip=True, separator=' ').replace('Afstand', '').strip() or 'N/A'
            else: loc_txt = 'N/A'

            # --- Geocoding Block ---
            if loc_txt and loc_txt != 'N/A':
                # Basic cleaning: remove things like "(Heel Nederland)", postal code patterns often included
                clean_loc = re.sub(r'\(\s*[\w\s]+\s*\)\s*$', '', loc_txt).strip() # Remove trailing (...)
                clean_loc = re.sub(r'^\d{4}\s*[A-Z]{2}\s*', '', clean_loc).strip() # Remove leading postcode
                if clean_loc and clean_loc.lower() != 'heel nederland':
                    logging.debug(f"Attempting to geocode clean location: '{clean_loc}' (Original: '{loc_txt}')")
                    try:
                        # Add ", Netherlands" for better accuracy
                        location_geo = geocode(f"{clean_loc}, Netherlands", timeout=10)
                    except Exception as geo_e:
                        logging.warning(f"Geocoding error for '{clean_loc}': {geo_e}")
                        location_geo = None
                    if location_geo:
                        latitude = location_geo.latitude
                        longitude = location_geo.longitude
                        logging.debug(f"Geocoded '{clean_loc}' to: {latitude}, {longitude}")
                    else:
                        logging.debug(f"Geocoding failed for '{clean_loc}'.")
                elif clean_loc.lower() == 'heel nederland':
                    logging.debug("Skipping geocoding for 'Heel Nederland'.")
                else:
                    logging.debug(f"Skipping geocoding for empty/invalid clean location from: '{loc_txt}'")

            # --- Filtering Logic ---
            num_p = parse_price(price_raw) # Numeric price for filtering

            # 1. Check Duplicate (already includes hidden ones)
            if is_duplicate(listing_url):
                logging.debug(f"[It:{item_idx+1}|URL:{listing_url}] Skip(Duplicate in DB)")
                continue

            # 2. Check Sponsored (more robust checks)
            is_unwanted_sponsor = False
            # Check for specific "Topadvertentie" text within common sponsor indicators
            sponsor_indicators = item.select('span[class*="hz-Listing-priority-product"], div[class*="hz-Listing-priority"]')
            for indicator in sponsor_indicators:
                if "Topadvertentie" in indicator.get_text(strip=True):
                    is_unwanted_sponsor = True
                    break
            # Also check common parent classes often used for sponsored items
            if not is_unwanted_sponsor:
                if item.find_parent(class_=lambda c: c and ('Listing-cas' in c or 'mp-Listing--cas' in c)):
                     is_unwanted_sponsor = True # Found parent suggesting sponsored

            if is_unwanted_sponsor:
                logging.info(f"[It:{item_idx+1}|URL:{listing_url}] Skip(Sponsored Ad Indicator Found)")
                continue

            # Combine title and description for keyword checks
            comb_txt = (title + ' ' + desc).lower()

            # 3. Required Keywords
            if req_kws and not any(kw in comb_txt for kw in req_kws):
                logging.debug(f"[It:{item_idx+1}|URL:{listing_url}] Skip(Missing Required KW: {req_kws})")
                continue

            # 4. Excluded Keywords
            if excl_kws and any(kw in comb_txt for kw in excl_kws):
                # Find which keyword caused exclusion for better logging
                excluded_by = [kw for kw in excl_kws if kw in comb_txt]
                logging.debug(f"[It:{item_idx+1}|URL:{listing_url}] Skip(Found Excluded KW: {excluded_by})")
                continue

            # 5. Age Filter
            if max_age is not None and list_date and list_date < (date.today() - timedelta(days=max_age)):
                logging.debug(f"[It:{item_idx+1}|URL:{listing_url}] Skip(Too old: {fmt_date}, MaxAge: {max_age} days)")
                continue

            # 6. Price Filter
            is_non_p = is_non_priced(price_raw)
            price_filter_active = min_p is not None or max_p is not None

            if price_filter_active:
                if is_non_p:
                    if not incl_non_p: # If non-priced items should be excluded
                        logging.debug(f"[It:{item_idx+1}|URL:{listing_url}] Skip(Non-Priced Excluded)")
                        continue
                elif num_p is None: # If it's supposed to have a price but couldn't be parsed
                    logging.debug(f"[It:{item_idx+1}|URL:{listing_url}] Skip(Unparsable Price: '{price_raw}')")
                    continue
                else: # Has a valid numeric price
                    if min_p is not None and num_p < min_p:
                        logging.debug(f"[It:{item_idx+1}|URL:{listing_url}] Skip(Below Min Price: €{num_p} < €{min_p})")
                        continue
                    if max_p is not None and num_p > max_p:
                        logging.debug(f"[It:{item_idx+1}|URL:{listing_url}] Skip(Above Max Price: €{num_p} > €{max_p})")
                        continue

            # 7. Condition Filter
            if sel_conds: # Only apply if conditions are specified in the query
                 current_condition = cond_txt.lower() if cond_txt else 'n/a'
                 # Normalize "z.g.a.n" to "zo goed als nieuw" for matching
                 if current_condition == 'z.g.a.n': current_condition = 'zo goed als nieuw'
                 allowed_conditions = [c.lower() for c in sel_conds]
                 # Normalize allowed conditions as well
                 allowed_conditions = ['zo goed als nieuw' if c == 'z.g.a.n' else c for c in allowed_conditions]

                 if current_condition not in allowed_conditions and 'n/a' not in allowed_conditions: # Allow if condition couldn't be extracted and 'N/A' is acceptable
                     logging.debug(f"[It:{item_idx+1}|URL:{listing_url}] Skip(Condition '{cond_txt}' not in allowed: {sel_conds})")
                     continue

            # --- Listing Passed All Filters ---
            logging.info(f"NEW: '{title[:40]}'| P:{price_raw}| L:{loc_txt}| D:{fmt_date}| C:{cond_txt}")
            new_listings += 1

            # Add to DB (now includes lat/lon, hidden/favorite default to 0)
            add_listing_to_db(listing_url, title, price_raw, img_url, loc_txt, cond_txt, latitude, longitude)

            # Update GUI Treeview (if GUI is running)
            list_data = {
                "title": title, "price": price_raw, "date": fmt_date,
                "condition": cond_txt, "location": loc_txt, "url": listing_url,
                "image_url": img_url
            }
            if app_instance and hasattr(app_instance, 'add_result_to_treeview') and app_instance.master.winfo_exists():
                 app_instance.master.after(0, app_instance.add_result_to_treeview, list_data)

            # Format and Send Telegram Notification
            tg_query = html.escape(query_name or 'N/A'); tg_title = html.escape(title or 'N/A'); tg_price = html.escape(price_raw or 'N/A')
            tg_location = html.escape(loc_txt or 'N/A'); tg_date = html.escape(fmt_date or 'N/A'); tg_condition = html.escape(cond_txt or 'N/A')
            tg_url = html.escape(listing_url or 'N/A'); html_img_url = html.escape(img_url) if img_url != 'N/A' else None
            # Basic HTML formatting for Telegram
            tg_message = f"<b><u>Found Listing ({tg_query})</u></b>\n\n"
            tg_message += f"<b>Title:</b> {tg_title}\n"
            tg_message += f"<b>Price:</b> {tg_price}\n"
            tg_message += f"<b>Location:</b> {tg_location}\n"
            if tg_date != 'N/A': tg_message += f"<b>Date:</b> {tg_date}\n"
            if tg_condition != 'N/A': tg_message += f"<b>Condition:</b> {tg_condition}\n"
            tg_message += f"<b>URL:</b> <a href=\"{tg_url}\">View Listing</a>"
            if html_img_url: tg_message += f" | <a href=\"{html_img_url}\">Image</a>"

            # Send in a separate thread to avoid blocking
            threading.Thread(target=send_telegram_notification, args=(tg_message,), daemon=True).start()

        except Exception as e:
            logging.error(f"Error Processing Item {item_idx+1} ('{title[:30]}...' URL: {listing_url}): {e}", exc_info=is_debug) # Show traceback if debug is on
            continue # Skip to the next item

    logging.info(f"Finished processing {items_processed} items for query '{query_name}'. Found {new_listings} new matching listings.")
    return new_listings
# --- End of scrape_and_process function ---

# --- gets ALL listings EVer --- 
def get_all_listings_from_db(limit=None):
    """Fetches ALL listings from the database, including hidden ones, sorted by date."""
    conn = None
    db_path_local = DEFAULT_DB_PATH
    if not db_path_local:
        logging.error("Database path not configured for get_all_listings.")
        return []
    listings_data = []
    try:
         conn = sqlite3.connect(db_path_local)
         conn.row_factory = sqlite3.Row # Return rows that behave like dicts
         cursor = conn.cursor()
         # Select all columns, including hidden/favorite, order by date
         sql_query = """
             SELECT url, title, scraped_at, price, image_url, location, condition, latitude, longitude, favorite, hidden
             FROM listings
             ORDER BY scraped_at DESC
         """
         params = []
         if limit and isinstance(limit, int) and limit > 0:
            sql_query += " LIMIT ?"
            params.append(limit)

         cursor.execute(sql_query, params)
         listings = cursor.fetchall()
         logging.info(f"Fetched {len(listings)} total listings (including hidden) from DB.")
         # Convert Row objects to simple dictionaries
         for row in listings:
             listings_data.append(dict(row))
         return listings_data
    except sqlite3.Error as e:
         logging.error(f"Database error fetching all listings from '{db_path_local}': {e}")
         return []
    except Exception as e:
         logging.error(f"Unexpected error fetching all listings: {e}")
         return []
    finally:
         if conn:
             conn.close()


# --- GUI Application Class 
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
        self.check_interval_ms = 5000 # Check for trigger file every 5s

        # Initialize status file
        self.update_status_file("idle")

        self.current_check_interval = DEFAULT_CHECK_INTERVAL_MINUTES
        try:
            self.current_check_interval = config.getint('Scraper', 'CheckIntervalMinutes', fallback=DEFAULT_CHECK_INTERVAL_MINUTES)
            logging.debug(f"Initial check interval set to {self.current_check_interval} minutes from config.")
        except Exception as e:
            logging.warning(f"Using default check interval ({DEFAULT_CHECK_INTERVAL_MINUTES} min) due to config error: {e}")
            self.current_check_interval = DEFAULT_CHECK_INTERVAL_MINUTES


        # Tkinter Variables for Editor
        self.dark_mode_var = tk.BooleanVar()
        self.interval_var = tk.IntVar(value=self.current_check_interval)
        self.edit_query_id = None
        self.edit_name_var = tk.StringVar()
        self.edit_url_var = tk.StringVar()
        self.edit_postcode_var = tk.StringVar()
        self.edit_distance_var = tk.StringVar() # Added for KM input
        self.edit_active_var = tk.BooleanVar()
        self.edit_min_price_var = tk.StringVar()
        self.edit_max_price_var = tk.StringVar()
        self.edit_include_non_priced_var = tk.BooleanVar()
        self.edit_max_age_var = tk.IntVar()
        self.edit_condition_vars = {
            "nieuw": tk.BooleanVar(),
            "zo goed als nieuw": tk.BooleanVar(), # Keep full name internally
            "gebruikt": tk.BooleanVar()
            # Add 'refurbished' if needed: "refurbished": tk.BooleanVar()
            }

        # UI Setup
        self.style = ttk.Style(master); self.setup_styles()
        dm_enabled = config.getboolean('GUIState', 'DarkMode', fallback=False); self.dark_mode_var.set(dm_enabled)

        self.notebook = ttk.Notebook(master, padding=10); self.notebook.pack(pady=10, padx=10, fill=tk.BOTH, expand=True)
        self.tab_run_log = ttk.Frame(self.notebook, padding="10", style='TFrame'); self.notebook.add(self.tab_run_log, text='Run & Logs')
        self.tab_results = ttk.Frame(self.notebook, padding="10", style='TFrame'); self.notebook.add(self.tab_results, text='Results')
        self.tab_query_management = ttk.Frame(self.notebook, padding="10", style='TFrame'); self.notebook.add(self.tab_query_management, text='Query Management')

        self.create_run_log_tab(); self.create_results_tab(); self.create_query_management_tab()

        self.setup_logging() # Setup logging handlers (incl. GUI)
        self.apply_theme() # Apply initial theme

        # Start trigger file check loop
        master.after(self.check_interval_ms, self.check_for_trigger_file)
        master.protocol("WM_DELETE_WINDOW", self.on_closing)
        logging.info(f"GUI Initialized. Loaded {len(self.queries)} queries.")

    # --- Styling Methods ---
    def setup_styles(self):
        self.style = ttk.Style() # Initialize without master argument
        theme = 'vista' if os.name == 'nt' else 'clam';
        try: self.style.theme_use(theme)
        except tk.TclError: self.style.theme_use('default') # Fallback theme
        # Basic style configurations
        self.style.configure('.', padding=(5, 5), font=('Segoe UI', 9))
        self.style.configure('TFrame', background='SystemButtonFace')
        self.style.configure('TButton', padding=(8, 5), font=('Segoe UI', 9))
        self.style.configure('TNotebook.Tab', padding=(10, 5), font=('Segoe UI', 10, 'bold'))
        self.style.configure('TLabelframe', padding=(5, 5))
        self.style.configure('TLabelframe.Label', padding=(5, 2), font=('Segoe UI', 9, 'bold'))
        self.style.configure('Treeview.Heading', font=('Segoe UI', 10, 'bold'))
        self.style.configure("Treeview", rowheight=25) # Adjust row height if needed
        self.style.map("Treeview", background=[('selected', '#0078D7')], foreground=[('selected', 'white')])

    def apply_theme(self):
        mode = 'dark' if self.dark_mode_var.get() else 'light'; logging.debug(f"Applying {mode} theme.")
        # Color palettes (can be customized further)
        colors={'dark':{'bg':'#2E2E2E','fg':'#EAEAEA','entry_bg':'#3C3C3C','entry_fg':'#EAEAEA','button_bg':'#4A4A4A','button_fg':'#EAEAEA','button_active':'#5F5F5F','notebook_bg':'#2E2E2E','tab_bg':'#4A4A4A','tab_fg':'#EAEAEA','tab_active_bg':'#5F5F5F','tab_inactive_bg':'#3C3C3C','tree_bg':'#2E2E2E','tree_fg':'#EAEAEA','tree_heading_bg':'#4A4A4A','tree_selected':'#005A9E','text_bg':'#1E1E1E','text_fg':'#DCDCDC','text_insert':'#EAEAEA','label_fg':'#EAEAEA','labelframe_fg':'#CCCCCC','frame_bg':'#2E2E2E','listbox_bg':'#3C3C3C','listbox_fg':'#EAEAEA','listbox_select_bg':'#005A9E','listbox_select_fg':'white','log_info':'#EAEAEA','log_debug':'#AAAAAA','log_found':'#77DD77','log_dup':'#C8A2C8','log_warn':'#FFB347','log_err':'#FF6961','log_crit':'#FF6961','log_sponsor':'#ADD8E6'},
        'light':{'bg':'SystemButtonFace','fg':'SystemWindowText','entry_bg':'SystemWindow','entry_fg':'SystemWindowText','button_bg':'SystemButtonFace','button_fg':'SystemWindowText','button_active':'SystemButtonFace','notebook_bg':'SystemButtonFace','tab_bg':'SystemButtonFace','tab_fg':'SystemWindowText','tab_active_bg':'SystemHighlight','tab_inactive_bg':'SystemButtonFace','tree_bg':'SystemWindow','tree_fg':'SystemWindowText','tree_heading_bg':'SystemButtonFace','tree_selected':'SystemHighlight','text_bg':'SystemWindow','text_fg':'SystemWindowText','text_insert':'SystemWindowText','label_fg':'SystemWindowText','labelframe_fg':'SystemWindowText','frame_bg':'SystemButtonFace','listbox_bg':'SystemWindow','listbox_fg':'SystemWindowText','listbox_select_bg':'SystemHighlight','listbox_select_fg':'SystemHighlightText','log_info':'black','log_debug':'grey','log_found':'green','log_dup':'purple','log_warn':'orange','log_err':'red','log_crit':'red','log_sponsor':'blue'}}
        c = colors[mode]
        try:
            # Attempt to set a base theme - 'clam' is often better for cross-platform customisation
            base_theme = 'clam';
            if mode == 'light': base_theme = 'vista' if os.name == 'nt' else 'clam' # Keep Vista for light on Win if preferred
            self.style.theme_use(base_theme)

            # Configure standard widget styles
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

            # Configure Treeview styles (using unique style names is safer)
            for tree_attr, style_prefix in [('query_tree', 'QueryTree'), ('results_tree', 'ResultsTree')]:
                 if hasattr(self, tree_attr) and getattr(self, tree_attr):
                      tree = getattr(self, tree_attr); style_name = f"{style_prefix}.Treeview"
                      tree.configure(style=style_name) # Ensure the treeview uses this specific style
                      self.style.configure(style_name, background=c['tree_bg'], fieldbackground=c['tree_bg'], foreground=c['tree_fg'], rowheight=25) # Added rowheight here too
                      self.style.configure(f"{style_name}.Heading", background=c['tree_heading_bg'], foreground=c['fg'], font=('Segoe UI', 10, 'bold')) # Apply heading font here
                      self.style.map(style_name, background=[('selected', c['tree_selected'])], foreground=[('selected', c['listbox_select_fg'])])

            # --- Apply Log Tag Colors AND Fonts ---
            # Define fonts
            log_font_normal = ("Consolas", 9) # Or ("Courier New", 9)
            log_font_bold = (*log_font_normal, "bold")

            for text_attr in ['debug_log_area', 'found_log_area']:
                 if hasattr(self, text_attr) and getattr(self, text_attr):
                     widget = getattr(self, text_attr)
                     widget.config(background=c['text_bg'], foreground=c['text_fg'], insertbackground=c['text_insert'])
                     # Apply color AND font tags
                     widget.tag_config(TAG_INFO, foreground=c['log_info'], font=log_font_normal)
                     widget.tag_config(TAG_DEBUG, foreground=c['log_debug'], font=log_font_normal)
                     widget.tag_config(TAG_FOUND, foreground=c['log_found'], font=log_font_bold) # Bold for found
                     widget.tag_config(TAG_DUPLICATE, foreground=c['log_dup'], font=log_font_normal)
                     widget.tag_config(TAG_SPONSORED, foreground=c['log_sponsor'], font=log_font_normal)
                     widget.tag_config(TAG_WARNING, foreground=c['log_warn'], font=log_font_normal)
                     widget.tag_config(TAG_ERROR, foreground=c['log_err'], font=log_font_normal)
                     widget.tag_config(TAG_CRITICAL, foreground=c['log_crit'], font=log_font_bold) # Bold for critical

            # Style editor text boxes (use ScrolledText style if possible, or config)
            editor_font = ('Segoe UI', 9)
            for text_attr in ['edit_req_keywords_text', 'edit_excl_keywords_text']:
                 if hasattr(self, text_attr) and getattr(self, text_attr):
                      widget = getattr(self, text_attr)
                      widget.config(background=c['text_bg'], foreground=c['text_fg'], insertbackground=c['text_insert'], font=editor_font)
                      # Attempt to style ScrolledText border, might be tricky
                      widget.config(borderwidth=1, relief=tk.SUNKEN)


            # Style Spinboxes explicitly (as they are standard Tk widgets)
            # Need to handle potential errors if widgets don't exist yet
            try:
                 if hasattr(self, 'interval_spinbox'):
                     self.interval_spinbox.config(background=c['entry_bg'], foreground=c['entry_fg'],buttonbackground=c['bg']) # Tk style
            except tk.TclError as e: logging.warning(f"Style error interval_spinbox: {e}")
            try:
                 if hasattr(self, 'edit_max_age_spinbox'):
                     self.edit_max_age_spinbox.config(background=c['entry_bg'], foreground=c['entry_fg'],buttonbackground=c['bg']) # Tk style
            except tk.TclError as e: logging.warning(f"Style error max_age_spinbox: {e}")

            # Configure the master window background
            self.master.config(bg=c['bg'])
            # Ensure Notebook tabs inherit background correctly
            if hasattr(self, 'tab_run_log'): self.tab_run_log.configure(style='TFrame')
            if hasattr(self, 'tab_results'): self.tab_results.configure(style='TFrame')
            if hasattr(self, 'tab_query_management'): self.tab_query_management.configure(style='TFrame')

        except tk.TclError as e: logging.error(f"Theme application TclError: {e}")
        except Exception as e: logging.error(f"Unexpected error applying theme: {e}")

    def toggle_theme(self):
        self.apply_theme()
        # Save theme preference to config.ini
        try:
            global config; config.set('GUIState', 'DarkMode', str(self.dark_mode_var.get()))
            with open(CONFIG_FILE, 'w') as cf: config.write(cf)
            logging.debug(f"Dark mode setting ({self.dark_mode_var.get()}) saved to {CONFIG_FILE}")
        except Exception as e: logging.error(f"Failed save dark mode setting: {e}")

    def save_general_settings_to_config(self):
        global config; logging.info(f"Saving general settings to {CONFIG_FILE}...")
        try:
            if not config.has_section('Scraper'): config.add_section('Scraper')
            if not config.has_section('GUIState'): config.add_section('GUIState')

            # Get interval, ensuring it's valid before saving
            try:
                interval_to_save = self.interval_var.get()
                if interval_to_save < 1: interval_to_save = DEFAULT_CHECK_INTERVAL_MINUTES
            except:
                interval_to_save = DEFAULT_CHECK_INTERVAL_MINUTES
                logging.warning("Invalid interval in var, saving default.")
            config.set('Scraper', 'CheckIntervalMinutes', str(interval_to_save))

            config.set('GUIState', 'DarkMode', str(self.dark_mode_var.get()))

            # Remove old/unused GUIState keys if necessary (optional)
            # valid_guistate_keys = ['darkmode']
            # if config.has_section('GUIState'):
            #     for key in list(config['GUIState']):
            #          if key.lower() not in valid_guistate_keys:
            #              config.remove_option('GUIState', key)

            with open(CONFIG_FILE, 'w') as cf: config.write(cf)
            logging.info("General settings saved successfully.")
        except Exception as e: logging.error(f"Failed save general settings: {e}")

    # --- File Check / Status Update Methods ---
    def check_for_trigger_file(self):
        """Checks if the trigger file exists and starts a scan if needed."""
        if self.running: # Check the instance's running flag
            # logging.debug("Trigger check skipped: Scan already running.")
            pass # Don't log every 5s, too noisy
        else:
            # If not scanning, check for the file
            trigger_exists = False
            try:
                if os.path.exists(self.trigger_file_path):
                    trigger_exists = True
                    logging.info(f"Trigger file '{self.trigger_file_path}' detected.")
                    # Attempt to remove the file FIRST
                    try:
                        os.remove(self.trigger_file_path)
                        logging.info(f"Trigger file '{self.trigger_file_path}' removed.")
                    except OSError as e:
                        logging.error(f"Could not remove trigger file {self.trigger_file_path}: {e}. Scan will not start.")
                        trigger_exists = False # Prevent starting scan if removal failed

            except Exception as e:
                logging.error(f"Error during trigger file check: {e}")
                trigger_exists = False

            # If file existed and was successfully removed, try starting scan
            if trigger_exists:
                logging.info("Attempting to start scan triggered by file.")
                # Use master.after to ensure start_scan runs in the main Tk thread
                self.master.after(50, self.start_scan) # Short delay before starting

        # Always reschedule the check if the window still exists
        if hasattr(self.master, 'after') and self.master.winfo_exists():
            self.master.after(self.check_interval_ms, self.check_for_trigger_file)

    def update_status_file(self, status):
        """Writes the current scan status to a file for Flask to read."""
        try:
            with open(self.status_file_path, 'w') as f:
                f.write(status)
            # logging.debug(f"Updated status file to: {status}") # Can be noisy
        except Exception as e:
            logging.error(f"Could not write to status file {self.status_file_path}: {e}")

    # --- Tab Creation ---
    def create_run_log_tab(self):
        # Frame for controls (Start/Stop Button, Interval, Dark Mode)
        controls_frame = ttk.Frame(self.tab_run_log, style='TFrame')
        controls_frame.pack(side=tk.TOP, fill=tk.X, pady=(0, 10))

        self.start_button = ttk.Button(controls_frame, text="Start Scan", command=self.start_scan, width=12)
        self.start_button.pack(side=tk.LEFT, padx=5, pady=5)

        self.stop_button = ttk.Button(controls_frame, text="Stop Scan", command=self.stop_scan, state=tk.DISABLED, width=12)
        self.stop_button.pack(side=tk.LEFT, padx=5, pady=5)

        ttk.Label(controls_frame, text="Interval(min):").pack(side=tk.LEFT, padx=(15, 5), pady=5)
        # Use standard Tk Spinbox as ttk.Spinbox styling is limited/buggy
        self.interval_spinbox = Spinbox(controls_frame, from_=1, to=1440, width=5, textvariable=self.interval_var, command=self.update_interval_from_spinbox, state=tk.NORMAL, bd=1, relief=tk.SUNKEN) # Added border
        self.interval_spinbox.pack(side=tk.LEFT, pady=5, ipady=2) # Internal padding

        ttk.Checkbutton(controls_frame, text="Dark Mode", variable=self.dark_mode_var, command=self.toggle_theme).pack(side=tk.LEFT, padx=(15, 5), pady=5)

        # Frame for "Found Listings" Log
        found_frame = ttk.LabelFrame(self.tab_run_log, text="Found Listings Log", style='TLabelframe')
        found_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=False, pady=(10, 5), padx=5) # Don't expand vertically much

        self.found_log_area = scrolledtext.ScrolledText(found_frame, state=tk.DISABLED, height=6, wrap=tk.WORD, font=("Consolas", 9), bd=0)
        self.found_log_area.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Frame for "Debug Console" Log
        debug_frame = ttk.LabelFrame(self.tab_run_log, text="Debug Console", style='TLabelframe')
        debug_frame.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=True, pady=(5, 5), padx=5) # Expand more

        self.debug_log_area = scrolledtext.ScrolledText(debug_frame, state=tk.DISABLED, height=15, wrap=tk.WORD, font=("Consolas", 9), bd=0)
        self.debug_log_area.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)


    def create_results_tab(self):
        results_frame=ttk.Frame(self.tab_results, style='TFrame'); results_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Define columns (internal names)
        columns = ("title", "price", "location", "date", "condition", "url", "image_url")
        self.results_tree = ttk.Treeview(results_frame, columns=columns, show='headings', height=20, style="ResultsTree.Treeview") # Use specific style

        # --- Setup headings and column properties ---
        # Title (wider, stretches)
        self.results_tree.heading("title", text="Title", anchor=tk.W)
        self.results_tree.column("title", width=280, stretch=tk.YES, anchor=tk.W)
        # Price (fixed width)
        self.results_tree.heading("price", text="Price", anchor=tk.W)
        self.results_tree.column("price", width=80, stretch=tk.NO, anchor=tk.W)
        # Location (stretches)
        self.results_tree.heading("location", text="Location", anchor=tk.W)
        self.results_tree.column("location", width=150, stretch=tk.YES, anchor=tk.W)
        # Date (fixed width)
        self.results_tree.heading("date", text="Date Found", anchor=tk.W)
        self.results_tree.column("date", width=120, stretch=tk.NO, anchor=tk.W)
        # Condition (fixed width)
        self.results_tree.heading("condition", text="Condition", anchor=tk.W)
        self.results_tree.column("condition", width=100, stretch=tk.NO, anchor=tk.W)
        # URL (stretches)
        self.results_tree.heading("url", text="Listing URL", anchor=tk.W)
        self.results_tree.column("url", width=180, stretch=tk.YES, anchor=tk.W)
        # Image URL (stretches)
        self.results_tree.heading("image_url", text="Image URL", anchor=tk.W)
        self.results_tree.column("image_url", width=180, stretch=tk.YES, anchor=tk.W)

        # --- Scrollbars ---
        vsb = ttk.Scrollbar(results_frame, orient=VERTICAL, command=self.results_tree.yview)
        hsb = ttk.Scrollbar(results_frame, orient=HORIZONTAL, command=self.results_tree.xview)
        self.results_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        # --- Grid Layout ---
        self.results_tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        # Configure resizing behavior
        results_frame.grid_rowconfigure(0, weight=1)
        results_frame.grid_columnconfigure(0, weight=1)

        # Clear Button
        ttk.Button(results_frame, text="Clear Results", command=self.clear_results_tree).grid(row=2, column=0, columnspan=2, pady=(10, 0))

        # Bind double-click event
        self.results_tree.bind("<Double-1>", self.on_treeview_double_click)

    # --- Query Management Tab ---
    def create_query_management_tab(self):
        paned_window = ttk.PanedWindow(self.tab_query_management, orient=tk.HORIZONTAL)
        paned_window.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # --- Left Pane: Query List ---
        query_list_frame = ttk.Frame(paned_window, padding=5, style='TFrame')
        paned_window.add(query_list_frame, weight=1) # Adjust weight as needed

        # Label for the list
        ttk.Label(query_list_frame, text="Saved Queries (Dbl-Click Active/Inactive):", style='TLabelframe.Label').grid(row=0, column=0, columnspan=2, sticky='w', pady=(0,5))

        # Treeview for query list
        tree_cols = ("active", "name", "url")
        self.query_tree = ttk.Treeview(query_list_frame, columns=tree_cols, show='headings', selectmode='browse', style="QueryTree.Treeview") # Use specific style

        # Configure Headings and Columns for Query List
        self.query_tree.heading("active", text="Active", anchor=tk.CENTER)
        self.query_tree.column("active", width=50, stretch=False, anchor=tk.CENTER) # Fixed width, centered
        self.query_tree.heading("name", text="Query Name", anchor=tk.W)
        self.query_tree.column("name", width=150, stretch=True, anchor=tk.W)
        self.query_tree.heading("url", text="Base URL", anchor=tk.W)
        self.query_tree.column("url", width=250, stretch=True, anchor=tk.W)

        # Scrollbars for Query List
        tree_scr_y = ttk.Scrollbar(query_list_frame, orient=tk.VERTICAL, command=self.query_tree.yview)
        tree_scr_x = ttk.Scrollbar(query_list_frame, orient=tk.HORIZONTAL, command=self.query_tree.xview)
        self.query_tree.configure(yscrollcommand=tree_scr_y.set, xscrollcommand=tree_scr_x.set)

        # Grid layout for Query List Treeview and Scrollbars
        self.query_tree.grid(row=1, column=0, sticky='nsew')
        tree_scr_y.grid(row=1, column=1, sticky='ns')
        tree_scr_x.grid(row=2, column=0, sticky='ew')

        # Configure resizing for Query List Frame
        query_list_frame.grid_rowconfigure(1, weight=1)
        query_list_frame.grid_columnconfigure(0, weight=1)

        # Populate tree and bind events
        self.populate_query_tree()
        self.query_tree.bind("<<TreeviewSelect>>", self.on_query_selected)
        self.query_tree.bind("<Double-1>", self.toggle_query_active)

        # --- Right Pane: Query Editor ---
        self.query_detail_frame = ttk.LabelFrame(paned_window, text="Edit Selected Query", padding=10, style='TLabelframe')
        paned_window.add(self.query_detail_frame, weight=2) # Adjust weight as needed
        # Configure column weights for the editor frame
        self.query_detail_frame.columnconfigure(1, weight=1) # Make entry column expandable

        current_row = 0
        # --- Editor Fields ---
        # Row 0: Name & Active Checkbox
        ttk.Label(self.query_detail_frame, text="Name:").grid(row=current_row, column=0, sticky=tk.W, padx=5, pady=3)
        name_entry = ttk.Entry(self.query_detail_frame, textvariable=self.edit_name_var)
        name_entry.grid(row=current_row, column=1, sticky=tk.EW, padx=5, pady=3)
        ttk.Checkbutton(self.query_detail_frame, text="Scan Active", variable=self.edit_active_var).grid(row=current_row, column=2, sticky=tk.W, padx=10, pady=3)
        current_row += 1

        # Row 1: Base URL Entry
        ttk.Label(self.query_detail_frame, text="Base URL:").grid(row=current_row, column=0, sticky=tk.W, padx=5, pady=3)
        ttk.Entry(self.query_detail_frame, textvariable=self.edit_url_var).grid(row=current_row, column=1, columnspan=2, sticky=tk.EW, padx=5, pady=3)
        current_row += 1

        # Row 2: Postcode & Distance Frame
        loc_frame = ttk.Frame(self.query_detail_frame, style='TFrame')
        loc_frame.grid(row=current_row, column=0, columnspan=3, sticky=tk.EW, pady=(5, 4)) # Added top padding
        ttk.Label(loc_frame, text="Postcode:").pack(side=tk.LEFT, padx=(5,2))
        ttk.Entry(loc_frame, textvariable=self.edit_postcode_var, width=10).pack(side=tk.LEFT, padx=(0,10))
        ttk.Label(loc_frame, text="Distance (km):").pack(side=tk.LEFT, padx=(10,2))
        ttk.Entry(loc_frame, textvariable=self.edit_distance_var, width=8).pack(side=tk.LEFT, padx=(0,5)) # For KM input
        current_row += 1

        # Row 3: Price Range & Non-Priced Frame
        p_frame = ttk.Frame(self.query_detail_frame, style='TFrame'); p_frame.grid(row=current_row, column=0, columnspan=3, sticky=tk.EW, pady=4)
        ttk.Label(p_frame, text="Min Price:").pack(side=tk.LEFT, padx=(5, 2)); ttk.Entry(p_frame, textvariable=self.edit_min_price_var, width=8).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(p_frame, text="Max Price:").pack(side=tk.LEFT, padx=(5, 2)); ttk.Entry(p_frame, textvariable=self.edit_max_price_var, width=8).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Checkbutton(p_frame, text="Incl.'Bieden'", variable=self.edit_include_non_priced_var).pack(side=tk.LEFT, padx=5)
        current_row += 1

        # Row 4: Condition Checkboxes Frame
        c_frame = ttk.Frame(self.query_detail_frame, style='TFrame'); c_frame.grid(row=current_row, column=0, columnspan=3, sticky=tk.EW, pady=4)
        ttk.Label(c_frame, text="Conditions:").pack(side=tk.LEFT, padx=5)
        # Use more readable text for checkboxes
        condition_display_map = {
            "nieuw": "New",
            "zo goed als nieuw": "Like New (ZGAN)",
            "gebruikt": "Used"
            # "refurbished": "Refurbished" # If added
        }
        for cond, var in self.edit_condition_vars.items():
             display_text = condition_display_map.get(cond, cond.capitalize()) # Get display text or capitalize
             ttk.Checkbutton(c_frame, text=display_text, variable=var).pack(side=tk.LEFT, padx=5)
        current_row += 1

        # Row 5: Max Age Spinbox Frame
        a_frame = ttk.Frame(self.query_detail_frame, style='TFrame'); a_frame.grid(row=current_row, column=0, columnspan=3, sticky=tk.EW, pady=4)
        ttk.Label(a_frame, text="Max Age(days):").pack(side=tk.LEFT, padx=5)
        self.edit_max_age_spinbox = Spinbox(a_frame, from_=1, to=365, width=5, textvariable=self.edit_max_age_var, state=tk.NORMAL, bd=1, relief=tk.SUNKEN) # Standard Tk Spinbox
        self.edit_max_age_spinbox.pack(side=tk.LEFT, padx=5, ipady=2)
        current_row += 1

        # Row 6: Required Keywords Text Area
        rq_frame = ttk.Frame(self.query_detail_frame, style='TFrame'); rq_frame.grid(row=current_row, column=0, columnspan=3, sticky=tk.NSEW, pady=(8, 4)); rq_frame.columnconfigure(0, weight=1)
        ttk.Label(rq_frame, text="Required KWs (one per line):").pack(anchor=tk.W, padx=5, pady=(0, 2))
        self.edit_req_keywords_text = scrolledtext.ScrolledText(rq_frame, height=4, width=40, wrap=tk.WORD, font=('Segoe UI', 9));
        self.edit_req_keywords_text.pack(fill=tk.BOTH, expand=True, padx=5)
        self.query_detail_frame.rowconfigure(current_row, weight=1) # Allow expansion
        current_row += 1

        # Row 7: Excluded Keywords Text Area
        ex_frame = ttk.Frame(self.query_detail_frame, style='TFrame'); ex_frame.grid(row=current_row, column=0, columnspan=3, sticky=tk.NSEW, pady=4); ex_frame.columnconfigure(0, weight=1)
        ttk.Label(ex_frame, text="Excluded KWs (one per line):").pack(anchor=tk.W, padx=5, pady=(0, 2))
        self.edit_excl_keywords_text = scrolledtext.ScrolledText(ex_frame, height=4, width=40, wrap=tk.WORD, font=('Segoe UI', 9));
        self.edit_excl_keywords_text.pack(fill=tk.BOTH, expand=True, padx=5)
        self.query_detail_frame.rowconfigure(current_row, weight=1) # Allow expansion
        current_row += 1

        # --- Action Buttons Frame (Below PanedWindow) ---
        act_frame = ttk.Frame(self.tab_query_management, padding=(0, 10, 0, 0), style='TFrame')
        act_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(10,0)) # Add padding above buttons

        self.add_query_button = ttk.Button(act_frame, text="Add New", command=self.add_new_query_action)
        self.add_query_button.pack(side=tk.LEFT, padx=5)

        self.save_query_button = ttk.Button(act_frame, text="Save Changes", command=self.save_query_action, state=tk.DISABLED) # Changed text
        self.save_query_button.pack(side=tk.LEFT, padx=5)

        self.delete_query_button = ttk.Button(act_frame, text="Delete Selected", command=self.delete_query_action, state=tk.DISABLED) # Changed text
        self.delete_query_button.pack(side=tk.RIGHT, padx=5) # Keep delete on the right

        self.apply_theme() # Apply theme after all widgets are created
    # --- Methods for Query Management GUI ---

    def populate_query_tree(self):
        if not hasattr(self, 'query_tree'): return
        # Remember selection
        sel_iid = self.query_tree.focus()
        # Clear existing items
        for item in self.query_tree.get_children():
            try: self.query_tree.delete(item)
            except tk.TclError: pass # Ignore if item already gone

        # Sort queries alphabetically by name for display
        sorted_queries = sorted(self.queries, key=lambda q: q.get('name', '').lower())

        # Insert items
        for index, query_dict in enumerate(sorted_queries):
            # Ensure every query has a UUID-based ID
            query_id = query_dict.get("id") or f"query_{uuid.uuid4()}"
            query_dict["id"] = query_id # Store back the generated ID if it was missing

            name = query_dict.get("name", f"Query {index+1}")
            url = query_dict.get("url", "N/A")
            active_status = "✓" if query_dict.get("active", False) else "✗" # Checkmark or X for active status

            try:
                # Use the query_id as the item identifier (iid)
                self.query_tree.insert("", tk.END, iid=query_id, values=(active_status, name, url))
            except tk.TclError as e:
                logging.error(f"Error inserting item into query tree (ID:{query_id}, Name:'{name}'): {e}")

        # Restore selection if possible
        if sel_iid and self.query_tree.exists(sel_iid):
            try:
                 self.query_tree.focus(sel_iid)
                 self.query_tree.selection_set(sel_iid)
            except tk.TclError: pass # Ignore if selection fails

        logging.debug(f"Populated query tree with {len(sorted_queries)} items.")

    def clear_detail_frame(self):
        logging.debug("Clearing query detail frame.")
        self.edit_query_id = None # No query selected/being edited

        # Clear basic fields
        self.edit_name_var.set("")
        self.edit_url_var.set("")
        self.edit_postcode_var.set("")
        self.edit_distance_var.set("") # Clear distance (KM input)
        self.edit_active_var.set(True) # Default new queries to active
        self.edit_min_price_var.set("")
        self.edit_max_price_var.set("")

        # Set fields based on defaults defined at the top
        self.edit_include_non_priced_var.set(DEFAULT_QUERY_SETTINGS.get("include_non_priced", True))
        try:
            # Use try-except for Spinbox variable setting
            default_age = DEFAULT_QUERY_SETTINGS.get("max_age_days", 7)
            self.edit_max_age_var.set(default_age)
        except (tk.TclError, ValueError):
             self.edit_max_age_var.set(7) # Fallback

        default_conds = DEFAULT_QUERY_SETTINGS.get("conditions", [])
        for cond, var in self.edit_condition_vars.items():
            var.set(cond in default_conds)

        # Clear text areas safely
        if hasattr(self,'edit_req_keywords_text') and self.edit_req_keywords_text.winfo_exists():
             self.edit_req_keywords_text.delete('1.0', tk.END)
        if hasattr(self,'edit_excl_keywords_text') and self.edit_excl_keywords_text.winfo_exists():
             self.edit_excl_keywords_text.delete('1.0', tk.END)

        # Reset button states for "add new" mode
        if hasattr(self,'save_query_button'): self.save_query_button.config(state=tk.NORMAL, text="Save New Query") # Enable save, update text
        if hasattr(self,'delete_query_button'): self.delete_query_button.config(state=tk.DISABLED) # Disable delete

        # Optionally set focus to the name field
        # if hasattr(self, 'query_detail_frame'):
        #     # Find the name entry widget to set focus
        #     for child in self.query_detail_frame.winfo_children():
        #         if isinstance(child, ttk.Entry) and child.cget('textvariable') == str(self.edit_name_var):
        #             child.focus_set()
        #             break


    def on_query_selected(self, event=None):
        selected_item_ids = self.query_tree.selection()
        if not selected_item_ids:
            # If selection is cleared (e.g., clicking empty space), perhaps clear the form? Optional.
            # self.clear_detail_frame() # Uncomment this line if you want the form to clear when nothing is selected
            logging.debug("Query selection cleared.")
            # Ensure buttons are disabled if nothing is selected
            if hasattr(self,'save_query_button'): self.save_query_button.config(state=tk.DISABLED, text="Save Changes")
            if hasattr(self,'delete_query_button'): self.delete_query_button.config(state=tk.DISABLED)
            return

        selected_item_id = selected_item_ids[0] # Get the first (and usually only) selected item ID
        logging.debug(f"Query selected in tree: {selected_item_id}")

        # Find the corresponding query dictionary from our self.queries list
        selected_query_dict = next((q for q in self.queries if q.get("id") == selected_item_id), None)

        if selected_query_dict:
            self.edit_query_id = selected_item_id # Store the ID of the query being edited

            # Populate basic fields
            self.edit_name_var.set(selected_query_dict.get("name", ""))
            self.edit_url_var.set(selected_query_dict.get("url", "")) # Base URL
            self.edit_active_var.set(selected_query_dict.get("active", True)) # Default to True if missing
            self.edit_postcode_var.set(selected_query_dict.get("postcode", "") or "") # Handle None

            # Load distance (convert meters stored to KM for display)
            distance_m = selected_query_dict.get("distanceMeters")
            distance_km_str = ""
            if distance_m is not None:
                try: distance_km_str = str(int(distance_m) // 1000) # Integer division
                except (ValueError, TypeError): distance_km_str = "" # Handle potential non-numeric data
            self.edit_distance_var.set(distance_km_str) # Set KM value in the entry

            # Populate price fields (handle None)
            min_p = selected_query_dict.get("min_price")
            self.edit_min_price_var.set(str(min_p) if min_p is not None else "")
            max_p = selected_query_dict.get("max_price")
            self.edit_max_price_var.set(str(max_p) if max_p is not None else "")

            self.edit_include_non_priced_var.set(selected_query_dict.get("include_non_priced", True))

            # Populate max age (handle potential errors/missing data)
            try: self.edit_max_age_var.set(selected_query_dict.get("max_age_days", 7))
            except (ValueError, TypeError, tk.TclError): self.edit_max_age_var.set(7) # Fallback

            # Populate condition checkboxes
            sel_conds = selected_query_dict.get("conditions", DEFAULT_QUERY_SETTINGS.get("conditions", []))
            for cond, var in self.edit_condition_vars.items():
                var.set(cond in sel_conds)

            # Populate keyword text areas
            req_kws = selected_query_dict.get("required_keywords", [])
            excl_kws = selected_query_dict.get("excluded_keywords", [])
            if hasattr(self,'edit_req_keywords_text') and self.edit_req_keywords_text.winfo_exists():
                 self.edit_req_keywords_text.delete('1.0', tk.END)
                 self.edit_req_keywords_text.insert('1.0', "\n".join(map(str, req_kws))) # Ensure items are strings
            if hasattr(self,'edit_excl_keywords_text') and self.edit_excl_keywords_text.winfo_exists():
                 self.edit_excl_keywords_text.delete('1.0', tk.END)
                 self.edit_excl_keywords_text.insert('1.0', "\n".join(map(str, excl_kws))) # Ensure items are strings

            # Enable Save and Delete buttons
            if hasattr(self,'save_query_button'): self.save_query_button.config(state=tk.NORMAL, text="Save Changes") # Update text for edit mode
            if hasattr(self,'delete_query_button'): self.delete_query_button.config(state=tk.NORMAL)

            logging.debug(f"Loaded query '{selected_query_dict.get('name')}' (ID: {selected_item_id}) into editor.")
        else:
            # This case should ideally not happen if tree selection corresponds to self.queries
            logging.error(f"Query data not found in self.queries for selected tree ID: {selected_item_id}")
            self.clear_detail_frame() # Clear the form if data is inconsistent

    def toggle_query_active(self, event=None):
        # Identify the row that was double-clicked
        item_id = self.query_tree.identify_row(event.y)
        if not item_id: return # Double-clicked empty space

        logging.debug(f"Toggle active requested for query ID: {item_id}")
        updated = False
        target_query = None
        for query_dict in self.queries:
            if query_dict.get("id") == item_id:
                target_query = query_dict
                current_state = query_dict.get("active", False)
                query_dict["active"] = not current_state # Toggle the state
                updated = True
                logging.info(f"Toggled query '{query_dict.get('name')}' active status to {query_dict['active']}.")
                break

        if updated:
            save_queries(self.queries) # Save changes to file
            self.populate_query_tree() # Refresh the tree view to show the change
            # Reselect the item that was just toggled
            if self.query_tree.exists(item_id):
                self.query_tree.focus(item_id)
                self.query_tree.selection_set(item_id)
                # If the editor was showing this query, update the 'Active' checkbox
                if self.edit_query_id == item_id and target_query:
                    self.edit_active_var.set(target_query["active"])

    def add_new_query_action(self):
        logging.debug("Add New Query button clicked.")
        # Deselect any item in the tree
        self.query_tree.selection_set([])
        # Clear the detail frame and set defaults for a new query
        self.clear_detail_frame()
        # Explicitly set edit_query_id to None to signify "add mode"
        self.edit_query_id = None
        # Configure buttons for add mode
        self.save_query_button.config(state=tk.NORMAL, text="Save New Query")
        self.delete_query_button.config(state=tk.DISABLED)
        # Optionally give focus to the first field (e.g., Name)
        # (See focus setting code commented out in clear_detail_frame)
        messagebox.showinfo("Add Query", "Enter details for the new query in the editor panel and click 'Save New Query'.")


    def save_query_action(self):
        is_new_query = self.edit_query_id is None # Determine if we are adding or editing
        query_id = self.edit_query_id if not is_new_query else f"query_{uuid.uuid4()}" # Use existing or generate new ID

        name = self.edit_name_var.get().strip()
        # Use a default name if empty, incorporating part of the ID
        if not name:
            name = f"Query_{query_id[:8]}" # Default name like "Query_a1b2c3d4"
            self.edit_name_var.set(name) # Update the variable so it shows in the GUI
            logging.warning(f"Query name was empty, using default: '{name}'")

        url = self.edit_url_var.get().strip() # Base URL

        # --- URL Validation ---
        if not url or not url.startswith(('http://', 'https://')):
            messagebox.showerror("Invalid URL", "Base URL is required and must start with http:// or https://.")
            logging.warning(f"Save query failed: Invalid URL '{url}'.")
            return

        # --- Postcode Handling ---
        postcode_raw = self.edit_postcode_var.get().strip()
        # Basic validation (example: must be like 1234 AB or empty)
        postcode_pattern = r"^\d{4}\s?[A-Z]{2}$"
        if postcode_raw and not re.match(postcode_pattern, postcode_raw, re.IGNORECASE):
             messagebox.showerror("Invalid Postcode", "Postcode must be in '1234 AB' format or empty.")
             logging.warning(f"Save query failed: Invalid postcode format '{postcode_raw}'.")
             return
        postcode = postcode_raw.upper() if postcode_raw else None # Store uppercase or None

        # --- Distance Handling (Convert KM input to Meters for storage) ---
        distance_km_str = self.edit_distance_var.get().strip()
        distance_meters = None # Default to None (no distance filter)
        if distance_km_str: # If user entered something
            try:
                distance_km = int(distance_km_str)
                if distance_km <= 0:
                    # Allow empty or positive numbers only
                     messagebox.showerror("Invalid Input", f"Distance (km) must be a positive whole number if provided. You entered: '{distance_km_str}'")
                     logging.warning(f"Save query failed: Invalid distance value {distance_km_str}.")
                     return
                distance_meters = distance_km * 1000 # Convert valid KM to Meters
            except ValueError:
                # Handle cases where input is not a number
                messagebox.showerror("Invalid Input", f"Distance (km) must be a whole number. You entered: '{distance_km_str}'")
                logging.warning(f"Save query failed: Non-numeric distance value '{distance_km_str}'.")
                return
        # --- End Distance Handling ---

        # --- Price and Age Handling (with validation) ---
        try:
            # Min Price
            min_p_str = self.edit_min_price_var.get().replace(',', '.').strip() # Allow comma, remove whitespace
            min_p = float(min_p_str) if min_p_str else None
            if min_p is not None and min_p < 0: raise ValueError("Minimum price cannot be negative.")

            # Max Price
            max_p_str = self.edit_max_price_var.get().replace(',', '.').strip()
            max_p = float(max_p_str) if max_p_str else None
            if max_p is not None and max_p < 0: raise ValueError("Maximum price cannot be negative.")

            # Check Min <= Max if both are provided
            if min_p is not None and max_p is not None and min_p > max_p:
                raise ValueError("Minimum price cannot be greater than maximum price.")

            # Max Age
            max_age = self.edit_max_age_var.get() # Get from IntVar
            if max_age < 1: raise ValueError("Maximum age (days) must be 1 or greater.")

        except ValueError as e:
            messagebox.showerror("Invalid Input", f"Please check numeric fields (Price/Max Age).\nError: {e}")
            logging.warning(f"Save query failed: Validation error - {e}")
            return
        except tk.TclError:
             messagebox.showerror("Input Error", "Error reading numeric value (Max Age). Please enter a valid number.")
             logging.warning("Save query failed: TclError reading max_age_var.")
             return
        except Exception as e: # Catch unexpected errors during conversion/validation
            messagebox.showerror("Input Error", f"Unexpected error reading numeric fields: {e}")
            logging.error(f"Save query failed: Unexpected error - {e}")
            return

        # --- Keywords and Conditions ---
        # Get keywords, clean them (lowercase, strip whitespace, remove empty lines)
        req_kws = [line.strip().lower() for line in self.edit_req_keywords_text.get("1.0", tk.END).splitlines() if line.strip()]
        excl_kws = [line.strip().lower() for line in self.edit_excl_keywords_text.get("1.0", tk.END).splitlines() if line.strip()]
        # Get selected conditions
        sel_conds = [cond for cond, var in self.edit_condition_vars.items() if var.get()]

        # --- Assemble the Query Dictionary ---
        query_data = {
            "id": query_id,
            "name": name,
            "url": url, # Base URL from form
            "active": self.edit_active_var.get(),
            "postcode": postcode,           # Stored Postcode (or None)
            "distanceMeters": distance_meters, # Stored Distance in Meters (or None)
            "min_price": min_p,
            "max_price": max_p,
            "include_non_priced": self.edit_include_non_priced_var.get(),
            "conditions": sel_conds,
            "max_age_days": max_age,
            "required_keywords": req_kws,
            "excluded_keywords": excl_kws
        }

        # --- Update or Add to self.queries list ---
        if is_new_query:
            self.queries.append(query_data)
            logging.info(f"Adding new query: '{name}' (ID: {query_id})")
        else: # Editing existing query
            found = False
            for i, q in enumerate(self.queries):
                if q.get("id") == query_id:
                    self.queries[i] = query_data # Replace the existing dict with the new one
                    found = True
                    logging.info(f"Updating existing query: '{name}' (ID: {query_id})")
                    break
            if not found:
                # This should not happen if edit_query_id was correctly set
                logging.error(f"Save failed: Query ID {query_id} not found in self.queries during update attempt.")
                messagebox.showerror("Save Error", f"Internal error: Could not find query ID {query_id} to update.")
                return

        # --- Save the entire updated queries list ---
        save_queries(self.queries) # Save the updated list to queries.json
        logging.info("Query list saved to queries.json")

        # --- Refresh UI ---
        self.populate_query_tree() # Refresh the tree view

        # Reselect the saved/added item in the tree and reload editor
        if self.query_tree.exists(query_id):
            self.query_tree.focus(query_id)
            self.query_tree.selection_set(query_id)
            # self.on_query_selected() # Reload editor automatically (optional, but good practice)
        else:
            # If insertion failed or ID changed unexpectedly
             logging.warning(f"Could not re-select query ID {query_id} in tree after save.")
             self.clear_detail_frame() # Clear editor if item can't be found

        messagebox.showinfo("Saved", f"Query '{name}' was saved successfully.")


    def delete_query_action(self):
        selected_item_id = self.query_tree.focus() # Get the iid of the focused item
        if not selected_item_id:
             messagebox.showwarning("No Selection", "Please select a query from the list to delete.")
             return

        # Find the query dictionary corresponding to the selected ID
        query_to_delete = next((q for q in self.queries if q.get("id") == selected_item_id), None)

        if not query_to_delete:
            logging.error(f"Delete failed: Query data not found for ID {selected_item_id}.")
            messagebox.showerror("Error", f"Could not find query data for ID {selected_item_id}.")
            return

        q_name = query_to_delete.get('name', selected_item_id) # Get name for confirmation

        # Confirmation dialog
        if messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete the query:\n\n'{q_name}'?\n\nThis action cannot be undone."):
            # Filter out the query to be deleted
            original_count = len(self.queries)
            self.queries = [q for q in self.queries if q.get("id") != selected_item_id]
            deleted_count = original_count - len(self.queries)

            if deleted_count > 0:
                save_queries(self.queries) # Save the modified list
                logging.info(f"Deleted query: '{q_name}' (ID: {selected_item_id}).")
                self.populate_query_tree() # Refresh the tree
                self.clear_detail_frame() # Clear the editor panel
                # Disable buttons after successful delete
                self.save_query_button.config(state=tk.DISABLED, text="Save Changes")
                self.delete_query_button.config(state=tk.DISABLED)
                messagebox.showinfo("Deleted", f"Query '{q_name}' has been deleted.")
            else:
                # Should not happen if query_to_delete was found initially
                logging.error(f"Deletion logic failed for query ID {selected_item_id}. Query list unchanged.")
                messagebox.showerror("Error", "An error occurred during deletion. The query was not removed.")
        else:
            logging.debug(f"Deletion cancelled for query: '{q_name}'")

    # --- Core App Methods ---
    def update_interval_from_spinbox(self):
        try:
            interval_value = self.interval_var.get()
            if interval_value >= 1:
                self.current_check_interval = interval_value
                logging.debug(f"Check interval updated to {interval_value} minutes via GUI spinbox.")
                # Optionally, save this setting immediately to config.ini
                # self.save_general_settings_to_config()
            else:
                logging.warning(f"Invalid interval value entered: {interval_value}. Must be 1 or greater.")
                # Reset spinbox to the last known valid value
                self.interval_var.set(self.current_check_interval)
        except (tk.TclError, ValueError) as e:
            logging.warning(f"Error reading interval spinbox value: {e}. Resetting to current value.")
            # Reset spinbox to the last known valid value
            self.interval_var.set(self.current_check_interval)
        except Exception as e:
            logging.error(f"Unexpected error updating interval from spinbox: {e}")
            self.interval_var.set(self.current_check_interval) # Reset on unexpected error

    def start_scan(self):
        if self.running:
            logging.warning("Start scan requested, but a scan is already running.")
            messagebox.showwarning("Already Running", "A scan process is already active.")
            return

        # Ensure logging is set up
        self.setup_logging()
        # Ensure the interval is current
        self.update_interval_from_spinbox()

        # Check for active queries
        active_queries_to_run = [q for q in self.queries if q.get("active", False)]
        if not active_queries_to_run:
             messagebox.showwarning("No Active Queries", "There are no active queries to run. Please activate queries in the Query Management tab (double-click 'Active' column).")
             logging.warning("Start scan aborted: No active queries found.")
             return

        logging.info(f"Preparing to start scan with {len(active_queries_to_run)} active queries.")

        # Check Telegram settings (optional warning)
        if not DEFAULT_TELEGRAM_BOT_TOKEN or not DEFAULT_TELEGRAM_CHAT_ID:
            if not messagebox.askokcancel("Telegram Warning", "Telegram Bot Token or Chat ID is missing in config.ini.\n\nNotifications will not be sent.\n\nContinue with scan anyway?"):
                logging.warning("Scan cancelled by user due to missing Telegram settings.")
                return

        # --- Update State and UI ---
        self.running = True
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        if hasattr(self, 'interval_spinbox'): self.interval_spinbox.config(state=tk.DISABLED) # Disable interval change while running

        current_interval = self.interval_var.get() # Get interval for this run
        self.update_status_file("running") # Update external status file

        logging.info(f"Starting scraper thread (Interval: {current_interval} min)...")
        # --- Start the Scraper Thread ---
        self.scraper_thread = threading.Thread(
            target=self.run_scraper_thread,
            args=(self, active_queries_to_run, current_interval), # Pass necessary info
            daemon=True # Allows main program to exit even if thread is running
        )
        self.scraper_thread.start()
        logging.info("Scraper thread started.")

    def stop_scan(self):
        if self.running and self.scraper_thread and self.scraper_thread.is_alive():
            logging.info("Stop scan requested by user...")
            self.running = False # Signal the thread to stop
            self.stop_button.config(state=tk.DISABLED) # Disable stop button immediately
            logging.debug("Waiting briefly for scraper thread to acknowledge stop signal...")
            # Optionally wait a very short time, but handle_thread_exit will manage UI updates
            # self.scraper_thread.join(timeout=2) # Wait max 2 seconds
            # if self.scraper_thread.is_alive():
            #     logging.warning("Scraper thread did not stop gracefully within timeout.")
            # else:
            #     logging.info("Scraper thread stopped.")
            #     self.handle_thread_exit() # Update UI if join successful - Moved to thread exit path
        elif self.running:
            # State mismatch: flag is running but thread isn't. Reset UI.
             logging.warning("Stop requested, but no active scraper thread found. Resetting UI.")
             self.handle_thread_exit(normal_exit=False) # Indicate abnormal state
        else:
            logging.debug("Stop scan requested, but scan was not running.")

    def run_scraper_thread(self, app_instance, active_queries, interval_minutes):
        """Scraper loop: runs scans, saves results, and saves stats to JSON file."""
        global config, DEFAULT_REQUEST_DELAY, STATS_FILE # Ensure access to needed globals

        run_start_time = datetime.now()
        logging.info(f"--- Scraper thread initiated at {run_start_time:%Y-%m-%d %H:%M:%S} ---")

        try:
            init_db() # Ensure DB schema is up-to-date before first scan
        except Exception as e:
            logging.critical(f"Scraper thread failed during initial DB check: {e}", exc_info=True)
            self.update_status_file("error_db_init") # Update status on critical error
            # Use 'after' to schedule the UI update in the main thread
            if hasattr(self.master, 'after') and self.master.winfo_exists():
                self.master.after(0, self.handle_thread_exit, False)
            return # Exit thread if DB init fails

        active_query_count_at_start = len(active_queries) # Store initial count for stats

        while self.running: # Check the instance flag controlled by start/stop scan
            cycle_start_time = time.time()
            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            logging.info(f"--- Starting Scan Cycle ({now_str}) ---")
            self.update_status_file("running") # Set status to running for this cycle

            # --- Re-evaluate active queries? (Optional - current implementation uses the list passed at start) ---
            # If you want it to dynamically check queries.json each cycle:
            # current_active_queries = [q for q in load_queries() if q.get("active", False)]
            # active_query_count_for_cycle = len(current_active_queries)
            # if not current_active_queries:
            # else:
            #     queries_to_process = current_active_queries
            # For now, stick to the list passed at the start for simplicity.
            queries_to_process = active_queries
            active_query_count_for_cycle = active_query_count_at_start # Use the count from when started


            if not queries_to_process:
                logging.warning("Scan cycle initiated but has no active queries to process.")
                # Use a shorter sleep if no queries are active, to check stop flag more often
                wait_seconds_no_query = min(interval_minutes * 60, 30) # Check every 30s if no queries
                logging.info(f"No active queries. Waiting {wait_seconds_no_query}s...")
                wait_until = time.time() + wait_seconds_no_query
                while time.time() < wait_until and self.running:
                    time.sleep(0.5) # Shorter sleep to be responsive to stop
                continue # Skip to next loop iteration

            # --- Process Queries ---
            total_new_in_cycle = 0
            for query_index, query_config in enumerate(queries_to_process):
                if not self.running:
                    logging.info("Stop signal received during query processing. Breaking cycle.")
                    break # Exit the query loop if stop was requested

                q_name = query_config.get('name', f'Query {query_index+1}')
                logging.info(f"--- Processing Query {query_index+1}/{len(queries_to_process)}: '{q_name}' ---")
                q_start = time.time()
                try:
                    # Call scrape_and_process, which returns the count of new listings found for this query
                    new_listings_for_query = scrape_and_process(app_instance, query_config)
                    if new_listings_for_query is None: new_listings_for_query = 0 # Ensure it's a number
                    total_new_in_cycle += new_listings_for_query
                    q_duration = time.time() - q_start
                    logging.info(f"--- Query '{q_name}' finished ({q_duration:.2f}s). New listings found: {new_listings_for_query} ---")
                except Exception as e:
                    # Log critical error but continue with other queries if possible
                    logging.critical(f"!!! Critical error processing query '{q_name}': {e}", exc_info=True)

                # --- Delay Between Queries ---
                # Only delay if the stop flag is not set and it's not the last query
                if self.running and query_index < len(queries_to_process) - 1:
                    delay = DEFAULT_REQUEST_DELAY # Use the globally loaded value
                    logging.debug(f"Waiting {delay}s before next query...")
                    wait_end = time.time() + delay
                    # Sleep in small increments to check the running flag frequently
                    while time.time() < wait_end and self.running:
                        time.sleep(min(0.5, wait_end - time.time())) # Sleep 0.5s or remaining time

            if not self.running:
                 logging.info("Stop signal received after query processing loop. Exiting thread.")
                 break # Exit the main while loop

            # --- Cycle Finished ---
            cycle_finish_time_dt = datetime.now()
            cycle_finish_time_iso = cycle_finish_time_dt.isoformat()
            cycle_duration = time.time() - cycle_start_time
            logging.info(f"--- Scan Cycle finished at {cycle_finish_time_dt:%Y-%m-%d %H:%M:%S}. Duration: {cycle_duration:.2f}s. Total new listings in cycle: {total_new_in_cycle} ---")

            # --- Save Statistics to JSON File ---
            stats_data = {
                'last_fetch_time': cycle_finish_time_iso, # Use standard ISO format string
                'last_found_count': total_new_in_cycle,
                'active_query_count': active_query_count_for_cycle # Queries active during this run
            }
            try:
                with open(STATS_FILE, 'w', encoding='utf-8') as f_stats:
                    json.dump(stats_data, f_stats, indent=4)
                logging.info(f"Scan statistics successfully saved to {STATS_FILE}")
            except IOError as e:
                logging.error(f"Could not write statistics to {STATS_FILE}: {e}")
            except Exception as e:
                logging.error(f"Unexpected error saving stats to {STATS_FILE}: {e}")
            # --- End Save Stats ---

            # --- Calculate Wait Time for Next Cycle ---
            # Ensure wait time is reasonable, at least a minimum value (e.g., 10s)
            wait_seconds = max(10, (interval_minutes * 60) - cycle_duration)
            logging.info(f"--- Waiting {wait_seconds:.0f}s for next cycle (Interval: {interval_minutes} min)... ---")
            self.update_status_file("idle") # Update status to idle while waiting

            # Wait loop, checking the running flag frequently
            wait_until = time.time() + wait_seconds
            while time.time() < wait_until and self.running: # Check running flag
                time.sleep(1) # Check every second

        # --- Loop Exited (either by stop signal or error) ---
        run_end_time = datetime.now()
        logging.info(f"--- Scraper thread processing loop finished at {run_end_time:%Y-%m-%d %H:%M:%S} ---")
        # Schedule UI update in the main thread using 'after'
        if hasattr(self.master, 'after') and self.master.winfo_exists():
             # Pass True if self.running became False (normal stop), False otherwise (e.g., error before loop)
            is_normal_exit = not self.running
            self.master.after(0, self.handle_thread_exit, is_normal_exit)

    def handle_thread_exit(self, normal_exit=True):
        """Called from the main thread to update UI after scraper thread finishes."""
        # Ensure status is updated correctly, even if thread errored out early
        if self.running: # If the flag is somehow still true, force it false
            self.running = False
            logging.warning("handle_thread_exit called while running flag was still true. Correcting.")

        if normal_exit:
             logging.info("Scan stopped normally.")
             self.update_status_file("idle") # Set status to idle on normal stop
        else:
             logging.error("Scraper thread stopped due to an error or unexpected state.")
             # Keep status as 'error_*' or update to a general error if needed
             # Optionally: messagebox.showerror("Thread Error", "Scraper thread encountered an error. Check logs.")
             self.update_status_file("error_thread_exit") # Specific status for thread exit error

        # Reset UI elements
        self.scraper_thread = None # Clear thread reference
        if hasattr(self, 'start_button') and self.start_button.winfo_exists():
             self.start_button.config(state=tk.NORMAL)
        if hasattr(self, 'stop_button') and self.stop_button.winfo_exists():
             self.stop_button.config(state=tk.DISABLED)
        if hasattr(self, 'interval_spinbox') and self.interval_spinbox.winfo_exists():
             try: self.interval_spinbox.config(state=tk.NORMAL)
             except tk.TclError: pass # Might fail if widget destroyed during shutdown

        logging.debug("GUI elements reset after thread exit.")

    def on_closing(self):
        """Handles the window close event."""
        if self.running:
            if messagebox.askokcancel("Quit", "Scraper process is currently running.\n\nDo you want to stop the scraper and exit?"):
                logging.info("Stop requested via window close.")
                self.stop_scan()
                # Add a short delay to allow the thread stop signal to propagate before finalizing exit
                self.master.after(500, self.finalize_exit) # Schedule finalize_exit after 500ms
            else:
                return # User cancelled close
        else:
            self.finalize_exit() # No scan running, exit directly

    def finalize_exit(self):
         """Saves settings and destroys the window."""
         logging.info("Saving configurations before closing...")
         try:
             save_queries(self.queries)
         except Exception as e:
              logging.error(f"Failed to save queries on exit: {e}")
         try:
              self.save_general_settings_to_config()
         except Exception as e:
              logging.error(f"Failed to save general settings on exit: {e}")

         logging.info("Exiting GUI application.")
         self.update_status_file("offline") # Set status to offline

         # Clean up trigger file if it exists
         if os.path.exists(self.trigger_file_path):
            try:
                os.remove(self.trigger_file_path)
                logging.info("Cleaned up trigger file on exit.")
            except OSError as e:
                logging.warning(f"Could not remove trigger file '{self.trigger_file_path}' on exit: {e}")

         # Destroy the Tkinter window
         if hasattr(self.master, 'destroy'):
             self.master.destroy()


    # --- Results Table Methods ---
    def add_result_to_treeview(self, listing_data):
        """Inserts a new listing into the results treeview."""
        if not hasattr(self, 'results_tree') or not self.results_tree.winfo_exists():
            logging.warning("Attempted to add result, but results_tree widget not available.")
            return
        try:
            # Ensure the order matches the column definition in create_results_tab
            values = (
                listing_data.get("title", "N/A"),
                listing_data.get("price", "N/A"),
                listing_data.get("location", "N/A"),
                listing_data.get("date", "N/A"),
                listing_data.get("condition", "N/A"),
                listing_data.get("url", "N/A"),
                listing_data.get("image_url", "N/A")
            )
            # Insert at the beginning (index 0) to show newest first
            self.results_tree.insert("", 0, values=values)
        except tk.TclError as e:
            logging.error(f"TclError adding result to treeview: {e}. Listing data: {listing_data}")
        except Exception as e:
            logging.error(f"Failed to add result to treeview: {e}. Listing data: {listing_data}")

    def clear_results_tree(self):
        """Removes all items from the results treeview."""
        if hasattr(self, 'results_tree') and self.results_tree.winfo_exists():
            logging.info("Clearing results treeview...")
            count = 0
            for item in self.results_tree.get_children():
                try:
                     self.results_tree.delete(item)
                     count += 1
                except tk.TclError: pass # Item might already be gone
            logging.info(f"Cleared {count} items from results treeview.")
        else:
            logging.warning("Attempted to clear results, but results_tree widget not available.")

    def on_treeview_double_click(self, event):
        """Handles double-clicks on the results treeview to open URLs."""
        try:
            widget = event.widget
            # Ensure the click happened on the results tree specifically
            if not (hasattr(self, 'results_tree') and widget == self.results_tree):
                return

            # Identify the clicked region, column, and item
            region = widget.identify("region", event.x, event.y)
            if region != "cell": return # Ignore clicks on headings or empty space

            col_id_str = widget.identify_column(event.x) # Returns something like '#3'
            # Convert column ID string to zero-based index
            try: col_idx = int(col_id_str.replace('#','')) - 1
            except ValueError: logging.warning(f"Could not parse treeview column ID: {col_id_str}"); return

            item_id = widget.identify_row(event.y) # Get the internal ID of the clicked row
            if not item_id: return # Clicked below last item

            # Get the values for the clicked row
            item_values = widget.item(item_id)['values']
            if not item_values or len(item_values) < 7: # Check if values are valid
                logging.warning(f"Invalid item data for treeview row ID: {item_id}"); return

            # Define which columns contain URLs (0-based index)
            url_column_indices = {5: "Listing URL", 6: "Image URL"} # Map index to description

            url_to_open = None
            if col_idx in url_column_indices:
                try: url_to_open = item_values[col_idx]
                except IndexError: logging.warning(f"IndexError accessing value at column {col_idx} for item {item_id}"); return
            else:
                logging.debug(f"Double click on non-URL column ({col_idx}). Ignoring.")
                return # Clicked on a column we don't handle

            # Open the URL in the default web browser if it's valid
            if url_to_open and isinstance(url_to_open, str) and url_to_open != 'N/A' and url_to_open.startswith('http'):
                logging.info(f"Opening {url_column_indices[col_idx]} in browser: {url_to_open}")
                webbrowser.open_new_tab(url_to_open)
            elif url_to_open and url_to_open != 'N/A':
                logging.warning(f"Attempted to open invalid URL: {url_to_open}")
                messagebox.showwarning("Invalid URL", f"The selected URL is not valid:\n{url_to_open}")
            else:
                logging.debug(f"No valid URL found in column {col_idx} for item {item_id}.")

        except Exception as e:
            logging.error(f"Error handling treeview double-click: {e}", exc_info=True)

    # --- Logging Setup ---
    def setup_logging(self):
        """Configures logging handlers for the application (File and GUI)."""
        # Ensure log directory exists
        log_dir = os.path.dirname(DEFAULT_LOG_FILE)
        if log_dir and not os.path.exists(log_dir):
            try:
                os.makedirs(log_dir)
                # Use basic print here as logging might not be fully configured yet
                print(f"INFO: Created log directory: {log_dir}")
            except OSError as e:
                print(f"ERROR: Failed to create log directory '{log_dir}': {e}")

        logger = logging.getLogger() # Get the root logger
        # Set root logger level - DEBUG allows all levels through to handlers
        logger.setLevel(logging.DEBUG)

        # --- GUI Handler ---
        # Check if GUI handler already exists
        has_dual_handler = any(isinstance(h, DualTextAreaHandler) for h in logger.handlers)
        if not has_dual_handler:
            # Check if log areas exist before creating handler
            if hasattr(self, 'debug_log_area') and hasattr(self, 'found_log_area') and \
               self.debug_log_area.winfo_exists() and self.found_log_area.winfo_exists():
                try:
                    self.dual_text_handler = DualTextAreaHandler(self.debug_log_area, self.found_log_area)
                    # Set level for GUI handler (e.g., DEBUG to show everything)
                    self.dual_text_handler.setLevel(logging.DEBUG)
                    logger.addHandler(self.dual_text_handler)
                    print("INFO: GUI logging handler added.") # Use print as logging might use the handler being added
                except Exception as e:
                    print(f"ERROR: Failed to create GUI logging handler: {e}")
            else:
                print("WARNING: Log text areas not found or ready during logging setup.")

        # --- File Handler ---
        try:
            # Check if a FileHandler for the correct file already exists
            log_file_abs = os.path.abspath(DEFAULT_LOG_FILE)
            has_file_handler = any(isinstance(h, logging.FileHandler) and getattr(h, 'baseFilename', None) == log_file_abs for h in logger.handlers)

            if not has_file_handler:
                file_handler = logging.FileHandler(DEFAULT_LOG_FILE, encoding='utf-8', mode='a') # Append mode
                # More detailed file log format
                file_formatter = logging.Formatter('%(asctime)s - %(levelname)-8s - [%(threadName)s] - %(filename)s:%(lineno)d - %(message)s')
                file_handler.setFormatter(file_formatter)
                # Set file logging level from config or default
                file_log_level_str = DEFAULT_LOG_LEVEL.upper()
                file_log_level = getattr(logging, file_log_level_str, logging.INFO) # Default to INFO if invalid level in config
                file_handler.setLevel(file_log_level)
                logger.addHandler(file_handler)
                print(f"INFO: File logging handler added: {DEFAULT_LOG_FILE} (Level: {file_log_level_str})")
            else:
                 print("DEBUG: File logging handler already exists.") # Use print as logging call might duplicate

        except Exception as e:
            print(f"ERROR: Failed to initialize file logging to {DEFAULT_LOG_FILE}: {e}")

        # Ensure log areas are initially disabled (read-only) after setup
        if hasattr(self, 'debug_log_area') and self.debug_log_area.winfo_exists():
            self.debug_log_area.config(state=tk.DISABLED)
        if hasattr(self, 'found_log_area') and self.found_log_area.winfo_exists():
            self.found_log_area.config(state=tk.DISABLED)


# --- Main Execution ---
if __name__ == "__main__":
    # Configure basic console logging immediately for startup messages
    log_format = '%(asctime)s - %(levelname)-8s - [%(threadName)s] %(message)s'
    logging.basicConfig(level=logging.INFO, format=log_format) # Basic console setup
    logging.info("Application starting...")


    load_configuration() # Load config before initializing UI/DB

    # Ensure log directory exists (needed before file handler in setup_logging is created by Tkinter app)
    log_dir_main = os.path.dirname(DEFAULT_LOG_FILE)
    if log_dir_main and not os.path.exists(log_dir_main):
        try: os.makedirs(log_dir_main)
        except OSError as e: logging.error(f"ERROR: Could not create log directory '{log_dir_main}' on startup: {e}")

    # Initialize DB schema on startup if needed
    try:
        init_db()
    except Exception as e:
         logging.critical(f"FATAL: Database initialization failed on startup: {e}. Exiting.", exc_info=True)
         exit(1) # Exit if DB can't be prepared

    # --- Start Tkinter GUI ---
    root = tk.Tk()
    app = ScraperApp(root) # Instantiates the GUI, which includes logging setup
    root.mainloop() # Start the Tkinter event loop

    logging.info("Application finished.")