#!/usr/bin/env python3
"""One bounded Chat health request, separate from Responses health evidence.

No experiment starts, tool changes, credential changes, annotations or retries.
"""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from probe_trace_processor_provider import safe_error_metadata
from trace_processor_v2 import provider_config

ENDPOINT = 'https://soclaas-api.comp.nus.edu.sg/v1/chat/completions'
VERSION = 'soclaas-chat-health-v1'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def payload(model):
    return {'model': model, 'messages': [{'role': 'user', 'content': 'Reply only OK.'}],
            'max_tokens': 256, 'temperature': 0, 'stream': False}


def classify(body):
    choices = body.get('choices') or []
    choice = choices[0] if len(choices) == 1 and isinstance(choices[0], dict) else {}
    content = (choice.get('message') or {}).get('content')
    visible = isinstance(content, str) and bool(content.strip())
    usage = body.get('usage') or {}
    return {'nonempty_output': visible, 'returned_model': body.get('model'),
            'finish_reason': choice.get('finish_reason'), 'usage': usage,
            'healthy': visible and choice.get('finish_reason') == 'stop',
            'tool_calling_validated': False, 'full_agent_recording_validated': False}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--env-file', type=Path, required=True)
    ap.add_argument('--model', required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--timeout', type=int, default=45, choices=range(1, 46))
    args = ap.parse_args()
    project = Path(__file__).resolve().parents[1]
    local = project / '.complete-trace-reruns'
    if not Path(__file__).resolve().parents[1].is_dir():
        raise SystemExit('External drive not mounted; no request sent')
    with (local / 'runner.lock').open('r') as own, (project / '.trace-processor-v2/job.lock').open('r') as shared:
        fcntl.flock(own, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(shared, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (local / 'pause.requested').exists():
            raise SystemExit('User pause marker present; no request sent')
        base, key = provider_config(args.env_file)
        if base.rstrip('/').removesuffix('/responses').removesuffix('/chat/completions') != ENDPOINT.removesuffix('/chat/completions') or not key:
            raise SystemExit('Unexpected provider or missing credential; no request sent')
        import certifi
        opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(
            context=ssl.create_default_context(cafile=certifi.where())))
        raw = json.dumps(payload(args.model), ensure_ascii=False).encode()
        request = urllib.request.Request(ENDPOINT, data=raw, method='POST', headers={
            'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        result = {'timestamp': datetime.now(timezone.utc).isoformat(),
                  'probe_version': VERSION, 'endpoint': ENDPOINT, 'wire_protocol': 'chat',
                  'requested_model': args.model, 'request_sha256': hashlib.sha256(raw).hexdigest(),
                  'max_output_tokens': 256, 'attempts': 1, 'automatic_retries': 0,
                  'contains_trace_data': False, 'healthy': False}
        started = time.monotonic()
        try:
            with opener.open(request, timeout=args.timeout) as response:
                body_raw = response.read(1024 * 1024 + 1)
                result['http_status'] = response.status
            if len(body_raw) > 1024 * 1024:
                raise ValueError('Oversized response')
            result['response_sha256'] = hashlib.sha256(body_raw).hexdigest()
            body = json.loads(body_raw)
            result.update(classify(body))
            result['healthy'] = result['http_status'] == 200 and result['healthy']
        except urllib.error.HTTPError as exc:
            result.update(http_status=exc.code, error_type='HTTPError')
            result.update(safe_error_metadata(exc))
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            result.update(error_type=type(exc).__name__, healthy=False)
        result['elapsed_seconds'] = round(time.monotonic() - started, 3)
        # Keep credentials and arbitrary server strings out of persisted metadata.
        from trace_processor_v2 import redact_credentials
        result = redact_credentials(result, key)
        args.output.mkdir(parents=True, exist_ok=True)
        path = args.output / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'record': str(path), **result}, ensure_ascii=False))
        raise SystemExit(0 if result['healthy'] else 1)


if __name__ == '__main__':
    main()
