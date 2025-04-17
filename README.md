# 📊 Marktplaats Scraper & Dashboard

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) <!-- Add a LICENSE file -->

Monitor Marktplaats listings with this powerful Python-based scraper featuring a Tkinter management GUI and a Flask web dashboard with map visualization.

This application allows you to define specific search queries for Marktplaats.nl, periodically scrapes the site for new listings matching your criteria, stores the findings in a local database, notifies you via Telegram (optional), and presents the results in a user-friendly web interface.

---

## ✨ Features

*   **📝 Configurable Queries:** Define multiple search queries with specific criteria (keywords, price range, condition, location, distance, age) via `queries.json`.
*   **🖥️ Management GUI:** A Tkinter-based interface (`scraper.py`) to add, edit, delete, activate/deactivate search queries and monitor logs.
*   **📈 Web Dashboard:** A Flask-based web application (`webapp.py`) to view scraped listings, see scanner status, and visualize listings geographically.
*   **🗺️ Map Visualization:** Uses Leaflet.js in the web dashboard to display listings with coordinates on an interactive map, color-coded by age.
*   **⭐ Favorites & ❌ Hiding:** Mark listings as favorites or hide them directly within the web dashboard for a personalized view. Changes persist in the local database.
*   **📲 Telegram Notifications:** Optional real-time notifications for newly found listings via a Telegram bot.
*   **💾 Persistent Storage:** Uses SQLite (`marktplaats_listings.db`) to store scraped listing data, hidden items, and favorites.
*   **⚙️ Status Monitoring:** The web app displays the scraper's current status (Idle, Running, Pending, Offline, Error) based on status/trigger files.
*   **🛡️ Duplicate Prevention:** Avoids adding the same listing multiple times to the database.

---

## 📸 Screenshots


Here's a visual overview of the different components and features of the project:

| Feature / Component             | Screenshot                                                                                                                        |
| :------------------------------ | :-------------------------------------------------------------------------------------------------------------------------------- |
| **Python GUI - Start/Stop & Logs** | <img src ="https://github.com/user-attachments/assets/6c43fb71-3a82-45bb-961d-ac1fd0e77ae0" alt="Python GUI showing start/stop controls and logs" width="500"/> 
| **Python GUI Query Management** | <img src="https://github.com/user-attachments/assets/8b38ad74-b072-44d2-a2b3-29e27211d046" alt="Python GUI for managing queries" "/> |
| **Webapp - Interactive Map**    | <img src="https://github.com/user-attachments/assets/46bcd658-2889-4154-bee0-a36d88086bf0" alt="Webapp showing data on an interactive map" /> |
| **Webapp - Data View**          | <img src="https://github.com/user-attachments/assets/7d4dc098-f32e-4db8-97c0-e146bf5ba3ab" alt="Webapp displaying detailed data view" /> |
| **Webapp - Database View**      | <img src="https://github.com/user-attachments/assets/d451b711-2934-40e3-bd64-91bb53749230" alt="Webapp showing database table contents" /> |
| **Webapp - FAQ Page**           | <img src="https://github.com/user-attachments/assets/6b06c730-ba0a-41b3-86e6-2769749d3a2f" alt="Webapp Frequently Asked Questions page" width="400"/> |
| **Example Telegram Notification** | <img src="https://github.com/user-attachments/assets/f93129bd-9ee8-43ee-8b54-205b5bd9c6ee" alt="Example notification message sent via Telegram" width="400"/> |

## 🛠️ Technology Stack

*   **Backend:** Python 3
*   **Scraping:** `requests`, `beautifulsoup4`
*   **Database:** `sqlite3`
*   **Management GUI:** `tkinter` (standard library)
*   **Web Framework:** `Flask`
*   **Geolocation:** `geopy`
*   **Telegram Bot:** `python-telegram-bot`
*   **Web Frontend:** HTML, CSS, JavaScript
*   **Mapping Library:** `Leaflet.js`

---

## 🏗️ Architecture Overview

The application consists of two main parts that run independently but share data:

1.  **`scraper.py` (Scraper & GUI):**
    *   Manages search queries (`queries.json`).
    *   Runs the core scraping logic based on active queries.
    *   Parses listing data and performs geocoding.
    *   Writes new/unique listings (including hidden/favorites) to the SQLite database (`marktplaats_listings.db`).
    *   Updates status (`scan_status.txt`) and statistics (`scan_stats.json`).
    *   Sends Telegram notifications.
    *   Listens for a trigger file (`scan_trigger.now`) created by the web app.
    *   Provides a desktop GUI for management and logging.

2.  **`webapp.py` (Web Dashboard):**
    *   A Flask server that reads data from the shared SQLite database (`marktplaats_listings.db`) to display listings.
    *   Reads `scan_stats.json` and `scan_status.txt` to display current statistics and scraper status.
    *   Reads `queries.json` to show the active query count.
    *   Allows users to trigger a scan by creating the `scan_trigger.now` file.
    *   Provides API endpoints (`/api/...`) for the frontend JavaScript to interact with the hidden/favorites tables in the database.
    *   Renders the `listings.html` template with data and the Leaflet map.

