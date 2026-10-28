"""
Sends the sign-in emails through an email service's SMTP server (Resend, Postmark, Amazon SES,
Brevo and the like all offer one). Mail sent straight from a home connection lands in spam.
With no CRAPS_SMTP_HOST the email is written to the server log instead.
"""

import html as html_lib
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid

from . import config

log = logging.getLogger("craps.mail")


def send(to, subject, text, html=None):
    message = EmailMessage()
    message["From"] = config.MAIL_FROM
    message["To"] = to
    message["Subject"] = subject
    message["Message-ID"] = make_msgid(domain=config.MAIL_FROM.rpartition("@")[2].strip("> ") or None)
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")

    if not config.SMTP_HOST:
        log.warning("No CRAPS_SMTP_HOST set, so this email was not sent:\nTo: %s\nSubject: %s\n\n%s", to, subject, text)
        return
    try:
        if config.SMTP_SECURITY == "ssl":
            server = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=20,
                                      context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20)
            if config.SMTP_SECURITY == "starttls":
                server.starttls(context=ssl.create_default_context())
        with server:
            if config.SMTP_USER:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(message)
    except (OSError, smtplib.SMTPException):
        log.exception("Could not send the email to %s", to)


def sign_in_email(to, link, minutes):
    subject = f"Your {config.SITE_NAME} sign-in link"
    text = (f"Here is your link to sign in to {config.SITE_NAME}:\n\n{link}\n\n"
            f"It works once, for the next {minutes} minutes. If you didn't ask for it, ignore this email: "
            "nobody can sign in without the link.\n")
    html = (f"<p>Here is your link to sign in to {config.SITE_NAME}:</p>"
            f'<p><a href="{link}" style="font-size:18px;font-weight:bold">Sign in</a></p>'
            f"<p>It works once, for the next {minutes} minutes. If you didn't ask for it, ignore this email: "
            "nobody can sign in without the link.</p>")
    send(to, subject, text, html)


def claim_email(to, link, host_name, guest_name, minutes):
    subject = f"{host_name} sent you your {config.SITE_NAME} history"
    text = (f"{host_name} has been keeping your throws as the guest \"{guest_name}\" on {config.SITE_NAME}. "
            f"Open this link to put them in an account of your own (one is made for you if you're new):\n\n{link}\n\n"
            f"It works once, for the next {minutes} minutes.\n")
    host, guest = html_lib.escape(host_name), html_lib.escape(guest_name)
    html = (f"<p>{host} has been keeping your throws as the guest <b>{guest}</b> on {config.SITE_NAME}. "
            "Open this link to put them in an account of your own (one is made for you if you're new):</p>"
            f'<p><a href="{link}" style="font-size:18px;font-weight:bold">Take my history</a></p>'
            f"<p>It works once, for the next {minutes} minutes.</p>")
    send(to, subject, text, html)
