import os
import json
import re
import smtplib
import time
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from bs4 import BeautifulSoup
import pickle
import base64
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Gmail API setup
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

class JobTracker:
    def __init__(self):
        self.companies_file = 'companies.json'
        self.jobs_file = 'previous_jobs.json'
        self.load_data()
        
    def load_data(self):
        """Load existing company and job data"""
        try:
            with open(self.companies_file, 'r') as f:
                self.companies = json.load(f)
        except FileNotFoundError:
            self.companies = {}
            
        try:
            with open(self.jobs_file, 'r') as f:
                self.previous_jobs = json.load(f)
        except FileNotFoundError:
            self.previous_jobs = {}
    
    def save_data(self):
        """Save company and job data"""
        with open(self.companies_file, 'w') as f:
            json.dump(self.companies, f, indent=2)
        with open(self.jobs_file, 'w') as f:
            json.dump(self.previous_jobs, f, indent=2)
    
    def authenticate_gmail(self):
        """Authenticate with Gmail API"""
        creds = None
        if os.path.exists('token.pickle'):
            with open('token.pickle', 'rb') as token:
                creds = pickle.load(token)
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    'credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)
            with open('token.pickle', 'wb') as token:
                pickle.dump(creds, token)
        
        return build('gmail', 'v1', credentials=creds)
    
    def get_recent_emails(self, gmail_service, days_back=7):
        """Get recent 'Funded and Hiring' emails"""
        # Calculate date for search
        since_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y/%m/%d')
        
        # Search for emails
        query = f'subject:"funded and hiring" after:{since_date}'
        results = gmail_service.users().messages().list(
            userId='me', q=query).execute()
        
        messages = results.get('messages', [])
        
        email_contents = []
        for message in messages:
            msg = gmail_service.users().messages().get(
                userId='me', id=message['id']).execute()
            
            # Extract email body
            payload = msg['payload']
            body = self.extract_email_body(payload)
            if body:
                email_contents.append({
                    'date': msg['internalDate'],
                    'body': body
                })
        
        return email_contents
    
    def extract_email_body(self, payload):
        """Extract text content from email payload"""
        body = ""
        if 'parts' in payload:
            for part in payload['parts']:
                if part['mimeType'] == 'text/plain':
                    data = part['body']['data']
                    body = base64.urlsafe_b64decode(data).decode('utf-8')
                    break
        elif payload['mimeType'] == 'text/plain':
            data = payload['body']['data']
            body = base64.urlsafe_b64decode(data).decode('utf-8')
        
        return body
    
    def parse_funded_hiring_email(self, email_body):
        """Parse company info from 'Funded and Hiring' email"""
        companies = {}
        
        # Pattern to match company entries with links
        # Assumes format like: "Company Name - Description\nWebsite: https://...\nJobs: https://..."
        lines = email_body.split('\n')
        
        current_company = None
        for line in lines:
            line = line.strip()
            
            # Look for company names (usually standalone lines or start with capital)
            if line and not line.startswith(('http', 'www', 'Website:', 'Jobs:', 'Careers:')):
                # Check if this looks like a company name
                if len(line) < 100 and not any(word in line.lower() for word in ['the', 'and', 'funded', 'hiring']):
                    # Extract company name (remove description after dash/hyphen)
                    company_name = re.split(r'\s*[-–—]\s*', line)[0].strip()
                    if company_name:
                        current_company = company_name
                        companies[current_company] = {
                            'website': '',
                            'jobs_page': '',
                            'date_added': datetime.now().isoformat()
                        }
            
            # Look for website and job links
            elif current_company and ('http' in line or 'www' in line):
                urls = re.findall(r'https?://[^\s]+', line)
                for url in urls:
                    url = url.rstrip('.,;)')  # Clean trailing punctuation
                    
                    if any(keyword in line.lower() for keyword in ['job', 'career', 'hiring']):
                        companies[current_company]['jobs_page'] = url
                    elif not companies[current_company]['website']:
                        companies[current_company]['website'] = url
        
        return companies
    
    def add_companies_to_tracking(self, new_companies):
        """Add new companies to tracking list"""
        added_count = 0
        for company, info in new_companies.items():
            if company not in self.companies:
                self.companies[company] = info
                added_count += 1
                print(f"Added new company: {company}")
        
        self.save_data()
        return added_count
    
    def scrape_job_page(self, url, company_name):
        """Scrape jobs from a company's job page"""
        if not url:
            return []
        
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Common job listing selectors for popular ATS platforms
            job_selectors = [
                # Greenhouse
                '.opening',
                '.opening-title',
                '[data-test="job-title"]',
                # Lever
                '.posting-title',
                '.posting',
                # Workday
                '[data-automation-id*="job"]',
                # BambooHR
                '.BambooHR-ATS-Jobs-Item',
                # Generic patterns
                '.job-title',
                '.job-listing',
                '.position',
                '.role',
                'h3 a[href*="job"]',
                'a[href*="position"]',
                'a[href*="career"]'
            ]
            
            jobs = []
            for selector in job_selectors:
                elements = soup.select(selector)
                for element in elements:
                    job_title = element.get_text(strip=True)
                    
                    # Skip if this looks like engineering or legal
                    if self.should_exclude_role(job_title):
                        continue
                        
                    if job_title and len(job_title) > 3:
                        # Try to get the job URL
                        job_url = ""
                        if element.name == 'a':
                            job_url = element.get('href', '')
                        else:
                            link = element.find('a')
                            if link:
                                job_url = link.get('href', '')
                        
                        # Make relative URLs absolute
                        if job_url.startswith('/'):
                            from urllib.parse import urljoin
                            job_url = urljoin(url, job_url)
                        
                        jobs.append({
                            'title': job_title,
                            'url': job_url,
                            'company': company_name,
                            'scraped_date': datetime.now().isoformat()
                        })
                
                if jobs:  # If we found jobs with this selector, stop trying others
                    break
            
            # Remove duplicates
            seen_titles = set()
            unique_jobs = []
            for job in jobs:
                if job['title'] not in seen_titles:
                    seen_titles.add(job['title'])
                    unique_jobs.append(job)
            
            return unique_jobs[:20]  # Limit to prevent spam
            
        except Exception as e:
            print(f"Error scraping {company_name} ({url}): {str(e)}")
            return []
    
    def should_exclude_role(self, job_title):
        """Check if role should be excluded (engineering/legal)"""
        exclude_keywords = [
            'engineer', 'engineering', 'developer', 'software', 'frontend', 
            'backend', 'full stack', 'devops', 'sre', 'legal', 'counsel', 
            'attorney', 'lawyer', 'paralegal'
        ]
        
        job_lower = job_title.lower()
        return any(keyword in job_lower for keyword in exclude_keywords)
    
    def check_for_new_jobs(self):
        """Check all tracked companies for new job postings"""
        new_jobs = []
        
        for company, info in self.companies.items():
            print(f"Checking {company}...")
            
            # Try jobs page first, then website
            jobs_from_page = []
            if info.get('jobs_page'):
                jobs_from_page = self.scrape_job_page(info['jobs_page'], company)
            
            if not jobs_from_page and info.get('website'):
                # Try to find careers page on main website
                careers_urls = self.find_careers_page(info['website'])
                for careers_url in careers_urls:
                    jobs_from_page = self.scrape_job_page(careers_url, company)
                    if jobs_from_page:
                        break
            
            # Compare with previous jobs to find new ones
            company_key = company.lower().replace(' ', '_')
            previous_jobs = self.previous_jobs.get(company_key, [])
            previous_titles = {job['title'] for job in previous_jobs}
            
            for job in jobs_from_page:
                if job['title'] not in previous_titles:
                    new_jobs.append(job)
            
            # Update previous jobs
            self.previous_jobs[company_key] = jobs_from_page
        
        self.save_data()
        return new_jobs
    
    def find_careers_page(self, website_url):
        """Try to find careers/jobs page on company website"""
        try:
            response = requests.get(website_url, timeout=10)
            soup = BeautifulSoup(response.content, 'html.parser')
            
            careers_links = []
            # Look for common careers page patterns
            for link in soup.find_all('a', href=True):
                href = link['href'].lower()
                text = link.get_text().lower()
                
                if any(word in href or word in text for word in ['career', 'job', 'hiring', 'work']):
                    full_url = link['href']
                    if full_url.startswith('/'):
                        from urllib.parse import urljoin
                        full_url = urljoin(website_url, full_url)
                    careers_links.append(full_url)
            
            return careers_links[:3]  # Return top 3 candidates
            
        except Exception as e:
            print(f"Error finding careers page for {website_url}: {str(e)}")
            return []
    
    def send_digest_email(self, new_jobs):
        """Send email digest of new job postings"""
        if not new_jobs:
            print("No new jobs found - no email sent")
            return
        
        # Group jobs by company
        jobs_by_company = {}
        for job in new_jobs:
            company = job['company']
            if company not in jobs_by_company:
                jobs_by_company[company] = []
            jobs_by_company[company].append(job)
        
        # Create email content
        subject = f"🚀 {len(new_jobs)} New Job Opportunities - {datetime.now().strftime('%B %d, %Y')}"
        
        body = f"Found {len(new_jobs)} new job postings from {len(jobs_by_company)} companies:\n\n"
        
        for company, jobs in jobs_by_company.items():
            body += f"📍 {company} ({len(jobs)} new roles):\n"
            for job in jobs:
                body += f"  • {job['title']}"
                if job['url']:
                    body += f" - {job['url']}"
                body += "\n"
            body += "\n"
        
        body += f"\nTotal companies being tracked: {len(self.companies)}\n"
        body += f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        # Send email using environment variables
        self.send_email(subject, body)
    
    def send_email(self, subject, body):
        """Send email notification"""
        smtp_server = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
        smtp_port = int(os.environ.get('SMTP_PORT', '587'))
        sender_email = os.environ.get('SENDER_EMAIL')
        sender_password = os.environ.get('SENDER_PASSWORD')
        recipient_email = os.environ.get('RECIPIENT_EMAIL')
        
        if not all([sender_email, sender_password, recipient_email]):
            print("Email credentials not configured - printing to console instead:")
            print(f"Subject: {subject}")
            print(f"Body: {body}")
            return
        
        try:
            msg = MIMEMultipart()
            msg['From'] = sender_email
            msg['To'] = recipient_email
            msg['Subject'] = subject
            
            msg.attach(MIMEText(body, 'plain'))
            
            server = smtplib.SMTP(smtp_server, smtp_port)
            server.starttls()
            server.login(sender_email, sender_password)
            text = msg.as_string()
            server.sendmail(sender_email, recipient_email, text)
            server.quit()
            
            print(f"Email sent successfully to {recipient_email}")
            
        except Exception as e:
            print(f"Failed to send email: {str(e)}")
            print(f"Subject: {subject}")
            print(f"Body: {body}")
    
    def check_new_funded_hiring_emails(self, gmail_service):
        """Check for new 'Funded and Hiring' emails and parse them"""
        # Look for emails from the last 7 days
        emails = self.get_recent_emails(gmail_service, days_back=7)
        
        new_companies_added = 0
        for email in emails:
            # Parse companies from email
            companies_from_email = self.parse_funded_hiring_email(email['body'])
            
            # Add new companies to tracking
            added = self.add_companies_to_tracking(companies_from_email)
            new_companies_added += added
        
        return new_companies_added
    
    def run_daily_check(self):
        """Main function to run daily job checking"""
        print(f"Starting daily job check at {datetime.now()}")
        
        try:
            # Authenticate with Gmail
            gmail_service = self.authenticate_gmail()
            
            # Check for new companies from recent emails
            new_companies = self.check_new_funded_hiring_emails(gmail_service)
            if new_companies > 0:
                print(f"Added {new_companies} new companies from recent emails")
            
            # Check all tracked companies for new jobs
            print(f"Checking {len(self.companies)} companies for new job postings...")
            new_jobs = self.check_for_new_jobs()
            
            print(f"Found {len(new_jobs)} new job postings")
            
            # Send digest email if there are new jobs
            if new_jobs:
                self.send_digest_email(new_jobs)
            
            print("Daily check completed successfully")
            
        except Exception as e:
            print(f"Error during daily check: {str(e)}")
            # Send error notification
            error_subject = "⚠️ Job Tracker Error"
            error_body = f"Error occurred during daily job check:\n\n{str(e)}\n\nTime: {datetime.now()}"
            self.send_email(error_subject, error_body)

def main():
    """Main entry point"""
    tracker = JobTracker()
    
    # Check if this is a test run
    if os.environ.get('TEST_MODE') == 'true':
        print("Running in test mode...")
        # Just check one company for testing
        test_jobs = tracker.scrape_job_page(
            "https://jobs.lever.co/anthropic", 
            "Test Company"
        )
        print(f"Test scraping found {len(test_jobs)} jobs")
        for job in test_jobs[:3]:
            print(f"- {job['title']}")
    else:
        # Run the full daily check
        tracker.run_daily_check()

if __name__ == "__main__":
    main()
