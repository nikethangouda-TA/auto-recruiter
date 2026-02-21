import streamlit as st
import pandas as pd
import imaplib
import email
from email.header import decode_header
from email.utils import parsedate_to_datetime
import io
import re
import json
import time 
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qsl

# To prevent crashes if these aren't installed yet
try:
    from pypdf import PdfReader
    import docx
    from O365 import Account
    from openai import OpenAI
    import google.generativeai as genai
    import anthropic
    from supabase import create_client, Client
except ImportError:
    pass

# --- PAGE CONFIG ---
st.set_page_config(page_title="Auto Recruiter: Enterprise", layout="wide")

# --- DATABASE & AUTH SETUP ---
@st.cache_resource
def init_supabase():
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        return create_client(url, key)
    except Exception as e:
        return None

supabase = init_supabase()

# Initialize session states
if 'authenticated' not in st.session_state: st.session_state.authenticated = False
if 'user_email' not in st.session_state: st.session_state.user_email = ""
if 'awaiting_otp' not in st.session_state: st.session_state.awaiting_otp = False
if 'temp_signup_email' not in st.session_state: st.session_state.temp_signup_email = ""
if 'reset_flow' not in st.session_state: st.session_state.reset_flow = False
if 'reset_sent' not in st.session_state: st.session_state.reset_sent = False
if 'reset_email' not in st.session_state: st.session_state.reset_email = ""

# ==========================================
# --- THE LANDING PAGE (If not logged in) ---
# ==========================================
if not st.session_state.authenticated:
    
    st.title("🏢 Auto Recruiter")
    st.subheader("The AI-Powered Bulk Resume Engine for Enterprise Staffing")
    st.divider()
    
    if supabase is None:
        st.error("⚠️ Database connection missing. Please configure Supabase in Streamlit Secrets.")
        st.stop()
        
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        tab1, tab2 = st.tabs(["🔒 Secure Log In", "🚀 Request Access"])
        
        # --- LOGIN & PASSWORD RESET TAB ---
        with tab1:
            if not st.session_state.reset_flow:
                st.write("### Welcome Back")
                login_email = st.text_input("Work Email", key="log_email")
                login_password = st.text_input("Password", type="password", key="log_pwd")
                
                col_btn1, col_btn2 = st.columns(2)
                with col_btn1:
                    if st.button("Access Dashboard", use_container_width=True, type="primary"):
                        if not login_email or not login_password:
                            st.error("Please enter both email and password.")
                        else:
                            try:
                                res = supabase.auth.sign_in_with_password({"email": login_email, "password": login_password})
                                st.session_state.authenticated = True
                                st.session_state.user_email = login_email
                                st.rerun()
                            except Exception as e:
                                st.error("Invalid credentials or email not verified.")
                with col_btn2:
                    if st.button("Forgot Password?", use_container_width=True):
                        st.session_state.reset_flow = True
                        st.rerun()
            else:
                # --- FORGOT PASSWORD SCREEN ---
                st.write("### Reset Password")
                if not st.session_state.reset_sent:
                    reset_email_input = st.text_input("Enter your Work Email", key="reset_em")
                    
                    if st.button("Send 6-Digit Reset Code", use_container_width=True, type="primary"):
                        if reset_email_input:
                            try:
                                supabase.auth.reset_password_for_email(reset_email_input)
                                st.session_state.reset_email = reset_email_input
                                st.session_state.reset_sent = True
                                st.rerun()
                            except Exception as e:
                                st.error(f"Error: {e}")
                        else:
                            st.error("Please enter an email address.")
                            
                    if st.button("Back to Login", use_container_width=True):
                        st.session_state.reset_flow = False
                        st.rerun()
                else:
                    # --- ENTER RESET OTP & NEW PASSWORD ---
                    st.info(f"📩 Sent reset code to **{st.session_state.reset_email}**")
                    reset_code = st.text_input("Enter 6-Digit Code")
                    new_pwd = st.text_input("Create New Password", type="password")
                    
                    if st.button("Update Password & Login", use_container_width=True, type="primary"):
                        if not reset_code or not new_pwd:
                            st.error("Please enter the code and a new password.")
                        else:
                            try:
                                # 1. Verify the OTP code for recovery
                                supabase.auth.verify_otp({"email": st.session_state.reset_email, "token": reset_code, "type": "recovery"})
                                # 2. Immediately update to the new password
                                supabase.auth.update_user({"password": new_pwd})
                                
                                st.session_state.authenticated = True
                                st.session_state.user_email = st.session_state.reset_email
                                st.session_state.reset_flow = False
                                st.session_state.reset_sent = False
                                st.success("Password updated successfully!")
                                st.rerun()
                            except Exception as e:
                                st.error("Invalid code. Please try again.")
                                
                    if st.button("Cancel", use_container_width=True):
                        st.session_state.reset_flow = False
                        st.session_state.reset_sent = False
                        st.rerun()

        # --- SIGNUP & OTP TAB ---
        with tab2:
            if not st.session_state.awaiting_otp:
                st.write("### Create Account")
                signup_email = st.text_input("Work Email *", key="reg_email")
                signup_phone = st.text_input("Mobile Number *", key="reg_phone")
                signup_password = st.text_input("Create Password *", type="password", key="reg_pwd")
                
                if st.button("Create Account & Send OTP", use_container_width=True, type="primary"):
                    if not signup_email or not signup_password or not signup_phone:
                        st.error("⚠️ Email, Phone, and Password are all required.")
                    else:
                        try:
                            res = supabase.auth.sign_up({
                                "email": signup_email, 
                                "password": signup_password,
                                "options": {"data": {"phone_number": signup_phone}}
                            })
                            st.session_state.temp_signup_email = signup_email
                            st.session_state.awaiting_otp = True
                            st.rerun()
                        except Exception as e:
                            st.error(f"Sign up failed: {e}")
            
            else:
                # --- OTP VERIFICATION SCREEN ---
                st.write("### 🔐 Verify Identity")
                st.info(f"We sent a 6-digit secure code to **{st.session_state.temp_signup_email}**")
                
                otp_code = st.text_input("Enter 6-Digit OTP Code")
                
                if st.button("Verify Identity & Login", use_container_width=True, type="primary"):
                    if not otp_code:
                        st.error("Please enter the OTP code.")
                    else:
                        try:
                            res = supabase.auth.verify_otp({
                                "email": st.session_state.temp_signup_email,
                                "token": otp_code,
                                "type": "signup"
                            })
                            st.session_state.authenticated = True
                            st.session_state.user_email = st.session_state.temp_signup_email
                            st.session_state.awaiting_otp = False
                            st.rerun()
                        except Exception as e:
                            st.error("Invalid or expired OTP code. Please try again.")
                            
                if st.button("Cancel", use_container_width=True):
                    st.session_state.awaiting_otp = False
                    st.session_state.temp_signup_email = ""
                    st.rerun()

    st.stop()

