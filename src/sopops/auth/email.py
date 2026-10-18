"""Sign in with email: a link sent to the address, which works once, for 15 minutes.

On when SMTP is set up (`sopops.mail`) or SOPOPS_EMAIL_DEV is on, which logs each link at INFO instead of sending it.
The identity is the address itself, verified by opening the link, so it links to a GitHub or Google user with the same
verified email. The link carries its token after `#` (`<sopops>/sign-in/email#<token>`): browsers never send that
part to a server, so it stays out of access logs, and a mail scanner that fetches the link can't use it up. The page
then asks for one click, which posts the token (`POST /v1/auth/email/confirm`), so a scanner that runs the page's
script doesn't sign in either.

Also the emails themselves: the sign-in link, invites (`invite_message`) and links that set a password
(`password_message`).
"""

from __future__ import annotations

import html
import logging

from .. import mail
from .providers import LoginProvider, register

log = logging.getLogger("sopops.auth")
dev_log = logging.getLogger("sopops.auth.email")

LINK_MINUTES = 15
SIGN_IN_SUBJECT = "Your sopops sign-in link"


class EmailLogin(LoginProvider):
    id = "email"
    label = "Email"
    redirects = False

    def configured(self) -> bool:
        return mail.smtp() or mail.dev()


def link_url(public_url: str, token: str) -> str:
    return f"{public_url}/sign-in/email#{token}"


def sign_in_message(to: str, link: str, site: str) -> mail.Message:
    text = (
        f"Someone, hopefully you, asked to sign in to sopops at {site} with {to}.\n\n"
        f"Open this link to sign in:\n{link}\n\n"
        f"It works once, for {LINK_MINUTES} minutes.\n\n"
        "If you didn't ask for this, ignore this email. Nobody can sign in without the link.\n"
    )
    body = (
        f"<p>Someone, hopefully you, asked to sign in to sopops at {_e(site)} with {_e(to)}.</p>"
        f'<p><a href="{_e(link)}" style="{BUTTON}">Sign in to sopops</a></p>'
        f"<p>It works once, for {LINK_MINUTES} minutes.</p>"
        "<p>If you didn't ask for this, ignore this email. Nobody can sign in without the link.</p>"
    )
    return mail.Message(to=to, subject=SIGN_IN_SUBJECT, text=text, html=_page(body))


def invite_message(to: str, link: str, inviter: str, org: str, role: str, days: int) -> mail.Message:
    who = inviter or "Someone"
    as_role = "a member" if role == "member" else f"an {role}"
    text = (
        f"{who} invited you to join {org} on sopops, as {as_role}.\n\n"
        f"Open this link and sign in with {to}:\n{link}\n\n"
        f"It works for {days} days. Signing in with {to} any other way joins you too.\n\n"
        "If you weren't expecting this, ignore this email.\n"
    )
    body = (
        f"<p>{_e(who)} invited you to join <b>{_e(org)}</b> on sopops, as {as_role}.</p>"
        f'<p><a href="{_e(link)}" style="{BUTTON}">Join {_e(org)}</a></p>'
        f"<p>Sign in with {_e(to)}. The link works for {days} days.</p>"
        "<p>If you weren't expecting this, ignore this email.</p>"
    )
    return mail.Message(to=to, subject=f"{who} invited you to {org} on sopops", text=text, html=_page(body))


def password_message(to: str, link: str, site: str, has_account: bool) -> mail.Message:
    """A link that sets a password: "Forgot password?", or making an account at an allowed domain."""
    asked = f"reset the password for {to}" if has_account else f"make an account with {to}"
    after = "Your password stays the same." if has_account else "Nobody gets an account without the link."
    text = (
        f"Someone, hopefully you, asked to {asked} on sopops at {site}.\n\n"
        f"Open this link to set a password:\n{link}\n\n"
        "It works once, for an hour.\n\n"
        f"If you didn't ask for this, ignore this email. {after}\n"
    )
    body = (
        f"<p>Someone, hopefully you, asked to {_e(asked)} on sopops at {_e(site)}.</p>"
        f'<p><a href="{_e(link)}" style="{BUTTON}">Set a password</a></p>'
        "<p>It works once, for an hour.</p>"
        f"<p>If you didn't ask for this, ignore this email. {after}</p>"
    )
    subject = "Reset your sopops password" if has_account else "Make your sopops account"
    return mail.Message(to=to, subject=subject, text=text, html=_page(body))


def deliver(msg: mail.Message, link: str) -> None:
    """Send a sign-in or password link, or in dev mode write it to the log. Never raises: the person asking is told
    the same thing either way, and a failure is logged without the link."""
    if mail.dev():
        _show_dev_log()
        dev_log.info("[SOPOPS_EMAIL_DEV] %s for %s (not emailed): %s", "sign-in link" if msg.subject == SIGN_IN_SUBJECT else "password link", msg.to, link)
        return
    try:
        mail.send(msg)
    except mail.MailError as e:
        log.warning("couldn't email a link to %s: %s", msg.to, e)


def _show_dev_log() -> None:
    """Dev mode's links must reach the server's output: `sopops` sets up no logging of its own (uvicorn logs only its
    loggers), so give this one logger INFO and, with no handler anywhere, its own on stderr."""
    if not dev_log.isEnabledFor(logging.INFO):
        dev_log.setLevel(logging.INFO)
    if not dev_log.hasHandlers():
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
        dev_log.addHandler(handler)


BUTTON = "display:inline-block;padding:10px 16px;border-radius:6px;background:#111;color:#fff;text-decoration:none;font-weight:600"


def _page(body: str) -> str:
    return f'<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;font-size:15px;line-height:1.5;color:#111;max-width:520px">{body}</div>'


def _e(s: str) -> str:
    return html.escape(s, quote=True)


register(EmailLogin())
