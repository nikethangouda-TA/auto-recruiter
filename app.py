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
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qsl
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
    except Exception as e: 
        return f"DEBUG_ERROR: {str(e)}"
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

# --- OUTLOOK ENGINE (DEBUG MODE) ---
def run_outlook_scan(account_obj, days, jd_text):
    if not account_obj.is_authenticated:
        return [], "Please authenticate with Outlook first."
        
    inbox = account_obj.mailbox().inbox_folder()
    
    # We will only pull the last 50 emails so the debug logs don't crash your browser
    messages = inbox.get_messages(limit=50) 
    
    candidates = []
    debug_logs = []
    
    for msg in messages:
        subject = getattr(msg, 'subject', 'No Subject')
        log = f"📧 {subject[:30]}..."
        
        if getattr(msg, 'has_attachments', False):
            log += " | 📎 Has Attachments"
            try:
                # Force attachment download
                if hasattr(msg.attachments, 'download_attachments'):
                    msg.attachments.download_attachments()
            except Exception as e:
                log += f" [Error downloading info: {e}]"
                
            for att in msg.attachments:
                if att.name and att.name.lower().endswith(('.pdf', '.docx')):
                    log += f" | 📄 {att.name}"
                    file_bytes = getattr(att, 'content', None)
                    
                    if file_bytes:
                        # Base64 Decryption check
                        if isinstance(file_bytes, str):
                            try:
                                file_bytes = base64.b64decode(file_bytes)
                                log += " (Base64 Decoded)"
                            except:
                                file_bytes = file_bytes.encode('utf-8', errors='ignore')
                                log += " (UTF-8 Encoded)"
                                
                        content = read_file_content(file_bytes, att.name)
                        
                        if "DEBUG_ERROR" in content:
                            log += f" ❌ CRASHED: {content}"
                        elif len(content) > 5:
                            log += f" ✅ Parsed! ({len(content)} chars)"
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
                        else:
                            log += " ⚠️ File read but text was completely empty."
                    else:
                        log += " ⚠️ File found, but Microsoft returned 0 bytes of content."
        else:
            log += " | No attachments."
            
        debug_logs.append(log)
                            
    if len(candidates) == 0:
        return [], "DEBUG REPORT:\n\n" + "\n".join(debug_logs)
        
    return candidates, "Success"

# --- MAIN LOGIC & UI FLOW ---
candidates = []
status = ""
is_ready_to_scan = True
outlook_account = None

# 1. OUTLOOK AUTHENTICATION GATE
if provider == "Outlook / Office 365 (Corporate)":
    if client_id and client_secret:
        
        if "o365_account" not in st.session_state:
            st.session_state.o365_account = Account((client_id, client_secret))
            
        outlook_account = st.session_state.o365_account
        
        if not outlook_account.is_authenticated:
            is_ready_to_scan = False
            
            if "o365_auth_flow" not in st.session_state:
                scopes = ['https://graph.microsoft.com/User.Read', 'https://graph.microsoft.com/Mail.Read']
                flow = outlook_account.con.msal_client.initiate_auth_code_flow(
                    scopes=scopes, 
                    redirect_uri='http://localhost:8501'
                )
                st.session_state.o365_auth_flow = flow
            
            st.warning("⚠️ Outlook Authentication Required")
            st.markdown(f"**Step 1:** [👉 Click here to authorize the App]({st.session_state.o365_auth_flow['auth_uri']})", unsafe_allow_html=True)
            
            with st.form("auth_form"):
                result_url = st.text_input("**Step 2:** Paste the localhost URL from the blank page here:")
                submitted = st.form_submit_button("Verify Connection")
                
                if submitted and result_url:
                    try:
                        query_params = dict(parse_qsl(urlparse(result_url).query))
                        result = outlook_account.con.msal_client.acquire_token_by_auth_code_flow(
                            auth_code_flow=st.session_state.o365_auth_flow,
                            auth_response=query_params
                        )
                        
                        if "access_token" in result:
                            outlook_account.con.token_backend.token = result
                            outlook_account.con.token_backend.save_token()
                            st.success("✅ Success! You can now scan your inbox.")
                            st.rerun() 
                        else:
                            st.error(f"Verification failed: {result.get('error_description', result)}")
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
if "scanned_candidates" in st.session_state and st.session_state.scanned_candidates:
    display_cands = st.session_state.scanned_candidates
    
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
            
    display_cands.sort(key=lambda x: x.get("Match %", 0), reverse=True)

    # --- TOP BAR: Stats & Excel Export ---
    top_col1, top_col2 = st.columns([3, 1])
    with top_col1:
        st.success(f"✅ Found {len(display_cands)} Candidates")
    with top_col2:
        # Build the DataFrame for Excel/CSV export
        export_df = pd.DataFrame([{
            "Score (%)": c.get('Match %', 0),
            "Name": c.get('Name', 'N/A'),
            "Phone": c.get('Phone', 'N/A'),
            "Email": c.get('Email', 'N/A'),
            "Experience": c.get('Experience', 'N/A'),
            "Skills": c.get('Skills', 'N/A')
        } for c in display_cands])
        
        csv_data = export_df.to_csv(index=False).encode('utf-8')
        
        st.download_button(
            label="📊 Download to Excel",
            data=csv_data,
            file_name=f"candidates_export_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
            use_container_width=True
        )
        
    st.divider()

    # --- THE TABLE UI ---
    h1, h2, h3, h4, h5, h6, h7 = st.columns([1, 1.5, 1.5, 2, 2, 1, 1])
    h1.markdown("**Score**")
    h2.markdown("**Name**")
    h3.markdown("**Phone**")
    h4.markdown("**Email**")
    h5.markdown("**Skills**")
    h6.markdown("**Exp**")
    h7.markdown("**Resume**")
    st.markdown("---")

    for c in display_cands:
        col1, col2, col3, col4, col5, col6, col7 = st.columns([1, 1.5, 1.5, 2, 2, 1, 1])
        with col1: st.write(f"**{c.get('Match %', 0)}%**")
        with col2: st.write(c.get('Name', 'N/A'))
        with col3: st.write(c.get('Phone', 'N/A'))
        with col4: st.caption(c.get('Email', 'N/A'))
        with col5: st.caption(c.get('Skills', 'N/A'))
        with col6: st.write(c.get('Experience', 'N/A'))
        with col7:
            st.download_button(
                label="📥 PDF", 
                data=c['Bytes'], 
                file_name=c['Filename'], 
                mime="application/octet-stream", 
                key=f"dl_{c['Filename']}"
            )
        st.markdown("<hr style='margin: 0px; opacity: 0.2;'>", unsafe_allow_html=True)

elif "scan_status" in st.session_state and st.session_state.scan_status != "Success":
    st.warning(st.session_state.scan_status)


