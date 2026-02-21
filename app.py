import streamlit as st
import pandas as pd
import imaplib
import email
from email.header import decode_header
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from pypdf import PdfReader
import docx
import io
import re
import base64
import json
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qsl
from O365 import Account
from openai import OpenAI

# --- PAGE CONFIG ---
st.set_page_config(page_title="Auto Recruiter: Enterprise", layout="wide")
st.title("🏢 Auto Recruiter: Enterprise Edition")

# --- SIDEBAR ---
with st.sidebar:
    st.header("1. Connection Type")
    provider = st.radio("Select Email Provider:", ["Gmail (Personal/App Password)", "Outlook / Office 365 (Corporate)"])
    
    st.divider()
    
    if provider == "Gmail (Personal/App Password)":
        email_user = st.text_input("Email Address")
        email_pass = st.text_input("App Password", type="password")
    else:
        st.info("ℹ️ Outlook uses Secure OAuth. No App Password needed.")
        client_id = st.text_input("Client ID (Azure)")
        client_secret = st.text_input("Client Secret (Azure)", type="password")

    st.header("2. Settings")
    days_back = st.number_input("Look back days:", min_value=1, value=365)
    
    st.header("3. Job Description")
    jd = st.text_area("JD for Ranking:", height=150, placeholder="Python, AWS, 5+ years experience...")

    st.header("4. AI Brain (LLM)")
    openai_api_key = st.text_input("OpenAI API Key (Required for high accuracy):", type="password", placeholder="sk-proj-...")

# --- SHARED HELPERS ---
def extract_details(text, jd_text, api_key=None):
    # --- SMART LLM EXTRACTION ---
    if api_key:
        try:
            client = OpenAI(api_key=api_key)
            prompt = f"""
            You are an expert IT Recruiter. Extract candidate details from the following resume text.
            
            Job Description: {jd_text if jd_text else 'None provided.'}
            
            Resume Text: {text[:6000]} 
            
            Respond STRICTLY with a valid JSON object containing exactly these keys:
            "Name": (String, candidate's full name, or "N/A"),
            "Email": (String, or "N/A"),
            "Phone": (String, or "N/A"),
            "Experience": (String, calculate total years of relevant experience, e.g., "7 Years", or "N/A"),
            "Skills": (String, comma-separated list of the top 5-7 skills matching the JD. "N/A" if no JD),
            "Match": (Integer, 0 to 100 score of how well the candidate fits the Job Description. Return 0 if no JD provided).
            """
            
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                response_format={ "type": "json_object" } 
            )
            
            data = json.loads(response.choices[0].message.content)
            
            return {
                "Name": data.get("Name", "N/A"),
                "Email": data.get("Email", "N/A"),
                "Phone": data.get("Phone", "N/A"),
                "Experience": str(data.get("Experience", "N/A")),
                "Skills": str(data.get("Skills", "N/A")),
                "Match %": int(data.get("Match", 0)) 
            }
        except Exception:
            pass # If API fails, silently fall back to the old method below

    # --- DUMB REGEX FALLBACK (Runs if no API key is provided or API fails) ---
    details = {"Name": "N/A", "Phone": "N/A", "Email": "N/A", "Experience": "N/A", "Skills": "N/A", "Match %": 0}
    
    phone_pattern = r'[\+\(]?[1-9][0-9 .\-\(\)]{8,}[0-9]'
    phones = re.findall(phone_pattern, text)
    if phones:
        valid_phones = [p for p in phones if len(re.sub(r'\D', '', p)) > 9]
        if valid_phones: details["Phone"] = valid_phones[0]

    email_pattern = r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+'
    emails = re.findall(email_pattern, text)
    if emails: 
        details["Email"] = emails[0]
        details["Name"] = emails[0].split('@')[0]

    exp_pattern = r'(\d+)\+?\s*years?'
    exps = re.findall(exp_pattern, text.lower())
    if exps:
        try:
            years = [int(x) for x in exps]
            details["Experience"] = f"{max(years)} Years"
        except Exception: pass

    return details