**Communication:** Primarily indirect via the shared SQLite database and simple status/stats/trigger files.

---

## 🚀 Setup and Installation ( .exe coming soon)

1.  **Prerequisites:**
    *   Python 3.8 or higher installed.
    *   `pip` (Python package installer).

2.  **Clone the Repository:**
    ```bash
    git clone <your-repository-url>
    cd marktplaats-monitor # Or your repository directory name
    ```

3.  **Create a Virtual Environment (Recommended):**
    ```bash
    python -m venv venv
    # Activate the environment:
    # Windows:
    .\venv\Scripts\activate
    # macOS/Linux:
    source venv/bin/activate
    ```

4.  **Install Dependencies:**
    *   **Create a `requirements.txt` file** in the root directory with the following content:
        ```txt
        requests
        beautifulsoup4
        Flask
        python-telegram-bot
        geopy
        # Add any other specific libraries if needed
        ```
    *   Install the requirements:
        ```bash
        pip install -r requirements.txt
        ```

---

## ⚙️ Configuration

Before running, configure the following files:

1.  **`config.ini`** (Create this file in the root directory if it doesn't exist)
    *   Used primarily by `scraper.py`.
    *   Contains sections like `[Telegram]`, `[Database]`, `[Scraper]`, `[Logging]`.
    *   **Important:** Fill in your `BotToken` and `ChatID` under `[Telegram]` if you want notifications.
    *   You can adjust `RequestDelay`, `CheckIntervalMinutes`, `UserAgent`, database path, log file path, etc.
    *   If the file is missing, defaults will be used (but Telegram won't work without credentials).

2.  **`queries.json`**
    *   Stores your specific Marktplaats search queries and filters.
    *   Managed through the Tkinter GUI (`scraper.py`). You can add/edit queries there.
    *   Each query object contains the base `url`, `active` status, `min_price`, `max_price`, `conditions`, `keywords`, `postcode`, `distanceMeters`, etc.

---

## ▶️ Running the Application

You need to run the scraper/GUI and the web app separately (usually in different terminals).

1.  **Run the Scraper GUI (`scraper.py`):**
    *   Make sure your virtual environment is activated.
    *   Navigate to the project directory.
    *   Execute:
        ```bash
        python scraper.py
        ```
    *   Use the GUI to manage your queries (`queries.json`) and start/stop the scraping process. The scraper needs to be running (or started via the GUI) for new data to appear and for the web app's "Trigger Scan" to work.

2.  **Run the Web Application (`webapp.py`):**
    *   Make sure your virtual environment is activated.
    *   Navigate to the project directory.
    *   Execute:
        ```bash
        python webapp.py
        ```
    *   The web server will start (usually on `http://localhost:5000` or `http://0.0.0.0:5000`). Access the dashboard in your web browser using the provided URL (e.g., `http://127.0.0.1:5000`).
    *   The web app reads data generated by the scraper process.

---

## 🖱️ Usage

*   **GUI (`scraper.py`):**
    *   Use the "Query Management" tab to add new search URLs, define filters (price, keywords, etc.), activate/deactivate queries, and save changes.
    *   Use the "Run & Logs" tab to start/stop the background scraping process, view logs, and adjust the scan interval.
    *   New listings found by active queries will appear in the "Results" tab and be sent via Telegram (if configured).
*   **Web Dashboard (Browser):**
    *   View the latest found (and not hidden) listings.
    *   See the scraper's status and statistics.
    *   Use the map to see listing locations.
    *   Click the "Trigger Scan Now" button to request an immediate scan cycle from the running `scraper.py` process.
    *   Use the ⭐ button on a listing card to mark it as a favorite.
    *   Use the ❌ button on a listing card to permanently hide it from the web view.
    *   Use the "Show Favorites Only" toggle to filter the listing view.

---

## 💡 Future Enhancements

*   More advanced web UI filtering/sorting options.
*   User accounts for personalized hidden/favorite lists (if multiple users access the web app).
*   Error handling improvements in both scraper and web app.
*   Option to edit queries directly via the web interface.
*   Dockerization for easier deployment.
*   Unit and integration tests.

---

## 🤝 Contributing

Contributions are welcome! Please feel free to open an issue to discuss bugs or feature requests, or submit a pull request.

1.  Fork the repository.
2.  Create your feature branch (`git checkout -b feature/AmazingFeature`).
3.  Commit your changes (`git commit -m 'Add some AmazingFeature'`).
4.  Push to the branch (`git push origin feature/AmazingFeature`).
5.  Open a Pull Request.

---

## 📜 License

   This project is licensed under the GLPL License 
