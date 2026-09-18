from __future__ import annotations

import os
import smtplib
import urllib.parse
import urllib.request
from email.message import EmailMessage

from rich.console import Console

console = Console()


def _send_telegram(message: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": message, "disable_web_page_preview": "true"}
    ).encode()
    try:
        with urllib.request.urlopen(url, data=payload, timeout=15) as resp:
            return 200 <= resp.status < 300
    except Exception as exc:
        console.log(f"[yellow]Aviso Telegram fallido: {exc}[/]")
        return False


def _send_email(message: str) -> bool:
    host = os.getenv("SMTP_HOST")
    to_addr = os.getenv("SMTP_TO")
    if not host or not to_addr:
        return False
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    from_addr = os.getenv("SMTP_FROM", user or to_addr)
    use_tls = os.getenv("SMTP_TLS", "true").lower() not in ("0", "false", "no")

    msg = EmailMessage()
    msg["Subject"] = "InfoJobs scraper: run bloqueada"
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(message)
    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            if use_tls:
                server.starttls()
            if user and password:
                server.login(user, password)
            server.send_message(msg)
        return True
    except Exception as exc:
        console.log(f"[yellow]Aviso email fallido: {exc}[/]")
        return False


def notify(message: str) -> bool:
    """Envia un aviso por Telegram o, si no esta configurado, por SMTP.

    Devuelve True si algun canal confirmo el envio. Si no hay ningun canal
    configurado, devuelve False sin lanzar error.
    """
    sent = _send_telegram(message)
    if not sent:
        sent = _send_email(message)
    if sent:
        console.log("[green]Aviso enviado.[/]")
    else:
        console.log("[dim]Sin canales de aviso configurados (TELEGRAM_*/SMTP_*).[/]")
    return sent
