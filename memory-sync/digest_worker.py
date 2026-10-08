"""Direct authenticated model boundary; no tool/agent loop or stored credentials.

Run with the resolver's Python environment. Install its agent package normally,
or provide --resolver-root / HERMES_AGENT_ROOT outside this repository.
"""
import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys

from bounded_protocol import normalize_usage
from memory_sync import canonical, load_packet


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model', default='gpt-6-luna')
    p.add_argument('--resolver-root', default=os.environ.get('HERMES_AGENT_ROOT'))
    args = p.parse_args(argv)
    try:
        request = load_packet(sys.stdin.read())
        # Requests are built by the bounded protocol. Disallow tools/loop options.
        if not isinstance(request, dict) or set(request) != {'instructions','input','store','reasoning','text'} or request['store'] is not False:
            raise ValueError('invalid bounded request')
        if args.resolver_root:
            root = Path(args.resolver_root).expanduser().resolve(strict=True)
            sys.path.insert(0, str(root))
        with redirect_stdout(io.StringIO()):
            from agent.auxiliary_client import resolve_provider_client
            from agent.codex_runtime import _consume_codex_event_stream
            client, chosen = resolve_provider_client('openai-codex', model=args.model, raw_codex=True)
            if client is None or chosen != args.model:
                raise ValueError('requested authenticated model unavailable')
            client = client.with_options(max_retries=0, timeout=90)
            try:
                stream = client.responses.create(model=chosen, stream=True, **request)
                try:
                    final = _consume_codex_event_stream(stream, model=chosen)
                finally:
                    stream.close()
            finally:
                client.close()
            if final is None:
                raise ValueError('missing final response')
            text = ''.join(part.text for out in (getattr(final, 'output', None) or [])
                           if getattr(out, 'type', None) == 'message' for part in out.content
                           if getattr(part, 'type', None) == 'output_text')
            usage = final.usage.model_dump() if getattr(final, 'usage', None) else None
            result = dict(payload=load_packet(text), usage=normalize_usage(usage),
                          provider='openai-codex', model=getattr(final, 'model', None) or chosen, api_calls=1)
        print(canonical(result))
        return 0
    except Exception as exc:
        # Never echo SDK errors: diagnostics may contain request/authentication.
        print(canonical(dict(error_class=type(exc).__name__)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
