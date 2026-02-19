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
from datetime import datetime, timedelta
from O365 import Account, FileSystemTokenBackend

# --- PAGE CONFIG ---
st.set_page_config(page_title="Auto Recruiter: Enterprise", layout="wide")
st.title("🏢 Auto Recruiter: Enterprise Edition")

# --- SIDEBAR ---
with st.sidebar:
    st.header("1. Connection Type")
    # THE SWITCH: Gmail vs Outlook
    provider = st.radio("Select Email Provider:", ["Gmail (Personal/App Password)", "Outlook / Office 365 (Corporate)"])
    
    st.divider()
    
    # CREDENTIALS UI
    if provider == "Gmail (Personal/App Password)":
        email_user = st.text_input("Email Address")
        email_pass = st.text_input("App Password", type="password")
    else:
        st.info("ℹ️ Outlook uses Secure OAuth. No App Password needed.")
        # Instructions for the user to get these keys (One time setup)
        client_id = st.text_input("Client ID (Azure)", help="Register an app in Azure Portal to get this.")
        client_secret = st.text_input("Client Secret (Azure)", type="password")

    st.header("2. Settings")
    days_back = st.number_input("Look back days:", min_value=1, value=365)
    
    st.header("3. Job Description")
    jd = st.text_area("JD for Ranking:", height=150, placeholder="Python, AWS, 5+ years experience...")

# --- SHARED HELPERS ---
def extract_details(text, jd_text):
    details = {"Phone": "N/A", "Email": "N/A", "Experience": "N/A", "Skills Match": []}
    
    phone_pattern = r'[\+\(]?[1-9][0-9 .\-\(\)]{8,}[0-9]'
    phones = re.findall(phone_pattern, text)
    if phones:
        valid_phones = [p for p in phones if len(re.sub(r'\D', '', p)) > 9]
        if valid_phones: details["Phone"] = valid_phones[0]

    email_pattern = r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+'
    emails = re.findall(email_pattern, text)
    if emails: details["Email"] = emails[0]

    exp_pattern = r'(\d+)\+?\s*years?'
    exps = re.findall(exp_pattern, text.lower())
    if exps:
        try:
            years = [int(x) for x in exps]
            details["Experience"] = f"{max(years)} Years"
        except: pass

    if jd_text:
        jd_words = set(re.findall(r'\b\w+\b', jd_text.lower()))
        stop_words = {'and', 'the', 'to', 'in', 'of', 'a', 'for', 'with', 'on', 'is', 'required', 'years', 'skills'}
        keywords = jd_words - stop_words
        found_skills = [word for word in keywords if word in text.lower()]
        details["Skills Match"] = ", ".join(list(set(found_skills))[:7])

    return details

def read_file_content(file_bytes, filename):
    try:
        if filename.lower().endswith(".pdf"):
            pdf = PdfReader(io.BytesIO(file_bytes))
            text = ""
            for page in pdf.pages: text += page.extract_text() + " "
            return text
        elif filename.lower().endswith(".docx"):
            doc = docx.Document(io.BytesIO(file_bytes))
            return "\n".join([para.text for para in doc.paragraphs])
    except: return ""
    return ""

def decode_fname(header_val):
    if not header_val: return ""
    decoded_list = decode_header(header_val)
    filename = ""
    for text, encoding in decoded_list:
        if isinstance(text, bytes): filename += text.decode(encoding if encoding else "utf-8", errors="ignore")
        else: filename += text
    return filename