# ==========================================
# --- SECURE AREA: MAIN APP LOGIC BELOW ---
# ==========================================

with st.sidebar:
    st.success(f"👤 {st.session_state.user_email}")
    if st.button("🚪 Log Out", use_container_width=True):
        supabase.auth.sign_out()
        st.session_state.authenticated = False
        st.session_state.user_email = ""
        st.rerun()
        
    st.divider()
    
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
    filter_type = st.radio("Time Filter Type:", ["Recent Window", "Specific Date Range"])
    
    selected_time = None
    start_date = None
    end_date = None
    
    if filter_type == "Recent Window":
        time_options = [
            "5 Minutes", "15 Minutes", "30 Minutes", "45 Minutes", 
            "1 Hour", "2 Hours", "4 Hours", "6 Hours", "8 Hours", "9 Hours", "10 Hours", "12 Hours", "24 Hours",
            "1 Day", "2 Days", "3 Days", "4 Days", "5 Days", "6 Days", "7 Days",
            "1 Week", "2 Weeks", "3 Weeks", "4 Weeks",
            "1 Month", "2 Months", "3 Months", "4 Months", "5 Months", "6 Months", 
            "7 Months", "8 Months", "9 Months", "10 Months", "11 Months", "12 Months"
        ]
        selected_time = st.selectbox("Look back time:", time_options, index=13) 
    else:
        c1, c2 = st.columns(2)
        with c1: start_date = st.date_input("From Date", value=datetime.today() - timedelta(days=7))
        with c2: end_date = st.date_input("To Date", value=datetime.today())
    
    st.header("3. Job Description")
    jd = st.text_area("JD for Ranking:", height=150, placeholder="Paste JD here (e.g. Python, AWS, 5+ years...)")

    st.header("4. AI Brain (LLM)")
    ai_choice = st.radio("Select AI Engine:", [
        "Anthropic Claude 3.5 (Best Accuracy)", 
        "OpenAI (GPT-4o-mini)", 
        "Google Gemini (Free)"
    ])
    
    if "Claude" in ai_choice:
        with st.expander("❓ How to get a Claude Key"):
            st.markdown("1. Go to [Anthropic Console](https://console.anthropic.com/).\n2. Sign up and verify phone number.\n3. Create API key.")
    elif "Gemini" in ai_choice:
        with st.expander("❓ How to get a FREE Gemini Key"):
            st.markdown("1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey).\n2. Create API key.")
    else:
        with st.expander("❓ How to get an OpenAI Key"):
            st.markdown("1. Go to [OpenAI Platform](https://platform.openai.com/api-keys).\n2. Create secret key.")
            
    api_key = st.text_input(f"Paste your Key here:", type="password")

