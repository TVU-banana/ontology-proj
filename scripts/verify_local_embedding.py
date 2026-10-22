#!/usr/bin/env python3
"""Real local service smoke checks; no cloud calls or project data ingestion."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:11434')
    parser.add_argument('--model', default='qwen3-embedding:0.6b')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    fixture = Path(__file__).resolve().parents[1] / 'deploy/embedding/smoke-cases.json'
    cases = json.loads(fixture.read_text())
    checks, calls = [], []

    def call(path, body=None):
        started = time.perf_counter()
        request = Request(args.base_url.rstrip('/') + path,
                          data=None if body is None else json.dumps(body).encode(),
                          headers={'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=180) as response:
                result = json.load(response)
                calls.append({'path': path, 'status': response.status,
                              'elapsed_seconds': round(time.perf_counter() - started, 6)})
                return result
        except HTTPError as exc:
            calls.append({'path': path, 'status': exc.code,
                          'error': exc.read().decode()[:1000]})
            raise

    def check(name, passed, detail=None):
        checks.append({'name': name, 'passed': bool(passed), 'detail': detail})

    def cosine(a, b):
        return sum(x*y for x, y in zip(a, b)) / math.sqrt(sum(x*x for x in a)*sum(y*y for y in b))

    error = None
    try:
        version = call('/api/version')
        tags = call('/api/tags')
        identity = next(m for m in tags['models'] if m['name'] == args.model)
        show = call('/api/show', {'model': args.model})
        check('embedding_capability', 'embedding' in show.get('capabilities', []))
        body = {'model': args.model, 'input': cases['documents'], 'truncate': False,
                'keep_alive': '5m', 'options': {'num_ctx': 4096}}
        batch = call('/api/embed', body)
        vectors = batch['embeddings']
        check('batch_cardinality', len(vectors) == len(cases['documents']))
        check('dimension_1024', all(len(v) == 1024 for v in vectors))
        check('finite_nonzero_unit_vectors', all(all(math.isfinite(x) for x in v)
              and abs(sum(x*x for x in v)-1) < 1e-4 for v in vectors))
        repeat = call('/api/embed', {**body, 'input': cases['documents'][0]})['embeddings'][0]
        check('repeat_matches_batch', len(repeat) == 1024 and cosine(repeat, vectors[0]) > 0.99999)
        queries = [f"Instruct: {cases['query_instruction']}\nQuery: {q['text']}" for q in cases['queries']]
        query_vectors = call('/api/embed', {**body, 'input': queries})['embeddings']
        rankings = []
        for query, vector in zip(cases['queries'], query_vectors):
            scores = [cosine(vector, doc) for doc in vectors]
            ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
            rankings.append({'query': query['text'], 'expected': query['expected_document_index'],
                             'ranking': ranked, 'scores': scores})
            check('smoke_top1_' + str(len(rankings)), ranked[0] == query['expected_document_index'])
        compatible = call('/v1/embeddings', {'model': args.model, 'input': cases['documents'],
                                            'encoding_format': 'float'})
        data = sorted(compatible['data'], key=lambda item: item['index'])
        check('openai_batch_order_and_dimension', [v['index'] for v in data] == list(range(4))
              and all(len(v['embedding']) == 1024 for v in data))
        check('openai_matches_native', all(cosine(a, b['embedding']) > 0.99999 for a, b in zip(vectors, data)))
        # Negative tests must return an explicit error, not a plausible vector.
        for name, bad_body in [('invalid_input_type_rejected', {**body, 'input': 37}),
                               ('overlong_input_rejected', {**body, 'input': ' 测试文本' * 5000})]:
            try:
                call('/api/embed', bad_body)
                check(name, False, 'unexpected success')
            except HTTPError as exc:
                check(name, 400 <= exc.code < 500, exc.code)
        ps = call('/api/ps')
        result = {'version': version, 'model': identity,
                  'model_details': show.get('details'), 'model_info': show.get('model_info'),
                  'capabilities': show.get('capabilities'), 'loaded_models': ps,
                  'rankings': rankings}
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
        result = {}
    result.update({'base_url': args.base_url, 'fixture_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest(),
                   'checks': checks, 'calls': calls, 'error': error,
                   'passed': error is None and bool(checks) and all(c['passed'] for c in checks),
                   'boundary': 'Service smoke only; not model A/B, S3.2 acceptance, or professional validation.'})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'passed': result['passed'], 'checks': len(checks), 'error': error,
                      'output': str(args.output)}, ensure_ascii=False))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