# --- GMAIL ENGINE (IMAP) ---
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
                                        meta = extract_details(content, jd_text)
                                        candidates.append({
                                            "Name": meta["Email"].split('@')[0],
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

# --- OUTLOOK ENGINE (MICROSOFT GRAPH) ---
def run_outlook_scan(client_id, client_secret, days, jd_text):
    # Using Session Backend (Ephemeral)
    credentials = (client_id, client_secret)
    account = Account(credentials)
    
    # 1. AUTHENTICATE
    if not account.is_authenticated:
        # Generate Auth Link
        url, state = account.con.get_authorization_url(requested_scopes=['User.Read', 'Mail.Read'])
        st.warning("⚠️ Action Required: Please click the link below to authorize Outlook access.")
        st.markdown(f"[**👉 Click to Login to Outlook**]({url})", unsafe_allow_html=True)
        
        # Input for the return URL
        result_url = st.text_input("Paste the full URL you were redirected to (localhost) here:")
        if result_url:
            try:
                result = account.con.request_token(result_url, state=state)
                if result:
                    st.success("✅ Outlook Authenticated!")
                else:
                    return [], "Authentication failed."
            except Exception as e:
                return [], f"Auth Error: {e}"
        else:
            return [], "Waiting for authentication..."

    # 2. SCAN
    if account.is_authenticated:
        st.toast("Scanning Outlook Inbox...", icon="🔍")
        mailbox = account.mailbox()
        
        # Calculate Date
        since_date = datetime.now() - timedelta(days=days)
        
        # QUERY: Has attachments AND received >= date
        query = mailbox.new_query().on_attribute('has_attachments').equals(True).chain('and').on_attribute('received_date_time').greater_equal(since_date)
        
        # FETCH (Limit to 100 for speed in demo)
        messages = mailbox.get_messages(limit=100, query=query)
        
        candidates = []
        bar = st.progress(0)
        
        # Convert generator to list to track progress
        msg_list = list(messages)
        if not msg_list: return [], "No emails found."

        for idx, msg in enumerate(msg_list):
            bar.progress((idx + 1) / len(msg_list))
            
            # Check Attachments
            for att in msg.attachments:
                if att.name.lower().endswith(('.pdf', '.docx')):
                    # Download to memory
                    file_bytes = att.content
                    content = read_file_content(file_bytes, att.name)
                    
                    if len(content) > 20:
                        meta = extract_details(content, jd_text)
                        candidates.append({
                            "Name": meta["Email"].split('@')[0],
                            "Email": meta["Email"],
                            "Phone": meta["Phone"],
                            "Experience": meta["Experience"],
                            "Skills": meta["Skills Match"],
                            "Filename": att.name,
                            "Bytes": file_bytes,
                            "text": content
                        })
        return candidates, "Success"
    return [], "Waiting..."

# --- MAIN LOGIC ---
candidates = []
status = ""

if st.button("🚀 Start Recruiter Engine"):
    if provider == "Gmail (Personal/App Password)":
        if not email_user or not email_pass:
            st.error("Credentials required.")
        else:
            with st.spinner("Connecting to Gmail..."):
                candidates, status = run_gmail_scan(email_user, email_pass, days_back, jd)
    
    elif provider == "Outlook / Office 365 (Corporate)":
        if not client_id or not client_secret:
            st.error("Client ID and Secret required for Corporate Access.")
        else:
            # We don't use spinner here because it interrupts the auth flow
            candidates, status = run_outlook_scan(client_id, client_secret, days_back, jd)

# --- RESULTS ---
if candidates:
    st.success(f"✅ Found {len(candidates)} Candidates")
    st.divider()
    
    # Simple Ranking
    if jd:
        # TF-IDF logic inside
        documents = [jd] + [c['text'] for c in candidates]
        vectorizer = TfidfVectorizer(stop_words='english')
        try:
            tfidf_matrix = vectorizer.fit_transform(documents)
            cosine_sim = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:])
            for i, c in enumerate(candidates):
                c["Match %"] = int(round(cosine_sim[0][i] * 100))
        except: pass
        candidates.sort(key=lambda x: x.get("Match %", 0), reverse=True)

    for c in candidates:
        with st.container():
            c1, c2, c3, c4 = st.columns([1, 2, 2, 1])
            with c1:
                st.metric("Score", f"{c.get('Match %', 0)}%")
            with c2:
                st.subheader(c['Name'])
                st.caption(f"{c['Email']}")
            with c3:
                st.write(f"Skills: {c['Skills']}")
                st.write(f"Exp: {c['Experience']}")
            with c4:
                st.write("#")
                st.download_button("📥 Download", data=c['Bytes'], file_name=c['Filename'], mime="application/octet-stream", key=f"dl_{c['Filename']}")
            st.divider()

elif status and status != "Success" and status != "Waiting...":
    st.warning(status)