# --- GMAIL ENGINE ---
def run_gmail_scan(user, password, days, jd_text):
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        mail.login(user, password)
    except Exception as e:
        return [], f"Login Failed: {e}"

    mail.select("INBOX")
    date_str = (datetime.now() - timedelta(days=days)).strftime("%Y/%m/%d")
    search_cmd = f'(X-GM-RAW "filename:pdf OR filename:docx after:{date_str}")'
    
    typ, data = mail.search(None, search_cmd)
    if not data[0]: return [], "No resumes found."

    email_ids = data[0].split()
    candidates = []
    
    bar = st.progress(0)
    for idx, num in enumerate(reversed(email_ids)):
        bar.progress((idx + 1) / len(email_ids))
        _, msg_data = mail.fetch(num, "(RFC822)")
        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])
                if msg.is_multipart():
                    for part in msg.walk():
                        if "attachment" in part.get("Content-Disposition", ""):
                            fname = part.get_filename()
                            if fname:
                                filename = decode_fname(fname)
                                if filename.lower().endswith(('.pdf', '.docx')):
                                    content = read_file_content(part.get_payload(decode=True), filename)
                                    if len(content) > 20:
                                       meta = extract_details(content, jd_text, openai_api_key)
                                        candidates.append({
                                            "Name": meta["Email"].split('@')[0] if meta["Email"] != "N/A" else "Candidate",
                                            "Email": meta["Email"],
                                            "Phone": meta["Phone"],
                                            "Experience": meta["Experience"],
                                            "Skills": meta["Skills Match"],
                                            "Filename": filename,
                                            "Bytes": part.get_payload(decode=True),
                                            "text": content
                                        })
    mail.logout()
    return candidates, "Success"

# --- OUTLOOK ENGINE (DEBUG MODE) ---
# --- OUTLOOK ENGINE ---
def run_outlook_scan(account_obj, days, jd_text):
    if not account_obj.is_authenticated:
        return [], "Please authenticate with Outlook first."
        
    inbox = account_obj.mailbox().inbox_folder()
    since_date = datetime.now() - timedelta(days=days)
    
    messages = inbox.get_messages(limit=2000) 
    
    candidates = []
    processed = 0
    status_text = st.empty()
    
    for msg in messages:
        processed += 1
        
        if processed % 25 == 0:
            status_text.write(f"Scanning Inbox: Checked {processed} emails...")
        
        msg_date = getattr(msg, 'received', getattr(msg, 'created', None))
        if msg_date:
            msg_date = msg_date.replace(tzinfo=None)
            if msg_date < since_date:
                continue 
                
        # ATTACHMENT CHECK
        if getattr(msg, 'has_attachments', False):
            try:
                msg.attachments.download_attachments()
            except Exception:
                pass 
                
            for att in msg.attachments:
                if att.name and att.name.lower().endswith(('.pdf', '.docx')):
                    file_bytes = getattr(att, 'content', None)
                    
                    if file_bytes:
                        if isinstance(file_bytes, str):
                            file_bytes = file_bytes.encode('utf-8', errors='ignore')
                            
                        content = read_file_content(file_bytes, att.name)
                        
                        if len(content) > 5: 
                            # CONNECTING THE BRAIN HERE! (Safe fallback if key is empty)
                            meta = extract_details(content, jd_text, openai_api_key)
                            
                            candidates.append({
                                "Name": meta.get("Name", "Candidate"),
                                "Email": meta.get("Email", "N/A"),
                                "Phone": meta.get("Phone", "N/A"),
                                "Experience": meta.get("Experience", "N/A"),
                                "Skills": meta.get("Skills", "N/A"),
                                "Match %": meta.get("Match %", 0),
                                "Filename": att.name,
                                "Bytes": file_bytes,
                                "text": content
                            })
                            
    status_text.empty()
    
    if len(candidates) == 0:
        return [], f"Done! Scanned {processed} emails, but found 0 resumes in the last {days} days."
        
    return candidates, "Success"
    
