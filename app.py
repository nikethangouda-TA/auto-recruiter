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
from datetime import datetime, timedelta

# --- PAGE CONFIG ---
st.set_page_config(page_title="Auto Recruiter: Brute Force", layout="wide")
st.title("🚜 Auto Recruiter: Deep Mining Mode")

# --- SIDEBAR ---
st.sidebar.header("Credentials")
email_user = st.sidebar.text_input("Email Address")
email_pass = st.sidebar.text_input("App Password", type="password")

# We use simple days now to calculate exact date
days_back = st.sidebar.number_input("Look back days:", min_value=1, value=365)
jd = st.text_area("Job Description", height=100, placeholder="Paste JD...")

# --- HELPER FUNCTIONS ---
def decode_str(header_val):
    if not header_val: return ""
    decoded_list = decode_header(header_val)
    text = ""
    for t, encoding in decoded_list:
        if isinstance(t, bytes):
            text += t.decode(encoding if encoding else "utf-8", errors="ignore")
        else:
            text += str(t)
    return text

def get_file_content(file_bytes, filename):
    try:
        if filename.lower().endswith(".pdf"):
            pdf = PdfReader(io.BytesIO(file_bytes))
            text = ""
            for page in pdf.pages:
                text += page.extract_text() + " "
            return text
        elif filename.lower().endswith(".docx"):
            doc = docx.Document(io.BytesIO(file_bytes))
            return "\n".join([para.text for para in doc.paragraphs])
    except:
        return ""
    return ""

# --- THE BRUTE FORCE ENGINE ---
def run_deep_scan(user, password, days):
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        mail.login(user, password)
    except Exception as e:
        return [], [], f"Login Failed: {e}"

    mail.select("INBOX")

    # 1. CALCULATE EXACT DATE (Format: 01-Jan-2024)
    since_date = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
    
    # 2. STANDARD IMAP SEARCH (No Google "Magic")
    # We search for ALL emails since date, then filter manually.
    st.toast(f"Requesting all emails since {since_date}...", icon="📡")
    typ, data = mail.search(None, f'(SINCE "{since_date}")')
    
    if not data[0]:
        mail.logout()
        return [], [], "No emails found in this date range."

    email_ids = data[0].split()
    total_emails = len(email_ids)
    
    resumes = []
    debug_log = []
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    # 3. ITERATE EVERYTHING (No [-50:] limit)
    # This might take a moment if you have thousands of emails, but it GUARANTEES accuracy.
    for idx, num in enumerate(reversed(email_ids)):
        progress_bar.progress((idx + 1) / total_emails)
        status_text.write(f"Scanning email {idx+1}/{total_emails}...")
        
        _, msg_data = mail.fetch(num, "(RFC822)")
        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])
                subject = decode_str(msg["Subject"])
                sender = msg["From"]
                
                # Check for attachments
                if msg.is_multipart():
                    has_resume = False
                    for part in msg.walk():
                        # robust check for attachment
                        content_disposition = str(part.get("Content-Disposition"))
                        
                        if "attachment" in content_disposition:
                            fname = part.get_filename()
                            if fname:
                                filename = decode_str(fname)
                                
                                # STRICT MATCH
                                if filename.lower().endswith(('.pdf', '.docx')):
                                    file_bytes = part.get_payload(decode=True)
                                    content = get_file_content(file_bytes, filename)
                                    
                                    if len(content) > 10:
                                        resumes.append({
                                            "Candidate": sender,
                                            "Subject": subject,
                                            "File": filename,
                                            "text": content
                                        })
                                        has_resume = True
                                        debug_log.append(f"✅ FOUND: {filename} in '{subject}'")
                                else:
                                    debug_log.append(f"⚠️ SKIPPED: {filename} (Wrong Type)")
                        
                    if not has_resume:
                         debug_log.append(f"❌ NO ATTACHMENT: Email '{subject}' had no PDF/DOCX")
                else:
                    debug_log.append(f"❌ TEXT ONLY: Email '{subject}' is not multipart")

    mail.logout()
    status_text.empty()
    return resumes, debug_log, "Success"

# --- UI EXECUTION ---
if st.button("🚀 START DEEP SCAN"):
    if not email_user or not email_pass:
        st.error("Credentials required.")
    else:
        # Clear previous results
        resumes = []
        debug_log = []
        
        with st.spinner("Mining Inbox... (This extracts everything)"):
            resumes, debug_log, status = run_deep_scan(email_user, email_pass, days_back)
            
            if resumes:
                st.success(f"Found {len(resumes)} Valid Resumes")
                
                # RANKING
                if jd:
                    documents = [jd] + [r['text'] for r in resumes]
                    vectorizer = TfidfVectorizer(stop_words='english')
                    tfidf_matrix = vectorizer.fit_transform(documents)
                    cosine_sim = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:])
                    
                    for i, r in enumerate(resumes):
                        r["Match %"] = int(round(cosine_sim[0][i] * 100))
                        r["Status"] = "Interview" if r["Match %"] > 40 else "Reject"
                        del r["text"] # Clean table
                    
                    df = pd.DataFrame(resumes).sort_values(by="Match %", ascending=False)
                    st.dataframe(df, use_container_width=True)
                else:
                    st.dataframe(pd.DataFrame(resumes))
            else:
                st.error("Still 0? Check the Debug Log below.")

            # FULL TRANSPARENCY LOG
            with st.expander("🕵️ Detailed Scan Log (See exactly what was skipped)", expanded=False):
                st.write(debug_log)