st.title("🏢 Auto Recruiter: Dashboard")

def get_timedelta(selection):
    val = int(selection.split()[0])
    unit = selection.split()[1].lower()
    if "minute" in unit: return timedelta(minutes=val)
    elif "hour" in unit: return timedelta(hours=val)
    elif "day" in unit: return timedelta(days=val)
    elif "week" in unit: return timedelta(weeks=val)
    elif "month" in unit: return timedelta(days=val * 30)
    return timedelta(days=1)

def extract_details(text, jd_text, key, ai_engine):
    if key:
        prompt = f"""
        You are an expert IT Recruiter. Extract candidate details from the following resume text.
        Job Description: {jd_text if jd_text else 'None provided.'}
        Resume Text: {text[:6000]} 
        Respond STRICTLY with a valid JSON object containing exactly these keys. Do not include markdown formatting or any other text.
        {{
            "Name": "candidate full name or N/A",
            "Email": "email or N/A",
            "Phone": "phone or N/A",
            "Experience": "calculate total years, e.g. 7 Years, or N/A",
            "Skills": "comma-separated list of top 5 skills, or N/A",
            "Match": integer from 0 to 100 representing JD fit
        }}
        """
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if "Claude" in ai_engine:
                    client = anthropic.Anthropic(api_key=key)
                    response = client.messages.create(
                        model="claude-3-5-sonnet-20241022", max_tokens=1000, temperature=0,
                        messages=[{"role": "user", "content": prompt}]
                    )
                    raw_text = response.content[0].text.strip()
                    if raw_text.startswith("```json"): raw_text = raw_text[7:-3].strip()
                    data = json.loads(raw_text)

                elif "Gemini" in ai_engine:
                    genai.configure(api_key=key)
                    model = genai.GenerativeModel('gemini-2.5-flash', generation_config={"response_mime_type": "application/json"})
                    response = model.generate_content(prompt)
                    data = json.loads(response.text)
                else:
                    client = OpenAI(api_key=key)
                    response = client.chat.completions.create(
                        model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}], response_format={ "type": "json_object" } 
                    )
                    data = json.loads(response.choices[0].message.content)
                
                return {
                    "Name": data.get("Name", "N/A"),
                    "Email": data.get("Email", "N/A"),
                    "Phone": data.get("Phone", "N/A"),
                    "Experience": str(data.get("Experience", "N/A")),
                    "Skills": str(data.get("Skills", "N/A")),
                    "Match %": int(data.get("Match", 0) or data.get("Match %", 0)) 
                }
            except Exception as e:
                error_msg = str(e).lower()
                if "429" in error_msg and attempt < max_retries - 1:
                    st.toast(f"Speed Limit hit! Taking a breather... (Attempt {attempt+1}/{max_retries})")
                    time.sleep(10)
                    continue 
                else:
                    st.toast(f"AI Error: {e}")
                    break 

    details = {"Name": "N/A", "Phone": "N/A", "Email": "N/A", "Experience": "N/A", "Skills": "N/A", "Match %": 0}
    phone_pattern = r'[\+\(]?[1-9][0-9 .\-\(\)]{8,}[0-9]'
    phones = re.findall(phone_pattern, text)
    if phones:
        valid_phones = [p for p in phones if len(re.sub(r'\D', '', p)) > 9]
        if valid_phones: details["Phone"] = valid_phones[0]
    email_pattern = r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+'
    emails = re.findall(email_pattern, text)
    if emails: 
        details["Email"] = emails[0]; details["Name"] = emails[0].split('@')[0]
    exp_pattern = r'(\d+)\+?\s*years?'
    exps = re.findall(exp_pattern, text.lower())
    if exps:
        try: details["Experience"] = f"{max([int(x) for x in exps])} Years"
        except: pass
    return details

def read_file_content(file_bytes, filename):
    try:
        if filename.lower().endswith(".pdf"):
            pdf = PdfReader(io.BytesIO(file_bytes))
            return " ".join([page.extract_text() for page in pdf.pages])
        elif filename.lower().endswith(".docx"):
            doc = docx.Document(io.BytesIO(file_bytes))
            return "\n".join([para.text for para in doc.paragraphs])
    except Exception as e: return f"DEBUG_ERROR: {str(e)}"
    return ""

