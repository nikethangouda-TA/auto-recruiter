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
st.set_page_config(page_title="Auto Recruiter: God Mode", layout="wide")
st.title("⚡ Auto Recruiter: God Mode")

# --- SIDEBAR ---
st.sidebar.header("Credentials")
email_user = st.sidebar.text_input("Email Address")
email_pass = st.sidebar.text_input("App Password", type="password")

# Exact Time Control
days_back = st.sidebar.number_input("Look back days:", min_value=1, value=365)
jd = st.text_area("Job Description", height=100, placeholder="Paste JD...")

# --- HELPER FUNCTIONS ---
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

def decode_fname(header_val):
    if not header_val: return ""
    decoded_list = decode_header(header_val)
    filename = ""
    for text, encoding in decoded_list:
        if isinstance(text, bytes):
            filename += text.decode(encoding if encoding else "utf-8", errors="ignore")
        else:
            filename += text
    return filename

# --- THE GOD MODE ENGINE ---
def run_god_mode_scan(user, password, days):
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        mail.login(user, password)
    except Exception as e:
        return [], f"Login Failed: {e}"

    mail.select("INBOX")

    # 1. CALCULATE DATE (YYYY/MM/DD)
    date_str = (datetime.now() - timedelta(days=days)).strftime("%Y/%m/%d")
    
    # 2. THE GOD QUERY
    # We tell Google: "Only give me emails with PDF or DOCX files sent after this date"
    # This filters 35,000 emails down to 46 INSTANTLY.
    search_cmd = f'(X-GM-RAW "filename:pdf OR filename:docx after:{date_str}")'
    
    st.toast(f"Asking Google for PDFs/DOCXs since {date_str}...", icon="📡")
    typ, data = mail.search(None, search_cmd)
    
    if not data[0]:
        mail.logout()
        return [], "No resumes found with this specific filter."

    email_ids = data[0].split()
    total_found = len(email_ids)
    
    st.info(f"⚡ Google identified {total_found} emails with resumes. Downloading now...")
    
    resumes = []
    progress_bar = st.progress(0)
    
    # 3. DOWNLOAD ONLY THE HITS
    for idx, num in enumerate(reversed(email_ids)):
        progress_bar.progress((idx + 1) / total_found)
        
        _, msg_data = mail.fetch(num, "(RFC822)")
        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])
                sender = msg["From"]
                
                if msg.is_multipart():
                    for part in msg.walk():
                        if "attachment" in part.get("Content-Disposition", ""):
                            fname = part.get_filename()
                            if fname:
                                filename = decode_fname(fname)
                                
                                # Double Check (Speed Optimization)
                                if filename.lower().endswith(('.pdf', '.docx')):
                                    file_bytes = part.get_payload(decode=True)
                                    content = get_file_content(file_bytes, filename)
                                    
                                    if len(content) > 10:
                                        resumes.append({
                                            "Candidate": sender,
                                            "File": filename,
                                            "text": content
                                        })
    
    mail.logout()
    return resumes, "Success"

# --- UI EXECUTION ---
if st.button("🚀 RUN GOD MODE SCAN"):
    if not email_user or not email_pass:
        st.error("Credentials required.")
    else:
        with st.spinner("Connecting..."):
            resumes, status = run_god_mode_scan(email_user, email_pass, days_back)
            
            if resumes:
                st.success(f"✅ Successfully extracted {len(resumes)} Resumes")
                
                # RANKING
                if jd:
                    documents = [jd] + [r['text'] for r in resumes]
                    vectorizer = TfidfVectorizer(stop_words='english')
                    tfidf_matrix = vectorizer.fit_transform(documents)
                    cosine_sim = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:])
                    
                    for i, r in enumerate(resumes):
                        r["Match %"] = int(round(cosine_sim[0][i] * 100))
                        r["Status"] = "Interview" if r["Match %"] > 45 else "Reject"
                        del r["text"]
                    
                    df = pd.DataFrame(resumes).sort_values(by="Match %", ascending=False)
                    st.dataframe(df, use_container_width=True)
                else:
                    st.dataframe(pd.DataFrame(resumes))
            else:
                st.warning(status)
