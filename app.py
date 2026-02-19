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
from O365 import Account

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
                                        meta = extract_details(content, jd_text)
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

# --- OUTLOOK ENGINE ---
def run_outlook_scan(client_id, client_secret, days, jd_text):
    account = Account((client_id, client_secret))
    if not account.is_authenticated:
        return [], "Please authenticate with Outlook first."
        
    mailbox = account.mailbox()
    since_date = datetime.now() - timedelta(days=days)
    
    # Fast filtering for Outlook
    query = mailbox.new_query().on_attribute('has_attachments').equals(True).chain('and').on_attribute('received_date_time').greater_equal(since_date)
    messages = mailbox.get_messages(limit=250, query=query)
    
    candidates = []
    msg_list = list(messages)
    if not msg_list: return [], "No emails found in the given timeframe."

    bar = st.progress(0)
    for idx, msg in enumerate(msg_list):
        bar.progress((idx + 1) / len(msg_list))
        for att in msg.attachments:
            if att.name.lower().endswith(('.pdf', '.docx')):
                file_bytes = att.content
                content = read_file_content(file_bytes, att.name)
                
                if len(content) > 20:
                    meta = extract_details(content, jd_text)
                    candidates.append({
                        "Name": meta["Email"].split('@')[0] if meta["Email"] != "N/A" else "Candidate",
                        "Email": meta["Email"],
                        "Phone": meta["Phone"],
                        "Experience": meta["Experience"],
                        "Skills": meta["Skills Match"],
                        "Filename": att.name,
                        "Bytes": file_bytes,
                        "text": content
                    })
    return candidates, "Success"

# --- MAIN LOGIC & UI FLOW ---
candidates = []
status = ""
is_ready_to_scan = True

# 1. OUTLOOK AUTHENTICATION GATE
if provider == "Outlook / Office 365 (Corporate)":
    if client_id and client_secret:
        account = Account((client_id, client_secret))
        if not account.is_authenticated:
            is_ready_to_scan = False
            
            # --- THE MEMORY FIX ---
            # Generate the URL ONCE and save the secret "state" into Streamlit's memory
            if "o365_auth_url" not in st.session_state:
                url, state = account.con.get_authorization_url(requested_scopes=['User.Read', 'Mail.Read'], redirect_uri='http://localhost:8501')
                st.session_state.o365_auth_url = url
                st.session_state.o365_state = state
            
            st.warning("⚠️ Outlook Authentication Required")
            st.markdown(f"**Step 1:** [👉 Click here to authorize the App]({st.session_state.o365_auth_url})", unsafe_allow_html=True)
            
            with st.form("auth_form"):
                result_url = st.text_input("**Step 2:** Paste the localhost URL from the blank page here:")
                submitted = st.form_submit_button("Verify Connection")
                
                if submitted and result_url:
                    try:
                        # Pull the secret "state" back out of memory to verify the URL
                        result = account.con.request_token(result_url, state=st.session_state.o365_state, redirect_uri='http://localhost:8501')
                        if result:
                            st.success("✅ Success! You can now scan your inbox.")
                            st.rerun() 
                        else:
                            st.error("Verification failed. Please try again.")
                    except Exception as e:
                        st.error(f"Error: {e}")

# 2. RUN THE ENGINE
if is_ready_to_scan:
    if st.button("🚀 Start Recruiter Engine"):
        if provider == "Gmail (Personal/App Password)":
            if not email_user or not email_pass:
                st.error("Credentials required.")
            else:
                with st.spinner("Connecting to Gmail..."):
                    candidates, status = run_gmail_scan(email_user, email_pass, days_back, jd)
        
        elif provider == "Outlook / Office 365 (Corporate)":
            with st.spinner("Mining Outlook Resumes..."):
                candidates, status = run_outlook_scan(client_id, client_secret, days_back, jd)

# 3. DISPLAY RESULTS
if candidates:
    st.success(f"✅ Found {len(candidates)} Candidates")
    st.divider()
    
    if jd:
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
                st.caption(f"📞 {c['Phone']}")
            with c3:
                st.write(f"**Skills:** {c['Skills']}")
                st.write(f"**Exp:** {c['Experience']}")
            with c4:
                st.write("#")
                st.download_button("📥 Download", data=c['Bytes'], file_name=c['Filename'], mime="application/octet-stream", key=f"dl_{c['Filename']}")
            st.divider()

elif status and status != "Success" and status != "Waiting...":
    st.warning(status)
