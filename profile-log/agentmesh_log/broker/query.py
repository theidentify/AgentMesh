"""Analyzer and scorer copied from memory-sync/recall_memory.py (Phase 3)."""
from dataclasses import dataclass
from datetime import datetime, timezone
import re

TASK_RE = re.compile(r'\b[A-Z][A-Z0-9]{1,15}-\d+\b', re.IGNORECASE)
WORD_RE = re.compile(r'[A-Za-z0-9_-]{2,}|[\u0E00-\u0E7F]{2,}')
STOP_WORDS = set('what when where which with from that this current about status memory project task ตอนนี้ อะไร ยังไง อย่างไร ของ และ หรือ ที่ ใน เป็น มี ไหม บ้าง ล่าสุด สถานะ งาน ใช้ เดิม แยก'.split())
INTENT_TERMS = {
    'status': ('status', 'current', 'latest', 'สถานะ', 'ล่าสุด', 'ถึงไหน'),
    'constraint': ('constraint', 'scope', 'exclude', 'ข้อจำกัด', 'ห้าม', 'ตัดออก'),
    'decision': ('decision', 'why', 'ทำไม', 'เลือก', 'ตัดสินใจ', 'เหตุผล'),
    'procedure': ('procedure', 'how', 'workflow', 'วิธี', 'ขั้นตอน', 'ควร', 'อย่างไร', 'ยังไง'),
    'preference': ('preference', 'prefer', 'ชอบ', 'ต้องการ'),
    'evidence': ('source', 'evidence', 'proof', 'อ้างอิง', 'หลักฐาน', 'มาจากไหน'),
}
THAI_SPLIT = re.compile('|'.join(re.escape(t) for t in sorted(
    STOP_WORDS | {'ทำไม', 'ควร', 'ทำ', 'ข้อจำกัด', 'สำคัญ', 'แต่'}, key=lambda t: (-len(t), t))))
KIND_WEIGHTS = {'constraint': 16, 'open_loop': 15, 'decision': 14,
                'procedure': 12, 'preference': 11, 'fact': 10, 'summary': 9,
                'user_request': 5, 'assistant_response': 4, 'verification': 4,
                'error': 3, 'tool_result': 1, 'tool_call': 0}
EVENT_KINDS = ('user_request', 'assistant_response', 'decision', 'verification', 'error', 'task_transition')


@dataclass(frozen=True)
class QueryPlan:
    query: str
    project: str | None
    task_refs: tuple[str, ...]
    intents: tuple[str, ...]
    terms: tuple[str, ...]


def analyze_query(query, *, project=None, task=None):
    normalized = ' '.join(query.split())
    terms = []
    for token in WORD_RE.findall(normalized.lower()):
        pieces = THAI_SPLIT.split(token) if re.search(r'[\u0E00-\u0E7F]', token) else [token]
        for piece in pieces:
            piece = piece.strip('_- ')
            if len(piece) >= 2 and piece not in STOP_WORDS and piece not in terms:
                terms.append(piece)
    return QueryPlan(normalized, project, tuple(sorted(set(t.upper() for t in TASK_RE.findall(normalized)) | ({task.upper()} if task else set()))),
                     tuple(k for k, needles in INTENT_TERMS.items()
                           if any(n in normalized.lower() for n in needles)) or ('status',),
                     tuple(terms[:12]))


def _score(candidate, plan):
    text = candidate['content'].lower()
    matched = sum(term in text for term in plan.terms)
    kind = candidate['kind']
    score = (25 if plan.query.lower() in text else 0) + matched * 4 + matched * matched * 3
    score += sum(24 for task in plan.task_refs if task.lower() in text)
    score += KIND_WEIGHTS.get(kind, 2) + float(candidate.get('confidence', 0.6)) * 5
    if str(candidate.get('task_ref') or candidate.get('scope_key') or '').upper() in plan.task_refs:
        score += 28
    if plan.project and candidate.get('project') == plan.project:
        score += 12
    if kind in plan.intents:
        score += 18
    if 'status' in plan.intents and kind in ('open_loop', 'fact', 'summary'):
        score += 8
    value = candidate.get('updated_at') or candidate.get('occurred_at')
    try:
        instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
        instant = instant.replace(tzinfo=timezone.utc) if instant.tzinfo is None else instant
        age = max(0, (datetime.now(timezone.utc) - instant).total_seconds() / 86400)
        score += max(0, 6 - min(age, 30) / 5)
    except (ValueError, TypeError, AttributeError):
        pass
    candidate['matched_terms'] = matched
    candidate['score'] = round(score, 3)


