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

# --- PAGE CONFIG ---
st.set_page_config(page_title="Auto Recruiter Speed", layout="wide")
st.title("⚡ Auto Recruiter: Live Speed Mode")

# --- SIDEBAR ---
st.sidebar.header("1. Credentials")
email_user = st.sidebar.text_input("Email Address")
email_pass = st.sidebar.text_input("App Password", type="password")

st.sidebar.header("2. Speed Filter")
time_options = {
    "Last 10 Minutes": "10m",
    "Last 1 Hour": "1h",
    "Last 24 Hours": "1d",
    "Last 7 Days": "7d",
    "Last 1 Year": "1y"
}
selected_label = st.sidebar.selectbox("Timeframe:", list(time_options.keys()), index=0)
time_code = time_options[selected_label]

jd = st.text_area("Job Description", height=100, placeholder="Paste JD here...")

# --- TEXT EXTRACTOR ---
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
        return "" # Return empty if read fails
    return ""

# --- EMAIL ENGINE (OPTIMIZED) ---
def fast_scan(user, password, time_limit):
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        mail.login(user, password)
    except Exception as e:
        return [], f"Login Error: {e}"

    # CRITICAL FIX: Use INBOX (Instant) instead of All Mail (Slow)
    mail.select("INBOX") 

    # FASTEST GOOGLE SEARCH
    search_cmd = f'(X-GM-RAW "has:attachment newer_than:{time_limit}")'
    status, data = mail.search(None, search_cmd)
    
    if not data[0]:
        mail.logout()
        return [], "No emails found in this timeframe."

    email_ids = data[0].split()
    # Batch limit to prevent crashes
    email_ids = email_ids[-50:] 
    
    resumes = []
    total_found = len(email_ids)
    
    status_bar = st.progress(0)
    
    # Process newest first
    for idx, num in enumerate(reversed(email_ids)):
        # Update progress
        status_bar.progress((idx + 1) / total_found)
        
        _, msg_data = mail.fetch(num, "(RFC822)")
        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])
                
                if msg.is_multipart():
                    for part in msg.walk():
                        if "attachment" in part.get("Content-Disposition", ""):
                            filename = part.get_filename()
                            if filename:
                                # Decode Filename
                                decoded_list = decode_header(filename)
                                filename = ""
                                for text, encoding in decoded_list:
                                    if isinstance(text, bytes):
                                        filename += text.decode(encoding if encoding else "utf-8", errors="ignore")
                                    else:
                                        filename += text
                                
                                if filename.lower().endswith(('.pdf', '.docx')):
                                    # DOWNLOAD & READ CONTENT
                                    file_bytes = part.get_payload(decode=True)
                                    content = get_file_content(file_bytes, filename)
                                    
                                    if len(content) > 50:
                                        resumes.append({
                                            "Candidate": msg["From"],
                                            "File": filename,
                                            "text": content
                                        })
    
    mail.logout()
    return resumes, "Success"

# --- EXECUTION ---
if st.button("🚀 INSTANT SCAN"):
    if not email_user or not email_pass:
        st.error("Enter Credentials.")
    else:
        with st.spinner("Scanning Inbox..."):
            resumes, status = fast_scan(email_user, email_pass, time_code)
            
            if resumes:
                # RANKING
                if jd:
                    documents = [jd] + [r['text'] for r in resumes]
                    vectorizer = TfidfVectorizer(stop_words='english')
                    tfidf_matrix = vectorizer.fit_transform(documents)
                    cosine_sim = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:])
                    
                    # Add scores to results
                    for i, r in enumerate(resumes):
                        r["Match %"] = int(round(cosine_sim[0][i] * 100))
                        r["Status"] = "Interview" if r["Match %"] > 50 else "Reject"
                        del r["text"] # Hide raw text from table
                    
                    df = pd.DataFrame(resumes).sort_values(by="Match %", ascending=False)
                    st.success(f"Processed {len(resumes)} resumes!")
                    st.dataframe(df, use_container_width=True)
                else:
                    st.warning("Found resumes, but need JD to rank them.")
                    st.dataframe(pd.DataFrame(resumes))
            else:
                st.warning(status)
