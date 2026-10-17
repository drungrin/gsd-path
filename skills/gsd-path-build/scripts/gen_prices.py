#!/usr/bin/env python3
# gsd-path project runtime
"""Explicitly refresh the offline list-price snapshot from models.dev."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.dont_write_bytecode = True
if __package__:
    from . import _common, usage_report
else:
    import _common, usage_report

SOURCE = 'https://models.dev/api.json'
PROVIDERS = {'claude-': 'anthropic', 'gpt-': 'openai', 'muse-': 'meta', 'glm-': 'zai'}


def convert(catalog, as_of, fetched_at, etag=None, selected=()):
    models = {}
    for prefix, provider in PROVIDERS.items():
        for identifier, model in catalog.get(provider, {}).get('models', {}).items():
            if not identifier.startswith(prefix) or (selected and identifier not in selected):
                continue
            cost = model.get('cost') or {}
            def rates(value):
                result = {key: value.get(key) for key in ('input', 'output', 'cache_read')}
                result['cache_write_5m'] = value.get('cache_write')
                result['cache_write_1h'] = float(usage_report.decimal(value['input']) * 2) if provider == 'anthropic' and value.get('input') is not None else value.get('cache_write')
                return result
            result = dict(rates(cost), provider=provider, name=model['name'], tiers=[])
            for band in cost.get('tiers', []):
                tier = band.get('tier', {})
                if tier.get('type') != 'context':
                    raise ValueError('unsupported price tier type for ' + identifier)
                size = tier.get('size')
                if type(size) is not int or size < 0:
                    raise ValueError('invalid context threshold for ' + identifier)
                result['tiers'].append(dict(rates(dict(cost, **band)), over=size))
            # context_over_200k is legacy shorthand; authoritative tiers define
            # their own threshold, which need not be 200k.
            models[identifier] = result
    if selected and set(selected) != set(models):
        raise ValueError('selected primary-provider models are missing from the catalog')
    if not models:
        raise ValueError('no supported primary-provider models in the catalog')
    value = {'schema': usage_report.PRICE_SCHEMA, 'as_of': as_of, 'fetched_at': fetched_at,
             'etag': etag, 'source': SOURCE, 'license': 'MIT', 'models': models}
    return usage_report.validate_prices(value)


def changes(previous, current, limit='.25'):
    warnings = []
    ratio = usage_report.decimal(limit)
    for key, old in previous.get('models', {}).items():
        new = current['models'].get(key)
        if new is None:
            warnings.append(key + ': removed')
            continue
        for field in (*usage_report.PRICE_FIELDS, 'tiers'):
            if field == 'tiers':
                if old.get(field) != new.get(field):
                    warnings.append(key + ': context tiers changed')
            elif old.get(field) is not None:
                a, b = usage_report.decimal(old[field]), new.get(field)
                if b is None or abs(usage_report.decimal(b) - a) > a * ratio:
                    warnings.append(key + ': ' + field + ' changed significantly')
    return warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, help='convert a previously downloaded catalog offline')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--fetched-at'); parser.add_argument('--etag')
    parser.add_argument('--model', action='append', default=[])
    parser.add_argument('--accept-change', action='store_true')
    args = parser.parse_args()
    try:
        previous = {}
        if args.output.exists():
            prior = args.output.read_text(encoding='utf-8')
            if args.output.suffix == '.py':
                tree = ast.parse(prior)
                data = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                            and any(isinstance(target, ast.Name) and target.id == 'DATA' for target in node.targets))
                prior = ast.literal_eval(data)
            previous = json.loads(prior)
        fetched = args.fetched_at or datetime.now(timezone.utc).isoformat(timespec='seconds')
        etag = args.etag
        if args.catalog:
            catalog = json.loads(args.catalog.read_text(encoding='utf-8'))
        else:
            headers = {'If-None-Match': previous['etag']} if previous.get('etag') else {}
            try:
                with urlopen(Request(SOURCE, headers=headers), timeout=30) as response:
                    raw = response.read(20_000_001)
                    if len(raw) > 20_000_000:
                        raise ValueError('price catalog exceeds the download size limit')
                    catalog, etag = json.loads(raw), response.headers.get('ETag')
            except HTTPError as error:
                if error.code != 304:
                    raise
                error.close()
                print(json.dumps({'status': 'unchanged', 'as_of': previous['as_of']}))
                return 0
        value = convert(catalog, args.as_of, fetched, etag, args.model)
        warnings = changes(previous, value)
        if warnings and not args.accept_change:
            print(json.dumps({'status': 'review-required', 'changes': warnings}))
            return 1
        args.output.parent.mkdir(parents=True, exist_ok=True)
        output = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
        if args.output.suffix == '.py':
            output = '#!/usr/bin/env python3\n# gsd-path project runtime\n"""Offline price snapshot. Generated explicitly by gen_prices.py."""\nDATA = ' + repr(output) + '\n'
        _common.atomic_write(args.output, output)
        print(json.dumps({'status': 'written', 'models': len(value['models']), 'changes': warnings}))
        return 0
    except (ValueError, OSError, KeyError) as error:
        print(json.dumps({'status': 'error', 'reason': str(error)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
