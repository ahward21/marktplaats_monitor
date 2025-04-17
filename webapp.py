# webapp.py (Added API endpoints for hide/favorite)
import os
import logging
from datetime import datetime
import threading
import sqlite3
import json
from flask import Flask, render_template, redirect, url_for, flash, request, jsonify # Added request, jsonify

# --- Configuration ---
STATS_FILE = 'scan_stats.json'
# Attempt to import necessary functions and constants from scraper.py
SCRAPER_IMPORT_SUCCESS = False # Assume failure initially
try:
    # Import needed functions and the DB path constant
    from scraper import (
        get_listings_from_db,  # <-- Will use the modified version from previous response
        load_configuration,
        DEFAULT_DB_PATH,       # <-- Use this for DB connections
        DEFAULT_LOG_FILE,
        init_db,
        load_queries           # <-- Still needed for stats
    )
    SCRAPER_IMPORT_SUCCESS = True # Set to True only if all imports succeed
    logging.info("Successfully imported required components from scraper.py")
except ImportError as e:
    print(f"ERROR: Could not import required components from scraper.py - {e}")
    print("Ensure scraper.py is in the same directory or Python path.")
    # Define fallbacks if import fails
    DEFAULT_DB_PATH = 'marktplaats_listings.db'
    DEFAULT_LOG_FILE = 'logs/webapp.log'
    def get_listings_from_db(limit=100): logging.error("Fallback get_listings_from_db used."); return []
    def load_configuration(): logging.warning("Fallback load_configuration used."); pass
    def init_db(): logging.warning("Fallback init_db used."); pass
    def load_queries(filename='queries.json'): logging.error("Fallback load_queries used."); return []
except Exception as import_err:
    # Catch other potential errors during import phase
    print(f"ERROR: Unexpected error during import from scraper.py - {import_err}")
    logging.error(f"Unexpected error importing from scraper.py: {import_err}", exc_info=True)
    DEFAULT_DB_PATH = 'marktplaats_listings.db'
    DEFAULT_LOG_FILE = 'logs/webapp.log'
    def get_listings_from_db(limit=100): logging.error("Fallback get_listings_from_db used."); return []
    def load_configuration(): logging.warning("Fallback load_configuration used."); pass
    def init_db(): logging.warning("Fallback init_db used."); pass
    def load_queries(filename='queries.json'): logging.error("Fallback load_queries used."); return []


TRIGGER_FILE = "scan_trigger.now"
STATUS_FILE = "scan_status.txt"

# --- Flask App Setup ---
app = Flask(__name__)
app.secret_key = os.urandom(24) # Needed for flash messages

# --- Configure Logging ---
log_dir = os.path.dirname(DEFAULT_LOG_FILE)
if log_dir and not os.path.exists(log_dir):
    try: os.makedirs(log_dir)
    except OSError as e: print(f"Warning: Could not create log directory '{log_dir}': {e}")

log_level = logging.INFO # Or DEBUG for more verbosity
log_format = '%(asctime)s %(levelname)-8s [FlaskWebApp] %(message)s'
# Remove existing handlers before adding new ones to prevent duplicate logs in console/file
root_logger = logging.getLogger()
if root_logger.hasHandlers():
    for handler in root_logger.handlers[:]:
        if isinstance(handler, (logging.StreamHandler, logging.FileHandler)):
            try:
                root_logger.removeHandler(handler)
            except Exception as e:
                print(f"Warn: Error removing handler {handler}: {e}") # Log warning on removal error

# Add desired handlers
log_handlers = [logging.StreamHandler()] # Log to console
# Ensure log file path is valid before adding FileHandler
if DEFAULT_LOG_FILE:
     log_handlers.append(logging.FileHandler(DEFAULT_LOG_FILE, mode='a', encoding='utf-8'))

logging.basicConfig(level=log_level, format=log_format, handlers=log_handlers)
logging.info("Flask application starting up...")