def decode_fname(header_val):
    if not header_val: return ""
    decoded_list = decode_header(header_val)
    filename = ""
    for text, encoding in decoded_list:
        if isinstance(text, bytes): filename += text.decode(encoding if encoding else "utf-8", errors="ignore")
        else: filename += text
    return filename

def run_gmail_scan(user, password, start_dt, end_dt, jd_text, current_key, current_engine):
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    try: mail.login(user, password)
    except Exception as e: return [], f"Login Failed: {e}"

    mail.select("INBOX")
    imap_after = (start_dt - timedelta(days=1)).strftime("%Y/%m/%d")
    imap_before = (end_dt + timedelta(days=2)).strftime("%Y/%m/%d")
    search_cmd = f'(X-GM-RAW "(filename:pdf OR filename:docx) after:{imap_after} before:{imap_before}")'
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
                msg_date_header = msg.get("Date")
                if msg_date_header:
                    try:
                        msg_date = parsedate_to_datetime(msg_date_header).replace(tzinfo=None)
                        if msg_date < start_dt or msg_date > end_dt: continue 
                    except: pass
                
                if msg.is_multipart():
                    for part in msg.walk():
                        if "attachment" in part.get("Content-Disposition", ""):
                            fname = part.get_filename()
                            if fname and fname.lower().endswith(('.pdf', '.docx')):
                                filename = decode_fname(fname)
                                content = read_file_content(part.get_payload(decode=True), filename)
                                if len(content) > 20:
                                    meta = extract_details(content, jd_text, current_key, current_engine)
                                    candidates.append({
                                        "Name": meta.get("Name", "Candidate"), "Email": meta.get("Email", "N/A"),
                                        "Phone": meta.get("Phone", "N/A"), "Experience": meta.get("Experience", "N/A"),
                                        "Skills": meta.get("Skills", "N/A"), "Match %": meta.get("Match %", 0),
                                        "Filename": filename, "Bytes": part.get_payload(decode=True)
                                    })
    mail.logout()
    return candidates, "Success"

def run_outlook_scan(account_obj, start_dt, end_dt, jd_text, current_key, current_engine):
    if not account_obj.is_authenticated: return [], "Please authenticate with Outlook first."
    inbox = account_obj.mailbox().inbox_folder()
    messages = inbox.get_messages(limit=2000) 
    candidates = []
    processed = 0
    status_text = st.empty()
    
    for msg in messages:
        processed += 1
        if processed % 25 == 0: status_text.write(f"Scanning Inbox: Checked {processed} emails...")
        msg_date = getattr(msg, 'received', getattr(msg, 'created', None))
        if msg_date:
            msg_date = msg_date.replace(tzinfo=None)
            if msg_date < start_dt or msg_date > end_dt: continue 
                
        if getattr(msg, 'has_attachments', False):
            try: msg.attachments.download_attachments()
            except: pass 
            for att in msg.attachments:
                if att.name and att.name.lower().endswith(('.pdf', '.docx')):
                    file_bytes = getattr(att, 'content', None)
                    if file_bytes:
                        if isinstance(file_bytes, str): file_bytes = file_bytes.encode('utf-8', errors='ignore')
                        content = read_file_content(file_bytes, att.name)
                        if len(content) > 5: 
                            meta = extract_details(content, jd_text, current_key, current_engine)
                            candidates.append({
                                "Name": meta.get("Name", "Candidate"), "Email": meta.get("Email", "N/A"),
                                "Phone": meta.get("Phone", "N/A"), "Experience": meta.get("Experience", "N/A"),
                                "Skills": meta.get("Skills", "N/A"), "Match %": meta.get("Match %", 0),
                                "Filename": att.name, "Bytes": file_bytes
                            })
                            
    status_text.empty()
    if len(candidates) == 0: return [], f"Done! Scanned {processed} emails, but found 0 resumes."
    return candidates, "Success"

is_ready_to_scan = True
outlook_account = None

