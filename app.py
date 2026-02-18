import streamlit as st
import pandas as pd
import imaplib
import email
import re
from email.header import decode_header
from datetime import datetime, timedelta
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

# --- PAGE CONFIG ---
st.set_page_config(page_title="Cloud Recruiter", layout="wide")
st.title("☁️ Auto Recruiter: Cloud Edition")

# --- SIDEBAR CONFIG ---
st.sidebar.header("🔐 Credentials")
email_user = st.sidebar.text_input("Email Address")
email_pass = st.sidebar.text_input("App Password", type="password")

# --- 1. NUCLEAR EMAIL SEARCH (Raw IMAP) ---
def get_gmail_attachments(user, password, days_back=365):
    """
    Uses raw IMAP commands to ask Google for attachments instantly.
    Returns a list of dictionaries: [{'filename': '...', 'text': '...'}]
    """
    # Connect to Gmail
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        mail.login(user, password)
    except Exception as e:
        return [], f"Login Failed: {e}"

    # Select Folder (Try All Mail, fall back to Inbox)
    status, messages = mail.select('"[Gmail]/All Mail"')
    if status != "OK":
        mail.select("Inbox")

    # CALCULATE DATE for Google Command
    date_since = (datetime.now() - timedelta(days=days_back)).strftime("%Y/%m/%d")
    
    # THE NUCLEAR COMMAND: X-GM-RAW "has:attachment after:YYYY/MM/DD"
    # This runs on Google's server, not yours. It is instant.
    search_cmd = f'(X-GM-RAW "has:attachment after:{date_since}")'
    
    status, data = mail.search(None, search_cmd)
    
    if not data[0]:
        return [], "No emails found with attachments."

    email_ids = data[0].split()
    st.toast(f"Google found {len(email_ids)} emails. Downloading...", icon="🚀")
    
    resumes = []
    
    # Fetch latest 20 only to prevent timeout on free cloud
    for num in reversed(email_ids[-20:]):
        _, msg_data = mail.fetch(num, "(RFC822)")
        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])
                
                # Extract Attachments
                if msg.is_multipart():
                    for part in msg.walk():
                        content_disposition = part.get("Content-Disposition", "")
                        if "attachment" in content_disposition:
                            filename = part.get_filename()
                            if filename and (filename.lower().endswith(".pdf") or filename.lower().endswith(".docx")):
                                # Decode filename
                                filename = decode_header(filename)[0][0]
                                if isinstance(filename, bytes):
                                    filename = filename.decode()
                                
                                # Extract Text (Simplified for Cloud - Text only)
                                # Note: Full PDF parsing requires extra libraries, 
                                # for now we just log the find to prove speed.
                                resumes.append({
                                    "email": msg["From"],
                                    "filename": filename,
                                    "date": msg["Date"],
                                    "text": filename # Placeholder for actual PDF text
                                })
    
    mail.logout()
    return resumes, "Success"

# --- 2. TURBO RANKING ENGINE ---
def rank_resumes(resumes, jd):
    if not resumes:
        return pd.DataFrame()
    
    # Mock text for now since we aren't downloading heavy PDFs to cloud memory yet
    # In a real cloud app, we would use 'pypdf' here.
    documents = [jd] + [r['filename'] + " " + r['email'] for r in resumes]
    
    vectorizer = TfidfVectorizer(stop_words='english')
    tfidf_matrix = vectorizer.fit_transform(documents)
    cosine_sim = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:])
    
    results = []
    for idx, score in enumerate(cosine_sim[0]):
        results.append({
            "Candidate": resumes[idx]['email'],
            "File": resumes[idx]['filename'],
            "Match %": int(round(score * 100)),
            "Status": "Interview" if score > 0.3 else "Reject" # Lower threshold for metadata match
        })
        
    return pd.DataFrame(results).sort_values(by="Match %", ascending=False)

# --- DASHBOARD UI ---
st.info("💡 Cloud Mode: This runs on Streamlit's servers. Enter creds in sidebar.")

jd = st.text_area("Job Description", "Looking for a Python Developer...")
days = st.slider("Look back (Days)", 1, 365, 30)

if st.button("🚀 Run Cloud Scan"):
    if not email_user or not email_pass:
        st.error("Please enter Email and App Password in the sidebar.")
    else:
        with st.spinner("Connecting to Google Servers..."):
            # 1. GET DATA
            resumes, status = get_gmail_attachments(email_user, email_pass, days)
            
            if resumes:
                st.success(f"Found {len(resumes)} resumes!")
                
                # 2. RANK DATA
                df = rank_resumes(resumes, jd)
                st.dataframe(df, use_container_width=True)
            else:
                st.warning(status)