# Load initial configuration via scraper module if possible
if SCRAPER_IMPORT_SUCCESS:
    try:
        load_configuration()
        logging.info("Scraper configuration loaded via scraper module.")
        # Ensure DB exists after loading config (which sets DEFAULT_DB_PATH)
        if DEFAULT_DB_PATH and not os.path.exists(DEFAULT_DB_PATH):
             logging.warning(f"Database file not found at configured path: {DEFAULT_DB_PATH}. Attempting initialization.")
             init_db() # This now includes schema checks/adds columns
        elif DEFAULT_DB_PATH:
             # DB Exists, ensure schema is up-to-date
             logging.info(f"Database found at: {DEFAULT_DB_PATH}. Ensuring schema is up-to-date.")
             init_db() # Run init again to add columns if missing
        else:
            logging.error("DEFAULT_DB_PATH is not set after loading configuration.")

    except Exception as e:
        logging.error(f"Failed to load configuration or initialize/check DB via scraper module: {e}", exc_info=True)
else:
    logging.error("Cannot load configuration or initialize DB because scraper module import failed.")


# --- Database Helper ---
def get_db_connection():
    """Establishes a connection to the SQLite database."""
    if not DEFAULT_DB_PATH:
        logging.error("Database path (DEFAULT_DB_PATH) is not set.")
        return None
    try:
        # Consider adding a timeout
        conn = sqlite3.connect(DEFAULT_DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row # Access columns by name
        return conn
    except sqlite3.Error as e:
        logging.error(f"Error connecting to database at '{DEFAULT_DB_PATH}': {e}")
        return None

# --- Helper function for Statistics (with Enhanced Logging) ---
def get_scan_stats_data():
    """Reads stats from JSON file and gets current active query count."""
    stats = { # Default values
        'last_fetch_time': 'N/A',
        'last_found_count': 'N/A',
        'active_query_count': 'N/A' # Will be overwritten
    }
    stats_file_path = os.path.abspath(STATS_FILE)

    # Read stats saved by scraper.py
    if os.path.exists(stats_file_path):
        try:
            with open(stats_file_path, 'r', encoding='utf-8') as f_stats:
                saved_stats = json.load(f_stats)

            # Process last fetch time
            raw_time = saved_stats.get('last_fetch_time')
            if raw_time:
                try:
                    dt_obj = datetime.fromisoformat(raw_time)
                    stats['last_fetch_time'] = dt_obj.strftime('%Y-%m-%d %H:%M:%S')
                except (ValueError, TypeError) as date_err:
                     stats['last_fetch_time'] = raw_time # Fallback to ISO string
                     logging.warning(f"Could not parse timestamp '{raw_time}': {date_err}. Using raw value.")
            else:
                 stats['last_fetch_time'] = 'N/A' # Explicitly set N/A

            # Process last found count
            count = saved_stats.get('last_found_count')
            stats['last_found_count'] = count if count is not None else 'N/A' # Allow 0

        except json.JSONDecodeError as e:
             logging.error(f"Error decoding JSON from {stats_file_path}: {e}")
             stats['last_fetch_time'] = 'Error Reading File'
             stats['last_found_count'] = 'Error'
        except IOError as e:
             logging.error(f"Error reading stats file {stats_file_path} (IOError): {e}")
             stats['last_fetch_time'] = 'Error Reading File'
             stats['last_found_count'] = 'Error'
        except Exception as e:
            logging.error(f"Unexpected error reading/processing stats file {stats_file_path}: {e}", exc_info=True)
            stats['last_fetch_time'] = 'Error Processing File'
            stats['last_found_count'] = 'Error'
    else:
        logging.warning(f"Stats file '{stats_file_path}' not found. Using defaults.") # Log clearly if missing

    # Get **current** active query count separately (requires successful import)
    if SCRAPER_IMPORT_SUCCESS:
        try:
            queries = load_queries() # Use function imported at top level
            active_count = sum(1 for q in queries if q.get("active", False))
            stats['active_query_count'] = active_count
        except Exception as e:
            logging.error(f"Error loading queries to count active ones: {e}", exc_info=True)
            stats['active_query_count'] = 'Error' # Indicate error in counting
    else:
        logging.warning("Cannot count active queries because scraper module import failed.")
        stats['active_query_count'] = 'N/A (Import Fail)'

    return stats
# --- End of get_scan_stats_data function ---


# --- Helper function for Status ---
def get_scan_status():
    """Checks status file or trigger file to estimate scan status."""
    status_file_path = os.path.abspath(STATUS_FILE)
    trigger_file_path = os.path.abspath(TRIGGER_FILE)
    current_status = "Unknown" # Start with unknown
    trigger_is_pending = False

    if os.path.exists(status_file_path):
        try:
            with open(status_file_path, 'r') as f:
                status_content = f.read().strip().lower()
            if status_content == 'running': current_status = "Running"
            elif status_content == 'idle': current_status = "Idle"
            elif status_content == 'offline': current_status = "Offline"
            elif status_content.startswith('error'): current_status = f"Error ({status_content.split('_', 1)[-1]})" # Show specific error if available
            # Any other content leaves status as 'Unknown' for now
        except Exception as e:
            logging.warning(f"Could not read status file '{status_file_path}': {e}")
            current_status = "Status File Error"

    # Check for trigger file ONLY if status isn't definitively running/offline/error
    if current_status in ["Idle", "Unknown", "Status File Error"]:
        if os.path.exists(trigger_file_path):
            trigger_is_pending = True
            # Only override status if it wasn't 'Idle'
            if current_status != "Idle":
                 current_status = "Trigger Pending"

    # Final default if still unknown
    if current_status == "Unknown": current_status = "Idle / Unknown"

    # Return both status string and pending flag
    return current_status, trigger_is_pending
# --- End of get_scan_status function ---


# --- Flask Routes ---
@app.route('/')
def index():
    """Displays the main page with listings, stats, status, and prepares map data."""
    logging.debug("Request received for index page.")
    listings = []
    listings_json = "[]" # For the map data

    # Fetch Listings & Prepare JSON for Map
    if SCRAPER_IMPORT_SUCCESS:
        try:
            # get_listings_from_db now handles filtering hidden and sorting by favorite
            # Fetch potentially more listings if needed for better map view
            listings = get_listings_from_db(limit=200)
            logging.info(f"Retrieved {len(listings)} non-hidden listings from DB.")

            # Prepare JSON data specifically for the map markers
            # Include essential info and coordinates
            map_listings_data = []
            for lst in listings:
                 map_item = {
                    "url": lst.get("url"),
                    "title": lst.get("title"),
                    "scraped_at": lst.get("scraped_at"), # For potential marker coloring by age
                    "price": lst.get("price"),
                    "image_url": lst.get("image_url"),
                    "latitude": lst.get("latitude"),
                    "longitude": lst.get("longitude"),
                 }
                 # Only add items with valid coordinates to the map data
                 if map_item.get("latitude") is not None and map_item.get("longitude") is not None:
                    map_listings_data.append(map_item)

            try:
                # Use default=str to handle datetime objects if present in scraped_at
                listings_json = json.dumps(map_listings_data, default=str)
            except Exception as json_e:
                logging.error(f"JSON dump error for map data: {json_e}", exc_info=True)
                listings_json = "[]" # Ensure valid JSON on error

        except Exception as db_e:
             logging.error(f"DB/Listing fetch error: {db_e}", exc_info=True)
             flash("Error retrieving listing data from database.", "error")
             listings = [] # Ensure listings is empty on error
             listings_json = "[]"
    else:
        flash("Backend scraper functions could not be imported. Listing data unavailable.", "error")
        listings = []
        listings_json = "[]"

    # Get Scan Statistics & Status
    scan_stats = get_scan_stats_data()
    scan_status_display, trigger_pending = get_scan_status() # Get both values

    # Render the template, passing all necessary data
    # listings variable now contains the full listing objects fetched (incl. 'favorite')
    # listings_json variable contains data specifically formatted for the map
    return render_template('listings.html',
                           listings=listings,             # Pass full data for card rendering
                           listings_json=listings_json,   # Pass map-specific data
                           scan_stats=scan_stats,
                           scan_status=scan_status_display,
                           trigger_pending=trigger_pending)


@app.route('/scan')
def trigger_scan():
    """Creates a trigger file to signal the Tkinter app to start a scan."""
    logging.info("Scan trigger requested via web interface.")
    trigger_file_path = os.path.abspath(TRIGGER_FILE)
    status_now, is_pending = get_scan_status() # Check current status

    # Prevent triggering if already running or already pending
    if status_now == "Running":
         flash("Scan is already running.", "warning")
         logging.warning(f"Scan trigger ignored: Status is already 'Running'.")
    elif is_pending or os.path.exists(trigger_file_path): # Redundant check for robustness
        flash("Scan trigger is already pending.", "warning")
        logging.warning(f"Scan trigger ignored: Trigger file '{trigger_file_path}' already exists.")
    else:
        # Attempt to create trigger file
        try:
            with open(trigger_file_path, 'w') as f:
                pass # Create empty file
            flash("Scan trigger request sent successfully.", "info")
            logging.info(f"Trigger file '{trigger_file_path}' created.")
        except Exception as e:
            flash(f"Error creating trigger file: {e}", "error")
            logging.error(f"Failed to create trigger file '{trigger_file_path}': {e}", exc_info=True)

    return redirect(url_for('index')) # Redirect back to the main dashboard

# --- API Endpoint: Hide Listing ---
@app.route('/api/hide_listing', methods=['POST'])
def api_hide_listing():
    """API endpoint to mark a listing as hidden in the database."""
    logging.debug("Received request for /api/hide_listing")
    if not request.is_json:
        logging.warning("Hide API request rejected: Content-Type is not application/json")
        return jsonify({"success": False, "message": "Request must be JSON"}), 415 # Use 415 Unsupported Media Type

    data = request.get_json()
    if not data: # Handle empty JSON body
        logging.warning("Hide API request failed: Empty JSON body")
        return jsonify({"success": False, "message": "Missing JSON body"}), 400

    url_to_hide = data.get('url')

    # Validate URL parameter
    if not url_to_hide or not isinstance(url_to_hide, str) or not url_to_hide.startswith('http'):
        logging.warning(f"Hide API request failed: Invalid or missing URL - {url_to_hide}")
        return jsonify({"success": False, "message": "Invalid or missing 'url' parameter in JSON body"}), 400 # Use 400 Bad Request

    conn = None
    try:
        conn = get_db_connection()
        if conn is None:
            # Log already happened in get_db_connection
            return jsonify({"success": False, "message": "Database connection failed."}), 500

        cursor = conn.cursor()
        logging.info(f"Attempting to mark listing as hidden: {url_to_hide}")
        cursor.execute("UPDATE listings SET hidden = 1 WHERE url = ?", (url_to_hide,))
        conn.commit()

        if cursor.rowcount > 0:
            logging.info(f"Successfully marked listing as hidden: {url_to_hide}")
            return jsonify({"success": True, "message": "Listing hidden successfully."})
        else:
            # URL wasn't found, which might be okay depending on requirements
            # Maybe it was already hidden or deleted?
            logging.warning(f"URL not found in database during hide attempt: {url_to_hide}")
            # Decide if this is an error or success (e.g., idempotent)
            # Returning success might be better UX if user clicks twice.
            return jsonify({"success": True, "message": "Listing not found or already hidden."}) # Report success if not found

    except sqlite3.Error as e:
        logging.error(f"Database error while trying to hide URL '{url_to_hide}': {e}", exc_info=True)
        if conn: conn.rollback() # Rollback on error
        return jsonify({"success": False, "message": f"Database error occurred."}), 500 # Generic DB error message
    except Exception as e:
        logging.error(f"Unexpected error in api_hide_listing for URL '{url_to_hide}': {e}", exc_info=True)
        if conn: conn.rollback()
        return jsonify({"success": False, "message": "An unexpected server error occurred."}), 500
    finally:
        if conn:
            conn.close()
            # logging.debug("Database connection closed for hide request.")


# --- API Endpoint: Toggle Favorite ---
@app.route('/api/toggle_favorite', methods=['POST'])
def api_toggle_favorite():
    """API endpoint to toggle the favorite status of a listing."""
    logging.debug("Received request for /api/toggle_favorite")
    if not request.is_json:
        logging.warning("Favorite API request rejected: Content-Type is not application/json")
        return jsonify({"success": False, "message": "Request must be JSON"}), 415

    data = request.get_json()
    if not data:
        logging.warning("Favorite API request failed: Empty JSON body")
        return jsonify({"success": False, "message": "Missing JSON body"}), 400

    url_to_toggle = data.get('url')

    if not url_to_toggle or not isinstance(url_to_toggle, str) or not url_to_toggle.startswith('http'):
        logging.warning(f"Favorite API request failed: Invalid or missing URL - {url_to_toggle}")
        return jsonify({"success": False, "message": "Invalid or missing 'url' parameter in JSON body"}), 400

    conn = None
    try:
        conn = get_db_connection()
        if conn is None:
            return jsonify({"success": False, "message": "Database connection failed."}), 500

        cursor = conn.cursor()
        logging.info(f"Attempting to toggle favorite status for URL: {url_to_toggle}")

        # Toggle favorite status: favorite = 1 - favorite or favorite = NOT favorite
        cursor.execute("UPDATE listings SET favorite = (1 - favorite) WHERE url = ?", (url_to_toggle,))
        conn.commit()

        if cursor.rowcount > 0:
            # Fetch the new state to return to the frontend
            cursor.execute("SELECT favorite FROM listings WHERE url = ?", (url_to_toggle,))
            result = cursor.fetchone()
            new_state = bool(result['favorite']) if result else None # Convert 0/1 to boolean

            if new_state is not None:
                 logging.info(f"Successfully toggled favorite status for: {url_to_toggle}. New state: {new_state}")
                 return jsonify({"success": True, "message": "Favorite status toggled.", "isFavorite": new_state})
            else:
                 # Should not happen if update was successful, but handle defensively
                 logging.error(f"Updated favorite for {url_to_toggle} but could not fetch new state.")
                 return jsonify({"success": False, "message": "Favorite status updated, but could not confirm new state."}), 500
        else:
            logging.warning(f"URL not found in database, could not toggle favorite: {url_to_toggle}")
            return jsonify({"success": False, "message": "Listing URL not found in database."}), 404 # Not Found

    except sqlite3.Error as e:
        logging.error(f"Database error while trying to toggle favorite for '{url_to_toggle}': {e}", exc_info=True)
        if conn: conn.rollback()
        return jsonify({"success": False, "message": f"Database error occurred."}), 500
    except Exception as e:
        logging.error(f"Unexpected error in api_toggle_favorite for URL '{url_to_toggle}': {e}", exc_info=True)
        if conn: conn.rollback()
        return jsonify({"success": False, "message": "An unexpected server error occurred."}), 500
    finally:
        if conn:
            conn.close()
            # logging.debug("Database connection closed for favorite toggle request.")


# --- Main Execution Block ---
if __name__ == '__main__':
    logging.info("Checking Database status before starting Flask server...")
    # Ensure DB exists and schema is potentially updated on startup
    if SCRAPER_IMPORT_SUCCESS:
        try:
            init_db() # This now includes schema checks/updates for hidden/favorite
            logging.info("Database schema check/update completed.")
        except Exception as e:
            logging.error(f"Failed DB initialization/check on startup: {e}. Web app might have issues.", exc_info=True)
            # Decide if you want to exit or continue with potential errors
            # exit(1)
    else:
        logging.warning("Scraper import failed, cannot automatically check/initialize database.")

    logging.info("Starting Flask server on host 0.0.0.0, port 5000...")
    # Use threaded=True for development handling multiple requests somewhat concurrently
    # For production deployment, use a proper WSGI server (like Gunicorn or Waitress) instead of Flask's built-in server.
    # Example using Waitress (install with pip install waitress):
    # from waitress import serve
    # serve(app, host='0.0.0.0', port=5000)
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True) # threaded=True helpful for dev