if provider == "Outlook / Office 365 (Corporate)":
    if client_id and client_secret:
        if "o365_account" not in st.session_state: st.session_state.o365_account = Account((client_id, client_secret))
        outlook_account = st.session_state.o365_account
        
        if not outlook_account.is_authenticated:
            is_ready_to_scan = False
            if "o365_auth_flow" not in st.session_state:
                flow = outlook_account.con.msal_client.initiate_auth_code_flow(scopes=['https://graph.microsoft.com/User.Read', 'https://graph.microsoft.com/Mail.Read'], redirect_uri='http://localhost:8501')
                st.session_state.o365_auth_flow = flow
            st.warning("⚠️ Outlook Authentication Required")
            st.markdown(f"**Step 1:** [👉 Click here to authorize the App]({st.session_state.o365_auth_flow['auth_uri']})", unsafe_allow_html=True)
            with st.form("auth_form"):
                result_url = st.text_input("**Step 2:** Paste localhost URL here:")
                if st.form_submit_button("Verify Connection") and result_url:
                    try:
                        result = outlook_account.con.msal_client.acquire_token_by_auth_code_flow(auth_code_flow=st.session_state.o365_auth_flow, auth_response=dict(parse_qsl(urlparse(result_url).query)))
                        if "access_token" in result:
                            outlook_account.con.token_backend.token = result
                            outlook_account.con.token_backend.save_token()
                            st.success("✅ Success! You can now scan your inbox."); st.rerun() 
                    except Exception as e: st.error(f"Verification failed: {e}")

if is_ready_to_scan:
    if st.button("🚀 Start Recruiter Engine", type="primary"):
        if filter_type == "Recent Window":
            end_dt = datetime.now()
            start_dt = end_dt - get_timedelta(selected_time)
            status_text = f"Mining Resumes from the last {selected_time}..."
        else:
            start_dt = datetime.combine(start_date, datetime.min.time())
            end_dt = datetime.combine(end_date, datetime.max.time())
            status_text = f"Mining Resumes from {start_date} to {end_date}..."
        
        if provider == "Gmail (Personal/App Password)":
            if not email_user or not email_pass: st.error("Credentials required in the sidebar.")
            else:
                with st.spinner(status_text):
                    cands, stat = run_gmail_scan(email_user, email_pass, start_dt, end_dt, jd, api_key, ai_choice)
                    st.session_state.scanned_candidates = cands; st.session_state.scan_status = stat
        elif provider == "Outlook / Office 365 (Corporate)":
            with st.spinner(status_text):
                cands, stat = run_outlook_scan(outlook_account, start_dt, end_dt, jd, api_key, ai_choice)
                st.session_state.scanned_candidates = cands; st.session_state.scan_status = stat

if "scanned_candidates" in st.session_state and st.session_state.scanned_candidates:
    display_cands = st.session_state.scanned_candidates
    display_cands.sort(key=lambda x: x.get("Match %", 0), reverse=True)

    top_col1, top_col2 = st.columns([3, 1])
    with top_col1: st.success(f"✅ Found {len(display_cands)} Candidates")
    with top_col2:
        export_df = pd.DataFrame([{"Score (%)": c.get('Match %', 0), "Name": c.get('Name', 'N/A'), "Phone": c.get('Phone', 'N/A'), "Email": c.get('Email', 'N/A'), "Experience": c.get('Experience', 'N/A'), "Skills": c.get('Skills', 'N/A')} for c in display_cands])
        st.download_button(label="📊 Download to Excel", data=export_df.to_csv(index=False).encode('utf-8'), file_name=f"candidates_{datetime.now().strftime('%Y%m%d')}.csv", mime="text/csv", use_container_width=True)
    st.divider()

    h1, h2, h3, h4, h5, h6, h7 = st.columns([1, 1.5, 1.5, 2, 2, 1, 1])
    h1.markdown("**Score**"); h2.markdown("**Name**"); h3.markdown("**Phone**"); h4.markdown("**Email**"); h5.markdown("**Skills**"); h6.markdown("**Exp**"); h7.markdown("**Resume**")
    st.markdown("---")

    for i, c in enumerate(display_cands):
        col1, col2, col3, col4, col5, col6, col7 = st.columns([1, 1.5, 1.5, 2, 2, 1, 1])
        with col1: st.write(f"**{c.get('Match %', 0)}%**")
        with col2: st.write(c.get('Name', 'N/A'))
        with col3: st.write(c.get('Phone', 'N/A'))
        with col4: st.caption(c.get('Email', 'N/A'))
        with col5: st.caption(c.get('Skills', 'N/A'))
        with col6: st.write(c.get('Experience', 'N/A'))
        with col7: st.download_button(label="📥 PDF", data=c['Bytes'], file_name=c['Filename'], mime="application/octet-stream", key=f"dl_{i}_{c['Filename']}")
        st.markdown("<hr style='margin: 0px; opacity: 0.2;'>", unsafe_allow_html=True)

elif "scan_status" in st.session_state and st.session_state.scan_status != "Success":
    st.warning(st.session_state.scan_status)
