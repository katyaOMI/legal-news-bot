"""Redact credentials before logging, persistence or diagnostic output."""
import os
import re
from urllib.parse import quote, quote_plus


RULES = (
    (r'\b\d{6,15}(?::|%3a)[A-Za-z0-9_-]{30,}\b', '[REDACTED]'),
    (r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{35,})\b', '[REDACTED]'),
    (r'\b(?:sk-(?:proj-|or-v1-)?[A-Za-z0-9_-]{24,}|AIza[A-Za-z0-9_-]{30,})\b', '[REDACTED]'),
    (r'((?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|https?)://)[^\s/@]+:[^\s/@]+@', r'\1[REDACTED]@'),
    (r'(?i)(authorization\s*[:=]\s*(?:bearer|basic)\s+)[^\s,;]+', r'\1[REDACTED]'),
    (r'''(?i)((?:password|passwd|pwd|api[_-]?key|(?:access[_-]?)?token)\s*[=:]\s*)(?:"[^"]*"|'[^']*'|[^\s,;]+)''', r'\1[REDACTED]'),
    (r'(\b(?:chat_id|user_id|telegram_id)\b\W{0,10})-?\d+', r'\1[REDACTED]'),
)


def redact(message):
    try:
        text = str(message)
    except Exception:
        return '[message unavailable]'
    # Read environment values only in memory; never enumerate them in reports.
    secrets = [value for name,value in os.environ.items() if value
               and (name.upper() == 'DATABASE_URL' or re.search(r'(?:TOKEN|PASSWORD|SECRET|API_KEY)$',name,re.I))]
    for value in sorted(secrets,key=len,reverse=True):
        for variant in {value,quote(value,safe=''),quote_plus(value,safe='')}:
            text=text.replace(variant,'[REDACTED]')
    for pattern,replacement in RULES:
        text=re.sub(pattern,replacement,text,flags=re.I)
    return text