# --- MAIN LOGIC & UI FLOW ---
candidates = []
status = ""
is_ready_to_scan = True
outlook_account = None

# --- OUTLOOK ENGINE ---
def run_outlook_scan(account_obj, days, jd_text):
    if not account_obj.is_authenticated:
        return [], "Please authenticate with Outlook first."
        
    inbox = account_obj.mailbox().inbox_folder()
    since_date = datetime.now() - timedelta(days=days)
    
    messages = inbox.get_messages(limit=2000) 
    
    candidates = []
    processed = 0
    status_text = st.empty()
    
    for msg in messages:
        processed += 1
        
        if processed % 25 == 0:
            status_text.write(f"Scanning Inbox: Checked {processed} emails...")
        
        msg_date = getattr(msg, 'received', getattr(msg, 'created', None))
        if msg_date:
            msg_date = msg_date.replace(tzinfo=None)
            if msg_date < since_date:
                continue 
                
        # ATTACHMENT CHECK
        if getattr(msg, 'has_attachments', False):
            try:
                msg.attachments.download_attachments()
            except Exception:
                pass 
                
            for att in msg.attachments:
                if att.name and att.name.lower().endswith(('.pdf', '.docx')):
                    file_bytes = getattr(att, 'content', None)
                    
                    if file_bytes:
                        if isinstance(file_bytes, str):
                            file_bytes = file_bytes.encode('utf-8', errors='ignore')
                            
                        content = read_file_content(file_bytes, att.name)
                        
                        if len(content) > 5: 
                            # CONNECTING THE BRAIN HERE! (Safe fallback if key is empty)
                            meta = extract_details(content, jd_text, openai_api_key)
                            
                            candidates.append({
                                "Name": meta.get("Name", "Candidate"),
                                "Email": meta.get("Email", "N/A"),
                                "Phone": meta.get("Phone", "N/A"),
                                "Experience": meta.get("Experience", "N/A"),
                                "Skills": meta.get("Skills", "N/A"),
                                "Match %": meta.get("Match %", 0),
                                "Filename": att.name,
                                "Bytes": file_bytes,
                                "text": content
                            })
                            
    status_text.empty()
    
    if len(candidates) == 0:
        return [], f"Done! Scanned {processed} emails, but found 0 resumes in the last {days} days."
        
    return candidates, "Success"
    
# 2. RUN THE ENGINE
if is_ready_to_scan:
    if st.button("🚀 Start Recruiter Engine"):
        if provider == "Gmail (Personal/App Password)":
            if not email_user or not email_pass:
                st.error("Credentials required.")
            else:
                with st.spinner("Connecting to Gmail..."):
                    cands, stat = run_gmail_scan(email_user, email_pass, days_back, jd)
                    # SAVE TO MEMORY
                    st.session_state.scanned_candidates = cands
                    st.session_state.scan_status = stat
        
        elif provider == "Outlook / Office 365 (Corporate)":
            with st.spinner("Mining Outlook Resumes..."):
                cands, stat = run_outlook_scan(outlook_account, days_back, jd)
                # SAVE TO MEMORY
                st.session_state.scanned_candidates = cands
                st.session_state.scan_status = stat

# 3. DISPLAY RESULTS
# --- DUMB AI FALLBACK SCORING ---
    if jd:
        documents = [jd] + [c['text'] for c in display_cands]
        vectorizer = TfidfVectorizer(stop_words='english')
        try:
            tfidf_matrix = vectorizer.fit_transform(documents)
            cosine_sim = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:])
            for i, c in enumerate(display_cands):
                c["Match %"] = int(round(cosine_sim[0][i] * 100))
        except Exception: 
            pass





