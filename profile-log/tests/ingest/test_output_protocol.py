"""Output planning contract; strict validation remains the acceptance boundary."""
import copy
import json
import unittest
from typing import Any

from bounded_protocol import build_request, validate, ValidationError


class OutputProtocolTests(unittest.TestCase):
    def setUp(self):
        self.events = [
            dict(id=10, project='demo', kind='user_request', content='Keep spaces  exactly.\nบรรทัดใหม่', occurred_at='2026-10-08T01:00:00Z'),
            dict(id=11, project='demo', kind='assistant_response', content='Acknowledged the request.', occurred_at='2026-10-08T01:01:00Z'),
        ]
        self.related = [dict(memory_key='project.demo.constraint.literal', kind='constraint', scope='project', scope_key='demo', project='demo', content='Old context only.', status='active', confidence=1)]
        self.item: dict[str, Any] = dict(memory_key=self.related[0]['memory_key'], content='Retain exact spacing.', status='active', confidence=1, source_event_ids=[10], evidence=[dict(event_id=10, quote='spaces  exactly.\nบรรทัดใหม่')])
        self.payload: dict[str, Any] = dict(items=[self.item], reviewed_event_ids=[10, 11], conflicts=[])

    def test_request_requires_event_bound_quotes_and_unique_key_planning(self):
        request = build_request(self.events, self.related)
        instructions = request['instructions']
        # This is a request contract, not a probabilistic assertion about a model.
        for rule in ('ONE item per memory_key', 'same event object', 'related_existing is identity/context only', 'Do not normalize whitespace', 'final chronological state'):
            self.assertIn(rule, instructions)
        self.assertEqual(json.loads(request['input'][0]['content']), dict(events=self.events, related_existing=self.related))
        self.assertFalse(request['store'])
        self.assertEqual(request['reasoning'], {'effort': 'low'})

    def test_literal_multiline_unicode_quote_is_accepted(self):
        result = validate(copy.deepcopy(self.payload), self.events, self.related)
        self.assertEqual(result['items'][0]['evidence'][0]['quote'], self.item['evidence'][0]['quote'])

    def test_quote_from_different_event_is_rejected(self):
        payload = copy.deepcopy(self.payload)
        payload['items'][0]['source_event_ids'] = [11]
        payload['items'][0]['evidence'][0]['event_id'] = 11
        with self.assertRaisesRegex(ValidationError, 'unsupported evidence quote'):
            validate(payload, self.events, self.related)

    def test_related_context_quote_is_not_evidence(self):
        payload = copy.deepcopy(self.payload)
        payload['items'][0]['evidence'][0]['quote'] = self.related[0]['content']
        with self.assertRaisesRegex(ValidationError, 'unsupported evidence quote'):
            validate(payload, self.events, self.related)

    def test_duplicate_key_is_rejected_even_if_each_item_has_valid_evidence(self):
        payload = copy.deepcopy(self.payload)
        payload['items'].append(copy.deepcopy(payload['items'][0]))
        with self.assertRaisesRegex(ValidationError, 'invalid/duplicate stable key'):
            validate(payload, self.events, self.related)

    def test_normalized_or_fabricated_quote_is_rejected(self):
        for quote in ('spaces exactly.\nบรรทัดใหม่', 'spaces  exactly. บรรทัดใหม่', 'Invented evidence.'):
            payload = copy.deepcopy(self.payload)
            payload['items'][0]['evidence'][0]['quote'] = quote
            with self.subTest(quote=quote), self.assertRaisesRegex(ValidationError, 'unsupported evidence quote'):
                validate(payload, self.events, self.related)

    def test_quote_outside_batch_is_rejected(self):
        payload = copy.deepcopy(self.payload)
        payload['items'][0]['source_event_ids'] = [12]
        payload['items'][0]['evidence'][0]['event_id'] = 12
        with self.assertRaisesRegex(ValidationError, 'unsupported evidence ids'):
            validate(payload, self.events, self.related)


if __name__ == '__main__':
    unittest.main()
