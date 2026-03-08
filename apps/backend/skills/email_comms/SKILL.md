# Email Communications

Native IMAP/SMTP email integration. Read, search, send, and reply to emails directly.

## Tools

| Tool | Risk | Description |
|------|------|-------------|
| `email_read_inbox` | safe | Read recent inbox emails (subject, sender, date, body preview) |
| `email_search` | safe | Search by keyword across subject, from, body |
| `email_get_thread` | safe | Fetch full thread by Message-ID |
| `email_send` | moderate | Send a new email (confirm-gated) |
| `email_reply` | moderate | Reply to an email by Message-ID (confirm-gated) |

## Configuration

Set in `.env`:

```
ALLOW_EMAIL=true
EMAIL_IMAP_HOST=imap.gmail.com
EMAIL_IMAP_PORT=993
EMAIL_SMTP_HOST=smtp.gmail.com
EMAIL_SMTP_PORT=587
EMAIL_USERNAME=you@gmail.com
EMAIL_PASSWORD=app-password-here
EMAIL_USE_TLS=true
```

> For Gmail: enable 2FA, then create an App Password at https://myaccount.google.com/apppasswords

## Status

✅ Implemented — v5.4.0
