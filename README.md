# TransferMap

A comprehensive transfer credit equivalency system for Georgia Tech students. This project scrapes Georgia Tech's transfer equivalency database and provides a user-friendly interface to search for course equivalencies from other Georgia institutions.

## Project Structure

```
TransferMap/
├── client/          # React frontend (Vite + React)
├── server/          # Express.js API server
├── scraper/         # Python web scraper
├── data/            # Database and scraped data (excluded from git)
└── README.md        # This file
```

## Quick Start

### Prerequisites
- Node.js (v18 or higher)
- Python 3.8+
- Git

### 1. Clone the Repository
```bash
git clone https://github.com/yourusername/TransferMap.git
cd TransferMap
```

### 2. Set Up the Backend Server
```bash
cd server
npm install
npm run dev
```
The API server will run on `http://localhost:5175`

### 3. Set Up the Frontend Client
```bash
cd client
npm install
npm run dev
```
The React app will run on `http://localhost:5173`

### 4. Set Up the Data Scraper (Optional)
```bash
cd scraper
pip install -r requirements.txt
python run.py
```

## API Endpoints

### GET /api/equivalents
Search for course equivalencies by Georgia Tech course code.

**Parameters:**
- `gt` (required): GT course code (e.g., "CS1331", "CS 1331")

**Example:**
```bash
curl "http://localhost:5175/api/equivalents?gt=CS1331"
```

**Response:**
```json
{
  "items": [
    {
      "schoolName": "Georgia State University",
      "gtCourseCode": "CS 1331",
      "gtCourseName": "Introduction to Object Oriented Programming",
      "gtCreditHours": 3,
      "externalCourseCode": "CSC 2510",
      "externalCourseName": "Object Oriented Programming",
      "externalCreditHours": 3,
      "semester": "Fall 2025"
    }
  ],
  "total": 1
}
```

## Data Scraping

The Python scraper (`scraper/run.py`) automatically crawls Georgia Tech's transfer equivalency system to build a comprehensive database of course equivalencies.

### Scraper Features:
- **Rate Limited**: Respects server limits (8 requests/minute by default)
- **Retry Logic**: Automatic retries with exponential backoff
- **Error Handling**: Saves debug HTML for failed requests
- **Filtering**: Optional filters for testing specific schools/subjects
- **Data Export**: Saves both SQLite database and JSON snapshots

### Configuration:
Create a `.env` file in the `scraper/` directory:
```env
BASE_URL=https://oscar.gatech.edu/pls/bprod/wwsktrna.P_find_location
STATE_NAME=Georgia
LEVEL=Undergraduate
SEMESTER=Fall 2025
REQUESTS_PER_MINUTE=8
RETRY_MAX=3
USER_AGENT=TransferMapGT/0.1 (student project; contact: your-email@example.com)

# Optional filters for testing
SCHOOL_NAME_FILTER=Abraham Baldwin
SUBJECT_PREFIX_FILTER=BIOL
```

## Database Schema

The SQLite database contains four main tables:

- **School**: External institutions
- **GTCourse**: Georgia Tech courses
- **ExternalCourse**: Courses from other institutions
- **Equivalency**: Mapping between GT and external courses

## 🛠️ Development

### Frontend (React + Vite)
```bash
cd client
npm run dev      # Development server
npm run build    # Production build
npm run preview  # Preview production build
npm run lint     # Run ESLint
```

### Backend (Express.js)
```bash
cd server
npm run dev      # Development with nodemon
npm start        # Production server
```

### Scraper (Python)
```bash
cd scraper
python run.py    # Run full scrape
```

## ⚠️ Important Notes

- The scraper is designed to be respectful of Georgia Tech's servers
- Always check the current terms of service before running the scraper
- The database and scraped data files are excluded from version control
- Run the scraper to populate the database before using the API
