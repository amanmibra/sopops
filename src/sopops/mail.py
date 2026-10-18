"""Sending email: sign-in links and invites, over SMTP.

Set SOPOPS_SMTP_HOST and SOPOPS_SMTP_FROM (the sender, `sopops <sopops@example.com>` or a bare address; it defaults to
SOPOPS_SMTP_USER when that's an address), plus SOPOPS_SMTP_PORT (default 587), SOPOPS_SMTP_USER and
SOPOPS_SMTP_PASSWORD when the server needs a login. Port 465 speaks TLS from the start (SMTPS); any other port upgrades
with STARTTLS when the server offers it, and sopops won't send a password over a connection that isn't encrypted.

SOPOPS_EMAIL_DEV=1 is for local use and demos: nothing is sent (even with SMTP set up), and each sign-in link is
written to the server log at INFO instead. Links are never logged otherwise.

`send` blocks (smtplib); call it from a worker thread (a sync route, or a background task), never on the event loop.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr

log = logging.getLogger("sopops.mail")

HOST_ENV = "SOPOPS_SMTP_HOST"
PORT_ENV = "SOPOPS_SMTP_PORT"
USER_ENV = "SOPOPS_SMTP_USER"
PASSWORD_ENV = "SOPOPS_SMTP_PASSWORD"
FROM_ENV = "SOPOPS_SMTP_FROM"
DEV_ENV = "SOPOPS_EMAIL_DEV"
DEFAULT_PORT = 587
SMTPS_PORT = 465
TIMEOUT_S = 15
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


class MailError(Exception):
    """The email couldn't be sent. The message is safe to show (no credentials)."""


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    text: str
    html: str


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def dev() -> bool:
    """SOPOPS_EMAIL_DEV: log sign-in links instead of sending them."""
    return _env(DEV_ENV).lower() in ("1", "true", "yes", "on")


def sender() -> str:
    got = _env(FROM_ENV)
    if got:
        return got
    user = _env(USER_ENV)
    return user if "@" in user else ""


def smtp() -> bool:
    """Whether SMTP is set up: a host and a sender."""
    return bool(_env(HOST_ENV) and "@" in parseaddr(sender())[1])


def sends() -> bool:
    """Whether sopops sends email: SMTP is set up and dev mode is off."""
    return smtp() and not dev()


def port() -> int:
    try:
        return int(_env(PORT_ENV) or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def send(msg: Message) -> None:
    """Send one email over SMTP. Raises MailError."""
    if not smtp():
        raise MailError("email isn't set up on this sopops (SOPOPS_SMTP_HOST and SOPOPS_SMTP_FROM)")
    host, user, password = _env(HOST_ENV), _env(USER_ENV), os.environ.get(PASSWORD_ENV, "")
    name, address = parseaddr(sender())
    em = EmailMessage()
    em["Subject"] = msg.subject
    em["From"] = formataddr((name or "sopops", address))
    em["To"] = msg.to
    em["Message-ID"] = make_msgid(domain=address.rpartition("@")[2])
    em.set_content(msg.text)
    em.add_alternative(msg.html, subtype="html")
    context = ssl.create_default_context()
    try:
        if port() == SMTPS_PORT:
            conn: smtplib.SMTP = smtplib.SMTP_SSL(host, port(), timeout=TIMEOUT_S, context=context)
        else:
            conn = smtplib.SMTP(host, port(), timeout=TIMEOUT_S)
        with conn:
            conn.ehlo()
            if not isinstance(conn, smtplib.SMTP_SSL):
                if conn.has_extn("starttls"):
                    conn.starttls(context=context)
                    conn.ehlo()
                elif user and host not in LOCAL_HOSTS:
                    raise MailError(f"{host} doesn't offer STARTTLS, and sopops won't send the SMTP password unencrypted. Use port 465 or a server with TLS")
            if user:
                conn.login(user, password)
            conn.send_message(em)
    except MailError:
        raise
    except smtplib.SMTPAuthenticationError:
        raise MailError(f"{host} refused the SMTP login (check SOPOPS_SMTP_USER and SOPOPS_SMTP_PASSWORD)") from None
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"couldn't send email through {host}:{port()} ({type(e).__name__})") from None
