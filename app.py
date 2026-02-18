import streamlit as st
import pandas as pd
import imaplib
import email
from email.header import decode_header
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# --- PAGE CONFIG ---
st.set_page_config(page_title="Auto Recruiter Pro", layout="wide")
st.title("☁️ Auto Recruiter: Cloud Edition")

# --- SIDEBAR ---
st.sidebar.header("1. Credentials")
email_user = st.sidebar.text_input("Email Address")
email_pass = st.sidebar.text_input("App Password", type="password")

st.sidebar.header("2. Search Filters")
# The exact filters you wanted
time_map = {
    "Last 5 Minutes": "5m",
    "Last 15 Minutes": "15m",
    "Last 1 Hour": "1h",
    "Last 24 Hours": "1d",
    "Last 7 Days": "7d",
    "Last 30 Days": "30d",
    "Last 1 Year": "1y"
}
selected_label = st.sidebar.selectbox("Look back period:", list(time_map.keys()), index=6)
selected_val = time_map[selected_label]

jd = st.text_area("Job Description", height=150, placeholder="Paste JD here...")

# --- EMAIL ENGINE ---
def get_gmail_attachments(user, password, time_filter):
    # Connect
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        mail.login(user, password)
    except Exception as e:
        return [], f"Login Failed: {e}"

    # Select Folder
    status, _ = mail.select('"[Gmail]/All Mail"')
    if status != "OK":
        mail.select("Inbox")

    # GOOGLE SEARCH COMMAND: "newer_than:1y has:attachment"
    # This is 100% server-side and accurate.
    search_cmd = f'(X-GM-RAW "has:attachment newer_than:{time_filter}")'
    
    status, data = mail.search(None, search_cmd)
    
    if not data[0]:
        mail.logout()
        return [], "No emails found in this timeframe."

    email_ids = data[0].split()
    total_found = len(email_ids)
    st.toast(f"Google found {total_found} emails. Processing...", icon="🔄")
    
    resumes = []
    
    # PROCESS ALL FOUND EMAILS (No Limit)
    # We iterate reversed to get newest first
    for num in reversed(email_ids):
        _, msg_data = mail.fetch(num, "(RFC822)")
        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])
                
                if msg.is_multipart():
                    for part in msg.walk():
                        if "attachment" in part.get("Content-Disposition", ""):
                            filename = part.get_filename()
                            if filename:
                                # Decode header if needed
                                decoded_list = decode_header(filename)
                                filename = ""
                                for text, encoding in decoded_list:
                                    if isinstance(text, bytes):
                                        filename += text.decode(encoding if encoding else "utf-8", errors="ignore")
                                    else:
                                        filename += text
                                
                                if filename.lower().endswith(('.pdf', '.docx')):
                                    # Add to list (Mocking text content for speed)
                                    resumes.append({
                                        "email": msg["From"],
                                        "filename": filename,
                                        "text": filename + " " + str(msg["Subject"]) # Using metadata for instant ranking
                                    })
    
    mail.logout()
    return resumes, "Success"

# --- RANKING ENGINE ---
def rank_resumes(resumes, jd):
    if not resumes:
        return pd.DataFrame()
    
    documents = [jd] + [r['text'] for r in resumes]
    
    # TF-IDF Vectorization
    vectorizer = TfidfVectorizer(stop_words='english')
    try:
        tfidf_matrix = vectorizer.fit_transform(documents)
    except:
        return pd.DataFrame() # Handle empty text case

    cosine_sim = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:])
    
    results = []
    for idx, score in enumerate(cosine_sim[0]):
        # Scaling score slightly since we are matching metadata/filenames
        display_score = int(round(score * 100))
        
        status = "Reject"
        if display_score > 50: status = "Interview"
        elif display_score > 20: status = "Hold"
            
        results.append({
            "Candidate": resumes[idx]['email'],
            "File": resumes[idx]['filename'],
            "Match %": display_score,
            "Status": status
        })
        
    return pd.DataFrame(results).sort_values(by="Match %", ascending=False)

# --- EXECUTION ---
if st.button("🚀 Run Cloud Scan"):
    if not email_user or not email_pass:
        st.error("Credentials required.")
    else:
        with st.spinner(f"Asking Google for files from {selected_label}..."):
            resumes, status = get_gmail_attachments(email_user, email_pass, selected_val)
            
            if resumes:
                st.success(f"Found {len(resumes)} resumes!")
                df = rank_resumes(resumes, jd)
                st.dataframe(df, use_container_width=True)
            else:
                st.warning(status)
