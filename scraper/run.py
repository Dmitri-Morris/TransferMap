#!/usr/bin/env python3
"""
TransferMap GT Scraper

A production-quality scraper for Georgia Tech's Transfer Equivalency system.
Crawls all Georgia schools and subjects to build a comprehensive transfer credit database.

Setup:
1. Create virtual environment: python -m venv .venv
2. Activate: source .venv/bin/activate (Linux/Mac) or .venv\Scripts\activate (Windows)
3. Install dependencies: pip install -r requirements.txt
4. Copy .env.example to .env and configure as needed
5. Run: python run.py

Filtering for testing:
- Set SCHOOL_NAME_FILTER=Abraham Baldwin to test with one school
- Set SUBJECT_PREFIX_FILTER=BIOL to test with biology subjects only

Outputs:
- SQLite database: ../data/transfermap.db
- JSON snapshots: ../data/schools/{school-slug}.json
- Debug HTML: ../data/debug_*.html (when errors occur)
"""

import os
import sys
import time
import json
import sqlite3
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse
from typing import Dict, List, Optional, Tuple, Any
import logging

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from tqdm import tqdm
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
BASE_URL = os.getenv('BASE_URL', 'https://oscar.gatech.edu/pls/bprod/wwsktrna.P_find_location')
STATE_NAME = os.getenv('STATE_NAME', 'Georgia')
LEVEL = os.getenv('LEVEL', 'Undergraduate')
SEMESTER = os.getenv('SEMESTER', 'Fall 2025')
REQUESTS_PER_MINUTE = int(os.getenv('REQUESTS_PER_MINUTE', '8'))
RETRY_MAX = int(os.getenv('RETRY_MAX', '3'))
RETRY_BACKOFF_SECONDS = float(os.getenv('RETRY_BACKOFF_SECONDS', '2'))
USER_AGENT = os.getenv('USER_AGENT', 'TransferMapGT/0.1 (student project; contact: myemail@example.com)')
DB_PATH = os.getenv('DB_PATH', '../data/transfermap.db')

# Optional filters for testing
SCHOOL_NAME_FILTER = os.getenv('SCHOOL_NAME_FILTER')
SUBJECT_PREFIX_FILTER = os.getenv('SUBJECT_PREFIX_FILTER')

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Rate limiting
REQUEST_INTERVAL = 60.0 / REQUESTS_PER_MINUTE  # seconds between requests


class TransferMapScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': USER_AGENT})
        self.last_request_time = 0
        
        # Ensure output directories exist
        Path('../data').mkdir(exist_ok=True)
        Path('../data/schools').mkdir(exist_ok=True)
        
        # Initialize database
        self.ensure_schema()
    
    def throttle(self):
        """Rate limiting: ensure minimum interval between requests."""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        if time_since_last < REQUEST_INTERVAL:
            sleep_time = REQUEST_INTERVAL - time_since_last
            time.sleep(sleep_time)
        self.last_request_time = time.time()
    
    @retry(
        stop=stop_after_attempt(RETRY_MAX),
        wait=wait_exponential(multiplier=RETRY_BACKOFF_SECONDS),
        retry=retry_if_exception_type((requests.RequestException, ConnectionError))
    )
    def fetch(self, method: str, url: str, **kwargs) -> requests.Response:
        """Fetch with retries and rate limiting."""
        self.throttle()
        
        try:
            response = self.session.request(method, url, timeout=30, **kwargs)
            
            # Handle HTTP 429 (Too Many Requests)
            if response.status_code == 429:
                retry_after = int(response.headers.get('Retry-After', 60))
                logger.warning(f"Rate limited, sleeping {retry_after} seconds")
                time.sleep(retry_after)
                raise requests.RequestException("Rate limited")
            
            response.raise_for_status()
            return response
            
        except requests.RequestException as e:
            logger.error(f"Request failed: {e}")
            raise
    
    def dump_debug(self, content: str, tag: str):
        """Save debug HTML for inspection."""
        debug_path = f'../data/debug_{tag}.html'
        with open(debug_path, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.error(f"Debug HTML saved to {debug_path}")
    
    def first_form(self, soup: BeautifulSoup) -> Optional[BeautifulSoup]:
        """Find the first form on the page."""
        return soup.find('form')
    
    def build_post(self, form: BeautifulSoup, overrides: Dict[str, str], current_url: str) -> Tuple[str, Dict[str, str]]:
        """Build POST data from form with overrides."""
        action = form.get('action', '')
        post_url = urljoin(current_url, action)
        
        # Start with all hidden inputs
        data = {}
        for hidden in form.find_all('input', type='hidden'):
            name = hidden.get('name')
            value = hidden.get('value', '')
            if name:
                data[name] = value
        
        # Add default select values
        for select in form.find_all('select'):
            name = select.get('name')
            if name and name not in overrides:
                selected = select.find('option', selected=True)
                if selected:
                    data[name] = selected.get('value', '')
                else:
                    # Use first option if none selected
                    first_option = select.find('option')
                    if first_option:
                        data[name] = first_option.get('value', '')
        
        # Apply overrides
        data.update(overrides)
        
        return post_url, data
    
    def select_option_by_text(self, form: BeautifulSoup, option_text: str) -> Optional[Tuple[str, str]]:
        """Find select and option value by visible text."""
        for select in form.find_all('select'):
            for option in select.find_all('option'):
                if option.get_text(strip=True) == option_text:
                    return select.get('name'), option.get('value', '')
        return None
    
    def select_all_option_values(self, select: BeautifulSoup) -> List[Tuple[str, str]]:
        """Get all option values and texts from a select."""
        options = []
        for option in select.find_all('option'):
            value = option.get('value', '')
            text = option.get_text(strip=True)
            if value and text:  # Skip empty options
                options.append((value, text))
        return options
    
    def find_largest_select(self, form: BeautifulSoup) -> Optional[BeautifulSoup]:
        """Find the select with the most options (typically subjects)."""
        largest_select = None
        max_options = 0
        
        for select in form.find_all('select'):
            option_count = len(select.find_all('option'))
            if option_count > max_options:
                max_options = option_count
                largest_select = select
        
        return largest_select
    
    def find_school_select(self, form: BeautifulSoup) -> Optional[BeautifulSoup]:
        """Find the school select by name pattern or size."""
        # First try by name pattern
        for select in form.find_all('select'):
            name = select.get('name', '').lower()
            if any(keyword in name for keyword in ['school', 'inst', 'college', 'univ']):
                return select
        
        # Fallback to largest select
        return self.find_largest_select(form)
    
    def step_us_yes(self, start_url: str) -> str:
        """Step 1: Answer 'Yes' to US institution question."""
        response = self.fetch('GET', start_url)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        form = self.first_form(soup)
        if not form:
            self.dump_debug(response.text, 'us_yes')
            raise Exception("Could not find form for US question")
        
        # Find submit button with "Yes"
        submit_data = {}
        for input_elem in form.find_all('input', type='submit'):
            if 'yes' in input_elem.get('value', '').lower():
                submit_data[input_elem.get('name', '')] = input_elem.get('value', '')
                break
        
        post_url, data = self.build_post(form, submit_data, response.url)
        response = self.fetch('POST', post_url, data=data)
        
        return response.url
    
    def step_choose_state(self, current_url: str) -> str:
        """Step 2: Choose the state."""
        response = self.fetch('GET', current_url)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        form = self.first_form(soup)
        if not form:
            self.dump_debug(response.text, 'state')
            raise Exception("Could not find form for state selection")
        
        # Find state select and value
        state_info = self.select_option_by_text(form, STATE_NAME)
        if not state_info:
            self.dump_debug(response.text, 'state')
            raise Exception(f"Could not find state '{STATE_NAME}'")
        
        state_name, state_value = state_info
        
        # Find submit button
        submit_data = {state_name: state_value}
        for input_elem in form.find_all('input', type='submit'):
            value = input_elem.get('value', '').lower()
            if 'state' in value or 'get' in value:
                submit_data[input_elem.get('name', '')] = input_elem.get('value', '')
                break
        
        post_url, data = self.build_post(form, submit_data, response.url)
        response = self.fetch('POST', post_url, data=data)
        
        return response.url
    
    def step_list_schools(self, current_url: str) -> List[Tuple[str, str]]:
        """Step 3: Get list of all schools."""
        response = self.fetch('GET', current_url)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        form = self.first_form(soup)
        if not form:
            self.dump_debug(response.text, 'schools')
            raise Exception("Could not find form for school selection")
        
        # Find school select
        school_select = self.find_school_select(form)
        if not school_select:
            self.dump_debug(response.text, 'schools')
            raise Exception("Could not find school select")
        
        schools = self.select_all_option_values(school_select)
        logger.info(f"Found {len(schools)} schools")
        
        # Apply filter if configured
        if SCHOOL_NAME_FILTER:
            schools = [(value, text) for value, text in schools 
                      if text.lower().startswith(SCHOOL_NAME_FILTER.lower())]
            logger.info(f"Filtered to {len(schools)} schools matching '{SCHOOL_NAME_FILTER}'")
        
        return schools
    
    def step_choose_school(self, current_url: str, school_value: str, school_name: str) -> str:
        """Step 4: Choose a specific school."""
        response = self.fetch('GET', current_url)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        form = self.first_form(soup)
        if not form:
            self.dump_debug(response.text, 'choose_school')
            raise Exception("Could not find form for school choice")
        
        school_select = self.find_school_select(form)
        if not school_select:
            self.dump_debug(response.text, 'choose_school')
            raise Exception("Could not find school select")
        
        school_field_name = school_select.get('name')
        
        # Find submit button
        submit_data = {school_field_name: school_value}
        for input_elem in form.find_all('input', type='submit'):
            value = input_elem.get('value', '').lower()
            if 'school' in value or 'get' in value:
                submit_data[input_elem.get('name', '')] = input_elem.get('value', '')
                break
        
        post_url, data = self.build_post(form, submit_data, response.url)
        response = self.fetch('POST', post_url, data=data)
        
        return response.url
    
    def step_subject_level_term(self, current_url: str) -> Tuple[str, List[Tuple[str, str]], str, str, str]:
        """Step 5: Get subjects and prepare level/term selection."""
        response = self.fetch('GET', current_url)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        form = self.first_form(soup)
        if not form:
            self.dump_debug(response.text, 'subjects')
            raise Exception("Could not find form for subject/level/term")
        
        # Find subjects (largest select)
        subject_select = self.find_largest_select(form)
        if not subject_select:
            self.dump_debug(response.text, 'subjects')
            raise Exception("Could not find subject select")
        
        subjects = self.select_all_option_values(subject_select)
        subject_field_name = subject_select.get('name')
        
        # Apply subject filter
        if SUBJECT_PREFIX_FILTER:
            subjects = [(value, text) for value, text in subjects 
                       if text.startswith(SUBJECT_PREFIX_FILTER)]
            logger.info(f"Filtered to {len(subjects)} subjects matching '{SUBJECT_PREFIX_FILTER}'")
        
        # Find level select
        level_info = self.select_option_by_text(form, LEVEL)
        if not level_info:
            self.dump_debug(response.text, 'subjects')
            raise Exception(f"Could not find level '{LEVEL}'")
        level_field_name, level_value = level_info
        
        # Find term select
        term_info = self.select_option_by_text(form, SEMESTER)
        if not term_info:
            self.dump_debug(response.text, 'subjects')
            raise Exception(f"Could not find semester '{SEMESTER}'")
        term_field_name, term_value = term_info
        
        return current_url, subjects, subject_field_name, level_field_name, term_field_name
    
    def submit_subject(self, base_url: str, subject_value: str, subject_field_name: str, 
                      level_field_name: str, term_field_name: str) -> str:
        """Submit subject selection and get equivalency table."""
        response = self.fetch('GET', base_url)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        form = self.first_form(soup)
        if not form:
            raise Exception("Could not find form for subject submission")
        
        # Find level and term values again
        level_info = self.select_option_by_text(form, LEVEL)
        term_info = self.select_option_by_text(form, SEMESTER)
        
        if not level_info or not term_info:
            raise Exception("Could not find level or term values")
        
        _, level_value = level_info
        _, term_value = term_info
        
        # Build submission data
        submit_data = {
            subject_field_name: subject_value,
            level_field_name: level_value,
            term_field_name: term_value
        }
        
        # Find submit button
        for input_elem in form.find_all('input', type='submit'):
            value = input_elem.get('value', '').lower()
            if 'course' in value or 'get' in value or 'submit' in value:
                submit_data[input_elem.get('name', '')] = input_elem.get('value', '')
                break
        
        post_url, data = self.build_post(form, submit_data, response.url)
        response = self.fetch('POST', post_url, data=data)
        
        return response.text
    
    def normalize_gt_course_code(self, code: str) -> str:
        """Normalize GT course code: 'CS1331' -> 'CS 1331', 'BIOS1107L' -> 'BIOS 1107L'."""
        code = code.strip()
        # Insert space between letters and digits
        normalized = re.sub(r'([A-Za-z]+)(\d+)', r'\1 \2', code)
        return normalized
    
    def parse_equivalency_table(self, html_content: str, subject: str) -> List[Dict[str, Any]]:
        """Parse the final equivalency table."""
        soup = BeautifulSoup(html_content, 'html.parser')
        equivalencies = []
        
        # Find the main data table
        # Look for tables with substantial content
        tables = soup.find_all('table')
        main_table = None
        
        for table in tables:
            rows = table.find_all('tr')
            if len(rows) > 2:  # Has header and data rows
                # Check if this looks like an equivalency table
                header_row = rows[0] if rows else None
                if header_row and ('class' in header_row.get_text().lower() or 
                                  'title' in header_row.get_text().lower()):
                    main_table = table
                    break
        
        if not main_table:
            logger.warning(f"Could not find equivalency table for subject {subject}")
            return equivalencies
        
        rows = main_table.find_all('tr')
        if len(rows) < 2:
            return equivalencies
        
        # Parse header to understand column structure
        header_row = rows[0]
        header_cells = header_row.find_all(['th', 'td'])
        
        # Find column indices
        col_headers = [cell.get_text(strip=True).lower() for cell in header_cells]
        
        # Look for key columns
        external_class_idx = None
        external_title_idx = None
        gt_class_idx = None
        gt_title_idx = None
        credit_hours_indices = []
        
        for i, header in enumerate(col_headers):
            if 'class' in header and (external_class_idx is None):
                external_class_idx = i
            elif 'title' in header and (external_title_idx is None):
                external_title_idx = i
            elif 'class' in header and (gt_class_idx is None) and i != external_class_idx:
                gt_class_idx = i
            elif 'title' in header and (gt_title_idx is None) and i != external_title_idx:
                gt_title_idx = i
            elif 'credit' in header and 'hour' in header:
                credit_hours_indices.append(i)
        
        # If we couldn't find clear patterns, make assumptions based on typical layout
        if external_class_idx is None or gt_class_idx is None:
            # Assume left side is external, right side is GT
            mid_point = len(col_headers) // 2
            for i, header in enumerate(col_headers):
                if 'class' in header and i < mid_point and external_class_idx is None:
                    external_class_idx = i
                elif 'class' in header and i >= mid_point and gt_class_idx is None:
                    gt_class_idx = i
                elif 'title' in header and i < mid_point and external_title_idx is None:
                    external_title_idx = i
                elif 'title' in header and i >= mid_point and gt_title_idx is None:
                    gt_title_idx = i
        
        # Process data rows
        for row in rows[1:]:
            cells = row.find_all(['td', 'th'])
            if len(cells) < max(external_class_idx or 0, gt_class_idx or 0) + 1:
                continue
            
            # Extract external course info
            external_class = cells[external_class_idx].get_text(strip=True) if external_class_idx is not None else ""
            external_title = cells[external_title_idx].get_text(strip=True) if external_title_idx is not None else ""
            
            # Extract GT course info
            gt_class = cells[gt_class_idx].get_text(strip=True) if gt_class_idx is not None else ""
            gt_title = cells[gt_title_idx].get_text(strip=True) if gt_title_idx is not None else ""
            
            # Skip ET DEPT rows
            if "ET DEPT" in gt_class:
                continue
            
            # Skip empty rows
            if not external_class or not gt_class:
                continue
            
            # Extract credit hours
            external_credit_hours = 3.0  # Default
            gt_credit_hours = 3.0  # Default
            
            if credit_hours_indices:
                try:
                    if len(credit_hours_indices) >= 2:
                        # Two credit hour columns: left is external, right is GT
                        external_credit_hours = float(cells[credit_hours_indices[0]].get_text(strip=True) or "3.0")
                        gt_credit_hours = float(cells[credit_hours_indices[-1]].get_text(strip=True) or "3.0")
                    else:
                        # One credit hour column: assume it's GT hours
                        gt_credit_hours = float(cells[credit_hours_indices[0]].get_text(strip=True) or "3.0")
                        external_credit_hours = gt_credit_hours  # Default external = GT
                except (ValueError, IndexError):
                    pass  # Keep defaults
            
            # Normalize GT course code
            normalized_gt_code = self.normalize_gt_course_code(gt_class)
            
            equivalency = {
                'subject': subject,
                'schoolCourseCode': external_class,
                'schoolCourseName': external_title,
                'schoolCreditHours': external_credit_hours,
                'gtCourseCode': normalized_gt_code,
                'gtCourseName': gt_title,
                'gtCreditHours': gt_credit_hours
            }
            
            equivalencies.append(equivalency)
        
        return equivalencies
    
    def ensure_schema(self):
        """Ensure database schema exists."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # The schema is already provided - we just need to ensure it exists
        # But since the requirement states "tables exist; keep this exact shape",
        # we'll just verify connection works
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        logger.info(f"Database connected. Tables: {[t[0] for t in tables]}")
        
        conn.close()
    
    def create_school_slug(self, school_name: str) -> str:
        """Create URL-safe slug from school name."""
        slug = re.sub(r'[^\w\s-]', '', school_name.lower())
        slug = re.sub(r'[-\s]+', '-', slug)
        return slug.strip('-')
    
    def upsert_school(self, name: str) -> int:
        """Insert or get school ID."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("INSERT OR IGNORE INTO School (name) VALUES (?)", (name,))
        cursor.execute("SELECT id FROM School WHERE name = ?", (name,))
        school_id = cursor.fetchone()[0]
        
        conn.commit()
        conn.close()
        return school_id
    
    def upsert_gt_course(self, code: str, title: str, credit_hours: float) -> int:
        """Insert or update GT course."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR IGNORE INTO GTCourse (code, title, creditHours) 
            VALUES (?, ?, ?)
        """, (code, title, credit_hours))
        
        cursor.execute("SELECT id FROM GTCourse WHERE code = ?", (code,))
        course_id = cursor.fetchone()[0]
        
        conn.commit()
        conn.close()
        return course_id
    
    def upsert_external_course(self, school_id: int, code: str, title: str, credit_hours: float):
        """Insert or ignore external course."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR IGNORE INTO ExternalCourse (schoolId, code, title, creditHours) 
            VALUES (?, ?, ?, ?)
        """, (school_id, code, title, credit_hours))
        
        conn.commit()
        conn.close()
    
    def upsert_equivalency(self, gt_course_id: int, school_id: int, external_course_code: str, semester: str):
        """Insert or update equivalency."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO Equivalency (gtCourseId, schoolId, externalCourseCode, semester) 
            VALUES (?, ?, ?, ?)
            ON CONFLICT(gtCourseId, schoolId, externalCourseCode) 
            DO UPDATE SET semester = excluded.semester
        """, (gt_course_id, school_id, external_course_code, semester))
        
        conn.commit()
        conn.close()
    
    def save_school_snapshot(self, school_name: str, equivalencies: List[Dict[str, Any]]):
        """Save JSON snapshot for a school."""
        slug = self.create_school_slug(school_name)
        
        snapshot = {
            "school": school_name,
            "semester": SEMESTER,
            "level": LEVEL,
            "subjects_count": len(set(eq['subject'] for eq in equivalencies)),
            "equivalencies": equivalencies
        }
        
        with open(f'../data/schools/{slug}.json', 'w') as f:
            json.dump(snapshot, f, indent=2)
        
        logger.info(f"Saved snapshot for {school_name}: {len(equivalencies)} equivalencies")
    
    def run(self):
        """Main scraper execution."""
        logger.info("Starting TransferMap GT scraper...")
        logger.info(f"Target: {STATE_NAME} schools, {LEVEL} level, {SEMESTER}")
        
        try:
            # Step 1: Answer US question
            logger.info("Step 1: Answering US institution question...")
            state_url = self.step_us_yes(BASE_URL)
            
            # Step 2: Choose state
            logger.info(f"Step 2: Selecting state '{STATE_NAME}'...")
            schools_url = self.step_choose_state(state_url)
            
            # Step 3: Get list of schools
            logger.info("Step 3: Getting list of schools...")
            schools = self.step_list_schools(schools_url)
            
            logger.info(f"Processing {len(schools)} schools...")
            
            # Process each school
            for school_value, school_name in tqdm(schools, desc="Schools"):
                try:
                    logger.info(f"Processing school: {school_name}")
                    
                    # Step 4: Choose school
                    subject_url = self.step_choose_school(schools_url, school_value, school_name)
                    
                    # Step 5: Get subjects and form field names
                    base_url, subjects, subject_field, level_field, term_field = self.step_subject_level_term(subject_url)
                    
                    logger.info(f"Found {len(subjects)} subjects for {school_name}")
                    
                    # Upsert school
                    school_id = self.upsert_school(school_name)
                    
                    all_equivalencies = []
                    
                    # Process each subject
                    for subject_value, subject_name in tqdm(subjects, desc=f"Subjects for {school_name}", leave=False):
                        try:
                            # Submit subject and get equivalency table
                            html_content = self.submit_subject(base_url, subject_value, subject_field, level_field, term_field)
                            
                            # Parse equivalencies
                            equivalencies = self.parse_equivalency_table(html_content, subject_name)
                            
                            # Store in database
                            for eq in equivalencies:
                                # Upsert GT course
                                gt_course_id = self.upsert_gt_course(
                                    eq['gtCourseCode'], 
                                    eq['gtCourseName'], 
                                    eq['gtCreditHours']
                                )
                                
                                # Upsert external course
                                self.upsert_external_course(
                                    school_id,
                                    eq['schoolCourseCode'],
                                    eq['schoolCourseName'],
                                    eq['schoolCreditHours']
                                )
                                
                                # Upsert equivalency
                                self.upsert_equivalency(
                                    gt_course_id,
                                    school_id,
                                    eq['schoolCourseCode'],
                                    SEMESTER
                                )
                            
                            all_equivalencies.extend(equivalencies)
                            
                            if equivalencies:
                                logger.info(f"  {subject_name}: {len(equivalencies)} equivalencies")
                        
                        except Exception as e:
                            logger.error(f"Error processing subject {subject_name} for {school_name}: {e}")
                            continue
                    
                    # Save school snapshot
                    if all_equivalencies:
                        self.save_school_snapshot(school_name, all_equivalencies)
                    
                    logger.info(f"Completed {school_name}: {len(all_equivalencies)} total equivalencies")
                
                except Exception as e:
                    logger.error(f"Error processing school {school_name}: {e}")
                    continue
        
        except Exception as e:
            logger.error(f"Fatal error in scraper: {e}")
            raise
        
        logger.info("Scraper completed successfully!")


if __name__ == "__main__":
    scraper = TransferMapScraper()
    scraper.run()