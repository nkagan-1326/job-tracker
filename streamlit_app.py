import streamlit as st
import json
import pandas as pd
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import re

st.set_page_config(
    page_title="Job Tracker Dashboard",
    page_icon="🚀",
    layout="wide"
)

# Load data
@st.cache_data
def load_companies():
    try:
        with open('companies.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

@st.cache_data
def load_previous_jobs():
    try:
        with open('previous_jobs.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_companies(companies):
    with open('companies.json', 'w') as f:
        json.dump(companies, f, indent=2)

def parse_funded_hiring_email(email_body):
    """Parse company info from 'Funded and Hiring' email"""
    companies = {}
    lines = email_body.split('\n')
    
    current_company = None
    for line in lines:
        line = line.strip()
        
        # Look for company names
        if line and not line.startswith(('http', 'www', 'Website:', 'Jobs:', 'Careers:')):
            if len(line) < 100 and not any(word in line.lower() for word in ['the', 'and', 'funded', 'hiring']):
                company_name = re.split(r'\s*[-–—]\s*', line)[0].strip()
                if company_name:
                    current_company = company_name
                    companies[current_company] = {
                        'website': '',
                        'jobs_page': '',
                        'date_added': datetime.now().isoformat()
                    }
        
        # Look for URLs
        elif current_company and ('http' in line or 'www' in line):
            urls = re.findall(r'https?://[^\s]+', line)
            for url in urls:
                url = url.rstrip('.,;)')
                
                if any(keyword in line.lower() for keyword in ['job', 'career', 'hiring']):
                    companies[current_company]['jobs_page'] = url
                elif not companies[current_company]['website']:
                    companies[current_company]['website'] = url
    
    return companies

def main():
    st.title("🚀 Job Tracker Dashboard")
    st.markdown("Monitor job postings from recently funded companies")
    
    # Load current data
    companies = load_companies()
    previous_jobs = load_previous_jobs()
    
    # Sidebar for actions
    st.sidebar.header("Actions")
    
    # Add new email
    st.sidebar.subheader("📧 Add Companies from Email")
    email_input = st.sidebar.text_area(
        "Paste 'Funded and Hiring' email content:",
        height=200,
        placeholder="Paste the full email content here..."
    )
    
    if st.sidebar.button("Parse Email & Add Companies"):
        if email_input:
            new_companies = parse_funded_hiring_email(email_input)
            
            if new_companies:
                added_count = 0
                for company, info in new_companies.items():
                    if company not in companies:
                        companies[company] = info
                        added_count += 1
                
                if added_count > 0:
                    save_companies(companies)
                    st.sidebar.success(f"Added {added_count} new companies!")
                    st.rerun()
                else:
                    st.sidebar.info("All companies already being tracked")
            else:
                st.sidebar.error("No companies found in email. Check the format.")
    
    # Manual company addition
    st.sidebar.subheader("➕ Add Company Manually")
    with st.sidebar.form("add_company"):
        company_name = st.text_input("Company Name")
        website_url = st.text_input("Website URL")
        jobs_url = st.text_input("Jobs Page URL (optional)")
        
        if st.form_submit_button("Add Company"):
            if company_name and website_url:
                companies[company_name] = {
                    'website': website_url,
                    'jobs_page': jobs_url,
                    'date_added': datetime.now().isoformat()
                }
                save_companies(companies)
                st.sidebar.success(f"Added {company_name}!")
                st.rerun()
    
    # Main dashboard
    if not companies:
        st.info("👋 Welcome! Add your first companies using the sidebar.")
        st.markdown("""
        **Getting Started:**
        1. Paste a 'Funded and Hiring' email in the sidebar to automatically extract companies
        2. Or manually add companies one by one
        3. The automated system will check for new job postings daily
        """)
        return
    
    # Stats
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Companies Tracked", len(companies))
    with col2:
        total_jobs = sum(len(jobs) for jobs in previous_jobs.values())
        st.metric("Total Jobs Found", total_jobs)
    with col3:
        recent_companies = sum(1 for c in companies.values() 
                             if datetime.fromisoformat(c['date_added']).date() == datetime.now().date())
        st.metric("Added Today", recent_companies)
    with col4:
        companies_with_jobs = sum(1 for company in companies.keys() 
                                if company.lower().replace(' ', '_') in previous_jobs)
        st.metric("Companies w/ Jobs", companies_with_jobs)
    
    # Companies table
    st.subheader("📊 Tracked Companies")
    
    # Convert to DataFrame for display
    df_data = []
    for company, info in companies.items():
        company_key = company.lower().replace(' ', '_')
        job_count = len(previous_jobs.get(company_key, []))
        
        df_data.append({
            'Company': company,
            'Website': info.get('website', ''),
            'Jobs Page': info.get('jobs_page', ''),
            'Jobs Found': job_count,
            'Date Added': datetime.fromisoformat(info['date_added']).strftime('%Y-%m-%d'),
        })
    
    if df_data:
        df = pd.DataFrame(df_data)
        
        # Add filters
        col1, col2 = st.columns(2)
        with col1:
            search_term = st.text_input("🔍 Search companies:", "")
        with col2:
            show_only_with_jobs = st.checkbox("Show only companies with jobs")
        
        # Apply filters
        if search_term:
            df = df[df['Company'].str.contains(search_term, case=False)]
        
        if show_only_with_jobs:
            df = df[df['Jobs Found'] > 0]
        
        # Display table
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Website": st.column_config.LinkColumn(),
                "Jobs Page": st.column_config.LinkColumn(),
            }
        )
        
        # Recent jobs section
        if previous_jobs:
            st.subheader("🆕 Recent Job Postings")
            
            # Flatten all jobs with company names
            all_jobs = []
            for company_key, jobs in previous_jobs.items():
                company_name = next((name for name in companies.keys() 
                                   if name.lower().replace(' ', '_') == company_key), 
                                   company_key.replace('_', ' ').title())
                
                for job in jobs:
                    job_with_company = job.copy()
                    job_with_company['company'] = company_name
                    all_jobs.append(job_with_company)
            
            # Sort by scraped date
            all_jobs.sort(key=lambda x: x.get('scraped_date', ''), reverse=True)
            
            # Show recent jobs
            for job in all_jobs[:20]:  # Show last 20 jobs
                with st.container():
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        if job.get('url'):
                            st.markdown(f"**[{job['title']}]({job['url']})** at {job['company']}")
                        else:
                            st.markdown(f"**{job['title']}** at {job['company']}")
                    with col2:
                        scraped_date = job.get('scraped_date', '')
                        if scraped_date:
                            try:
                                date_obj = datetime.fromisoformat(scraped_date.replace('Z', '+00:00'))
                                st.caption(date_obj.strftime('%m/%d %H:%M'))
                            except:
                                st.caption(scraped_date[:10])
    
    # Company management
    st.subheader("🛠️ Manage Companies")
    
    # Remove companies
    if companies:
        companies_to_remove = st.multiselect(
            "Select companies to remove:",
            list(companies.keys())
        )
        
        if st.button("Remove Selected Companies") and companies_to_remove:
            for company in companies_to_remove:
                del companies[company]
                # Also remove from previous jobs
                company_key = company.lower().replace(' ', '_')
                if company_key in previous_jobs:
                    del previous_jobs[company_key]
            
            save_companies(companies)
            with open('previous_jobs.json', 'w') as f:
                json.dump(previous_jobs, f, indent=2)
            
            st.success(f"Removed {len(companies_to_remove)} companies")
            st.rerun()
    
    # Export data
    st.subheader("📤 Export Data")
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("Download Companies JSON"):
            st.download_button(
                label="📁 Download companies.json",
                data=json.dumps(companies, indent=2),
                file_name=f"companies_{datetime.now().strftime('%Y%m%d')}.json",
                mime="application/json"
            )
    
    with col2:
        if st.button("Download Jobs JSON"):
            st.download_button(
                label="📁 Download previous_jobs.json", 
                data=json.dumps(previous_jobs, indent=2),
                file_name=f"jobs_{datetime.now().strftime('%Y%m%d')}.json",
                mime="application/json"
            )

if __name__ == "__main__":
    main()
