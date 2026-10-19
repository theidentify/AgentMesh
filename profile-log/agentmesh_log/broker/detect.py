"""Bounded built-in secret and PII rules. Matches never enter diagnostics."""
import base64
from collections import Counter
from dataclasses import dataclass
import json
import math
import re
from typing import Protocol
from ..model import Label


@dataclass(frozen=True)
class Finding:
    kind: str
    category: str
    start: int
    end: int


class Detector(Protocol):
    def scan(self, text: str) -> list[Finding]: ...


# Lookarounds anchor token boundaries without requiring a whole-string match.
RULES = {
    'pem': r'-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----[\s\S]{1,16384}?-----END (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----',
    'aws': r'(?<![A-Za-z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Za-z0-9])',
    'github': r'(?<![\w])gh[pousr]_[A-Za-z0-9]{20,255}(?![\w])',
    'github_pat': r'(?<![\w])github_pat_[A-Za-z0-9_]{22,255}(?![\w])',
    'slack': r'(?<![\w])xox[abprs]-[A-Za-z0-9-]{10,255}(?![\w-])',
    'anthropic': r'(?<![\w])sk-ant-[A-Za-z0-9_-]{20,255}(?![\w-])',
    'openai': r'(?<![\w])sk-[A-Za-z0-9_-]{32,255}(?![\w-])',
    'google': r'(?<![\w])AIza[A-Za-z0-9_-]{35}(?![\w-])',
    'stripe': r'(?<![\w])(?:sk|rk)_live_[A-Za-z0-9]{16,255}(?![\w])',
    'jwt': r'(?<![\w-])[A-Za-z0-9_-]{4,2048}\.[A-Za-z0-9_-]{2,4096}\.[A-Za-z0-9_-]{8,2048}(?![\w.-])',
    'assignment': r'(?i)(?<![\w])(?:password|passwd|secret|token|api_key)[ \t]{0,16}[=:][ \t]{0,16}["\']?([^\s"\',;]{12,256})',
}
COMPILED = {kind: re.compile(pattern) for kind, pattern in RULES.items()}
PII = {
    'email': re.compile(r'(?<![\w.+-])[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){1,6}(?![\w.-])'),
    'phone': re.compile(r'(?<![\w+])(?:\+[1-9][0-9]{7,14}|0[0-9]-[0-9]{4}-[0-9]{4}|0[0-9]{2}-[0-9]{3}-[0-9]{4})(?![\w])'),
    'thai_id': re.compile(r'(?<![\w])[0-9]{13}(?![\w])'),
    'card': re.compile(r'(?<![\w])(?:[0-9][ -]?){12,18}[0-9](?![\w])'),
}


def luhn(value):
    digits = [int(c) for c in value]
    return sum((n*2-9 if n*2 > 9 else n*2) if i%2 else n for i,n in enumerate(reversed(digits)))%10 == 0


def valid_secret(kind, match):
    if kind == 'jwt':
        try:
            header = match[0].split('.')[0]
            obj = json.loads(base64.urlsafe_b64decode(header + '='*(-len(header)%4)))
            return isinstance(obj, dict) and 'alg' in obj
        except Exception: return False
    if kind == 'assignment':
        value = match[1]; counts = Counter(value)
        return -sum(n/len(value)*math.log2(n/len(value)) for n in counts.values()) >= 3.5
    return True


class RegexDetector:
    def scan(self, text):
        result = []
        for kind, pattern in COMPILED.items():
            for m in pattern.finditer(text):
                if valid_secret(kind, m): result.append(Finding(kind, 'secret', m.start(), m.end()))
        for kind, pattern in PII.items():
            for m in pattern.finditer(text):
                value = re.sub('[ -]', '', m[0])
                if kind == 'thai_id' and (int(value[-1]) != (11-sum(int(v)*(13-i) for i,v in enumerate(value[:12]))%11)%10): continue
                if kind == 'card' and (not re.match(r'(?:4|5[1-5]|3[47]|6(?:011|5)|2[2-7])', value) or not luhn(value)): continue
                result.append(Finding(kind, 'pii', m.start(), m.end()))
        return result


def redact(text, findings):
    spans = sorted((f for f in findings if f.category == 'secret'), key=lambda f: (f.start, -f.end, f.kind))
    merged = []
    for f in spans:
        if merged and f.start < merged[-1][1]:
            start, end, kind = merged[-1]; merged[-1] = start, max(end, f.end), kind
        else: merged.append((f.start, f.end, f.kind))
    for start, end, kind in reversed(merged): text = text[:start] + '[REDACTED:'+kind+']' + text[end:]
    return text


def sanitize(body, tags, task, detector=None):
    detector = detector or RegexDetector(); detected = Label.general; counts = Counter()
    def walk(value):
        nonlocal detected
        if type(value) is str:
            findings = detector.scan(value)
            for f in findings:
                detected = max(detected, Label.secret if f.category == 'secret' else Label.internal)
                if f.category == 'secret': counts[f.kind] += 1
            return redact(value, findings)
        if type(value) is list: return [walk(v) for v in value]
        if type(value) is dict:
            for key in value:
                if len(key) > 64 or any(f.category == 'secret' for f in detector.scan(key)): raise ValueError('invalid_request')
            return {key: walk(v) for key,v in value.items()}
        return value
    return walk(body), walk(tags), walk(task), detected, dict(counts)
