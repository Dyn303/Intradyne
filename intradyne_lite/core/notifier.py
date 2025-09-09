
from __future__ import annotations
import os, smtplib, ssl
from email.mime.text import MIMEText
from typing import Optional
import json, urllib.request

def send_telegram(msg: str) -> bool:
    token = os.getenv("TG_BOT_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")
    if not token or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({"chat_id": chat_id, "text": msg}).encode()
        req = urllib.request.Request(url, data=data, headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status==200
    except Exception:
        return False

def send_email(msg: str, subject: str = "IntraDyne Alert") -> bool:
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    pwd  = os.getenv("SMTP_PASS")
    to   = os.getenv("SMTP_TO")
    from_ = os.getenv("SMTP_FROM", user or "intradyne@localhost")
    if not (host and to):
        return False
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.starttls(context=context)
            if user and pwd:
                server.login(user, pwd)
            msg_obj = MIMEText(msg)
            msg_obj["Subject"] = subject
            msg_obj["From"] = from_
            msg_obj["To"] = to
            server.sendmail(from_, [to], msg_obj.as_string())
        return True
    except Exception:
        return False

def notify(msg: str, subject: str = "IntraDyne Alert") -> dict:
    t = send_telegram(msg)
    e = send_email(msg, subject)
    return {"telegram": t, "email": e}
