#!/usr/bin/env python3
"""One credential-safe, minimal health request. Never retries or starts jobs."""
import argparse
import hashlib
import io
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from trace_processor_v2 import extract_output_text, provider_config


PROBE_VERSION = 'minimal-health-v2-safe-error-identifiers'
# Only public protocol identifiers may leave the error body. Arbitrary server
# strings (even under a "code" key) could contain credentials or echoed input.
SAFE_ERROR_IDENTIFIERS = frozenset({
    'continuation_limit_exceeded', 'invalid_request_error', 'rate_limit_exceeded',
    'insufficient_quota', 'authentication_error', 'permission_denied',
    'model_not_found', 'context_length_exceeded', 'server_error',
    'service_unavailable', 'invalid_api_key', 'unsupported_parameter',
    'invalid_parameter', 'request_timeout', 'internal_error', 'overloaded_error',
    'not_found_error', 'permission_error', 'api_error', 'bad_request_error',
})


def safe_error_metadata(response):
    """Bounded local inspection; never retain error text, headers or credentials."""
    limit = 65536
    try:
        raw = response.read(limit + 1)
    except (OSError, ValueError, io.UnsupportedOperation):
        return {'error_body_inspection': 'read_failed'}
    metadata = {'error_body_inspection': 'bounded_json_identifiers_only',
                'error_body_truncated': len(raw) > limit}
    if len(raw) > limit:
        return metadata
    metadata['error_body_sha256'] = hashlib.sha256(raw).hexdigest()
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError):
        metadata['error_body_inspection'] = 'non_json'
        return metadata
    error = body.get('error') if isinstance(body, dict) else None
    if not isinstance(error, dict):
        metadata['error_body_inspection'] = 'missing_error_object'
        return metadata
    for source, target in [('code', 'provider_error_code'), ('type', 'provider_error_type')]:
        value = error.get(source)
        if isinstance(value, str) and value in SAFE_ERROR_IDENTIFIERS:
            metadata[target] = value
        elif value is not None:
            metadata[target + '_omitted'] = True
    return metadata


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=60)
    parser.add_argument('--max-output-tokens', type=int, default=256)
    args = parser.parse_args()
    base, key = provider_config(args.env_file)
    if not base or not key:
        raise SystemExit('Missing provider configuration; no request sent')
    url = base.rstrip('/')
    if not url.endswith('/responses'):
        url += '/responses'
    ca_file = os.environ.get('SSL_CERT_FILE')
    if not ca_file:
        try:
            import certifi
            ca_file = certifi.where()
        except ImportError:
            pass
    request = urllib.request.Request(
        url, method='POST',
        data=json.dumps({'model': args.model, 'input': 'Reply only OK.',
                         'max_output_tokens': args.max_output_tokens, 'temperature': 0}).encode(),
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    # Store only minimal status metadata, never credentials or error-body text.
    result = {'timestamp': datetime.now(timezone.utc).isoformat(),
              'probe_version': PROBE_VERSION,
              'requested_model': args.model, 'healthy': False,
              'max_output_tokens': args.max_output_tokens}
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=args.timeout,
                                    context=ssl.create_default_context(cafile=ca_file)) as response:
            body = json.load(response)
            result['http_status'] = response.status
        text = extract_output_text(body).strip()
        result.update(healthy=result['http_status'] == 200 and bool(text),
                      nonempty_output=bool(text), returned_model=body.get('model'),
                      usage=body.get('usage'))
        if not text:
            tokens = (body.get('usage') or {}).get('output_tokens', 0)
            result['health_classification'] = (
                'inconclusive_output_budget' if tokens >= args.max_output_tokens
                else 'inconclusive_empty_output')
    except urllib.error.HTTPError as exc:
        result.update(http_status=exc.code, error_type='HTTPError')
        result.update(safe_error_metadata(exc))
    except (OSError, ValueError) as exc:
        result['error_type'] = type(exc).__name__
    result['elapsed_seconds'] = round(time.monotonic() - started, 3)
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result['healthy'] else 1)


if __name__ == '__main__':
    main()
