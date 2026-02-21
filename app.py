import streamlit as st
import pandas as pd
import imaplib
import email
from email.header import decode_header
import io
import re
import base64
import json
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qsl

# To prevent crashes if these aren't installed yet
try:
    from pypdf import PdfReader
    import docx
    from O365 import Account
    from openai import OpenAI
    import google.generativeai as genai
except ImportError:
    pass

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
    jd = st.text_area("JD for Ranking:", height=150, placeholder="Paste JD here (e.g. Python, AWS, 5+ years...)")

    st.header("4. AI Brain (LLM)")
    ai_choice = st.radio("Select AI Engine:", ["Google Gemini (Free)", "OpenAI (GPT-4o-mini)"])
    
    if "Gemini" in ai_choice:
        with st.expander("❓ How to get a FREE Gemini Key"):
            st.markdown("""
            **Why do I need my own key?**
            To keep this Enterprise tool 100% free and to guarantee your candidate data remains private to your agency, this app uses a "Bring Your Own Key" model.
            
            1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey).
            2. Sign in with Google.
            3. Click **Create API key**.
            """)
    else:
        with st.expander("❓ How to get an OpenAI Key"):
            st.markdown("""
            **Why do I need my own key?**
            To keep this Enterprise tool 100% free and to guarantee your candidate data remains private to your agency, this app uses a "Bring Your Own Key" model.
            
            1. Go to [OpenAI Platform](https://platform.openai.com/api-keys).
            2. Click **Create new secret key**.
            3. *Note: Requires a $5 minimum credit balance in Settings > Billing.*
            """)
            
    api_key = st.text_input(f"Paste your {ai_choice.split()[0]} Key here:", type="password")

# --- SHARED HELPERS ---
def extract_details(text, jd_text, key, ai_engine):
    # --- DUAL AI LLM EXTRACTION ---
    if key:
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
        "Match": (Integer, 0 to 100 score of how well the candidate fits the Job Description. Return 0 if no JD).
        """
        try:
            if "Gemini" in ai_engine:
                genai.configure(api_key=key)
                model = genai.GenerativeModel('gemini-1.5-flash', generation_config={"response_mime_type": "application/json"})
                response = model.generate_content(prompt)
                data = json.loads(response.text)
            else:
                client = OpenAI(api_key=key)
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
        except Exception as e:
            st.toast(f"{ai_engine.split()[0]} Error: {e}")
            pass # Fallback to regex below if API fails

    # --- DUMB REGEX FALLBACK ---
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
def run_gmail_scan(user, password, days, jd_text, current_key, current_engine):
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
                                        meta = extract_details(content, jd_text, current_key, current_engine)
                                        candidates.append({
                                            "Name": meta.get("Name", "Candidate"),
                                            "Email": meta.get("Email", "N/A"),
                                            "Phone": meta.get("Phone", "N/A"),
                                            "Experience": meta.get("Experience", "N/A"),
                                            "Skills": meta.get("Skills", "N/A"),
                                            "Match %": meta.get("Match %", 0),
                                            "Filename": filename,
                                            "Bytes": part.get_payload(decode=True),
                                            "text": content
                                        })
    mail.logout()
    return candidates, "Success"

# --- OUTLOOK ENGINE ---
def run_outlook_scan(account_obj, days, jd_text, current_key, current_engine):
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
                
        if getattr(msg, 'has_attachments', False):
            try:
                msg.attachments.download_attachments()
            except Exception:
                pass 
                
            for att in msg.attachments:
                if att.name and att.name.lower().endswith(('.pdf', '.docx')):
                    file_bytes = getattr(att, 'content'),

