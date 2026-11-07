"""Outgoing email for Kassara.

Production: SMTP through the cPanel mailbox (e.g. no-reply@kassara.org) set in .env.
Development: when SMTP_HOST is empty, emails are printed to the server log instead.
Sending never breaks a request: failures are logged and the action still succeeds.
"""
import html
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import config


def _log(msg):
    print(f"[kassara-mail] {msg}", file=sys.stderr, flush=True)


def send(to, subject, lines, button=None):
    """Send a simple branded email. `lines` are paragraphs; `button` is (label, url)."""
    if not to:
        return False
    text = "\n\n".join(lines) + (f"\n\n{button[0]}: {button[1]}" if button else "") + \
        "\n\n— Kassara\nhttps://kassara.org"
    paras = "".join(f'<p style="margin:0 0 14px">{html.escape(p)}</p>' for p in lines)
    btn = (f'<p style="margin:22px 0"><a href="{html.escape(button[1])}" style="background:#247e4c;'
           f'color:#fff;text-decoration:none;padding:12px 18px;border-radius:8px;font-weight:700;'
           f'display:inline-block">{html.escape(button[0])}</a></p>') if button else ""
    body = (f'<div style="font-family:system-ui,Segoe UI,Arial,sans-serif;color:#183627;max-width:520px;'
            f'margin:0 auto;padding:24px"><p style="font-weight:800;font-size:18px;margin:0 0 20px">'
            f'◈ kassara</p>{paras}{btn}<p style="color:#7a8b7e;font-size:12px;margin-top:28px">'
            f'Kassara · test environment · no real money moves.</p></div>')

    if not config.SMTP_HOST:
        _log(f"(console) to={to} subject={subject!r}\n{text}")
        return True
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((config.MAIL_FROM_NAME, config.MAIL_FROM))
    msg["To"] = to
    msg["Message-ID"] = make_msgid(domain=config.MAIL_FROM.split("@")[-1])
    msg.set_content(text)
    msg.add_alternative(body, subtype="html")
    try:
        if config.SMTP_PORT == 465:
            with smtplib.SMTP_SSL(config.SMTP_HOST, 465, context=ssl.create_default_context(), timeout=15) as s:
                s.login(config.SMTP_USER, config.SMTP_PASSWORD)
                s.send_message(msg)
        else:
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(config.SMTP_USER, config.SMTP_PASSWORD)
                s.send_message(msg)
        return True
    except Exception as exc:  # never fail the user's action because email failed
        _log(f"send failed to={to} subject={subject!r}: {exc}")
        return False


def money(minor, currency):
    return f"{minor / 100:,.2f} {currency.upper()}"
