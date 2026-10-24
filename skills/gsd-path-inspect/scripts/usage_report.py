#!/usr/bin/env python3
# gsd-path project runtime
"""Reprice recorded usage and produce a portable, transcript-free snapshot."""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
if __package__:
    from . import _common, usage_collect
else:
    import _common, usage_collect

SCHEMA = 'gsd-path/usage-report/v1'
PRICE_SCHEMA = 'gsd-path/usage-prices/v1'
PRICE_FIELDS = ('input', 'cache_write_5m', 'cache_write_1h', 'cache_read', 'output')


def decimal(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError('price must be a finite non-negative number')
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError('price must be a finite non-negative number') from error
    if not result.is_finite() or result < 0:
        raise ValueError('price must be a finite non-negative number')
    return result


def validate_prices(value):
    if not isinstance(value, dict) or value.get('schema') != PRICE_SCHEMA:
        raise ValueError('unsupported usage price schema')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value.get('as_of', '')) or not isinstance(value.get('models'), dict):
        raise ValueError('prices require an as_of date and model map')
    for model in value['models'].values():
        if not isinstance(model, dict) or not isinstance(model.get('name'), str) or not model['name'].strip():
            raise ValueError('price entries require display names')
        for band in (model, *model.get('tiers', [])):
            for key in PRICE_FIELDS:
                if band.get(key) is not None:
                    decimal(band[key])
            if band is not model and (type(band.get('over')) is not int or band['over'] < 0):
                raise ValueError('context tiers require non-negative integer thresholds')
    return value


def message_cost(row, model):
    if model is None:
        return None
    rates = dict(model)
    for tier in sorted(model.get('tiers', []), key=lambda band: band['over']):
        if row['prompt_size'] > tier['over']:
            rates.update(tier)
    tokens = row['tokens']
    if tokens.get('cache_write_unknown', 0):
        return None  # a missing TTL must not silently acquire a five-minute price
    result = Decimal(0)
    for key in PRICE_FIELDS:
        n = usage_collect.count(tokens.get(key, 0))
        if n:
            if rates.get(key) is None:
                return None
            result += Decimal(n) * decimal(rates[key]) / Decimal(1_000_000)
    return result


def bucket(name):
    return {'name': name, 'messages': 0, 'tokens': {key: 0 for key in usage_collect.COUNTERS},
            'priced_usd': Decimal(0), 'unpriced_messages': 0, 'attempts': set()}


def add(target, row, cost):
    target['messages'] += 1
    for key in usage_collect.COUNTERS:
        target['tokens'][key] += usage_collect.count(row['tokens'].get(key, 0))
    target['attempts'].add(row['attempt_id'])
    if cost is None:
        target['unpriced_messages'] += 1
    else:
        target['priced_usd'] += cost


def finish(value):
    return dict(value, attempts=len(value['attempts']), priced_usd=str(value['priced_usd']),
                cost_usd=None if value['unpriced_messages'] else str(value['priced_usd']))


def pricing(prices=None, aliases=None):
    if prices is None:
        if __package__:
            from . import usage_prices
        else:
            import usage_prices
        raw_prices = usage_prices.DATA.encode('utf-8')
    else:
        raw_prices = usage_collect.safe(prices).read_bytes()
    catalog = validate_prices(json.loads(raw_prices))
    alias_file = aliases or Path.home() / '.gsd-path/usage-model-aliases.json'
    mapping = json.loads(usage_collect.safe(alias_file).read_text(encoding='utf-8')) if alias_file.exists() else {}
    if not isinstance(mapping, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in mapping.items()):
        raise ValueError('usage model aliases must map model ids to canonical model ids')
    return raw_prices, catalog, mapping


def unpriced(repo, prices=None, aliases=None):
    """Name recorded raw model ids without a catalog price, for local use only."""
    _, catalog, mapping = pricing(prices, aliases)
    _, ledger = usage_collect.location(repo)
    return sorted({row['model'] for row in usage_collect.ledger_rows(ledger) if row.get('kind') == 'message'
                   and mapping.get(row['model'], row['model']) not in catalog['models']})


def report(repo, prices=None, aliases=None):
    _, ledger = usage_collect.location(repo)
    rows = usage_collect.ledger_rows(ledger)
    receipts = [row for row in rows if row.get('kind') == 'collection']
    messages = [row for row in rows if row.get('kind') == 'message']
    if not receipts:
        raise ValueError('usage has no collection receipt; collect before reporting')
    raw_prices, catalog, mapping = pricing(prices, aliases)
    total, models, tasks, phases = bucket('Total'), {}, {}, {}
    for row in messages:
        model_id = mapping.get(row['model'], row['model'])
        model = catalog['models'].get(model_id)
        # Public reports do not disclose personal routing aliases or transcript paths.
        identity = model_id if model else 'unpriced-' + hashlib.sha256(model_id.encode()).hexdigest()[:12]
        display = model['name'] if model else 'Sem preço / Unpriced (' + identity[-12:] + ')'
        cost = message_cost(row, model)
        logical, phase = row.get('logical_name', 'unattributed'), row.get('phase', 'unattributed')
        for target in (total, models.setdefault(identity, bucket(display)),
                       tasks.setdefault(logical, bucket(logical)), phases.setdefault(phase, bucket(phase))):
            add(target, row, cost)
    receipt = receipts[-1]
    value = {'schema': SCHEMA, 'host': 'claude-code', 'coverage': receipt['status'],
             'collection_issues': len(receipt.get('errors', [])),
             'window': {'since': receipt['since'], 'cutoff': receipt['cutoff']},
             'scope': 'milestone', 'total': finish(total),
             'models': [dict(finish(row), id=key) for key, row in sorted(models.items())],
             'tasks': [finish(row) for _, row in sorted(tasks.items())],
             'phases': [finish(row) for _, row in sorted(phases.items())],
             'prices': {key: catalog.get(key) for key in ('as_of', 'fetched_at', 'etag', 'source')},
             'prices_sha256': hashlib.sha256(raw_prices).hexdigest(),
             'ledger_sha256': hashlib.sha256(ledger.read_bytes()).hexdigest()}
    if not messages:
        value['total']['cost_usd'] = None
    return validate_snapshot(value)


def validate_snapshot(value):
    try:
        return _validate_snapshot(value)
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError('malformed usage snapshot') from error


def _validate_snapshot(value):
    if not isinstance(value, dict) or value.get('schema') != SCHEMA or value.get('scope') != 'milestone':
        raise ValueError('unsupported usage snapshot')
    if value.get('coverage') not in ('complete', 'incomplete'):
        raise ValueError('usage snapshot requires explicit coverage')
    if value['prices'].get('source') != 'https://models.dev/api.json':
        raise ValueError('usage snapshot requires the recorded public price source')
    for key in ('since', 'cutoff'):
        usage_collect.instant(value['window'][key])
    if usage_collect.instant(value['window']['since']) > usage_collect.instant(value['window']['cutoff']):
        raise ValueError('usage snapshot window is reversed')
    total = value['total']
    for row in (total, *value['models'], *value['tasks'], *value['phases']):
        for key in ('messages', 'unpriced_messages', 'attempts'):
            usage_collect.count(row[key])
        for n in row['tokens'].values():
            usage_collect.count(n)
        decimal(row['priced_usd'])
        if not isinstance(row['name'], str) or '\n' in row['name'] or '\r' in row['name']:
            raise ValueError('usage display names must be single-line text')
        if row['cost_usd'] is not None:
            if row['unpriced_messages'] or decimal(row['cost_usd']) != decimal(row['priced_usd']):
                raise ValueError('usage snapshot cost contradicts its pricing coverage')
    for collection in ('models', 'tasks', 'phases'):
        for key in usage_collect.COUNTERS:
            if sum(row['tokens'].get(key, 0) for row in value[collection]) != total['tokens'][key]:
                raise ValueError('usage snapshot token totals do not match')
        if sum(row['messages'] for row in value[collection]) != total['messages']:
            raise ValueError('usage snapshot message totals do not match')
        if sum(row['unpriced_messages'] for row in value[collection]) != total['unpriced_messages']:
            raise ValueError('usage snapshot unpriced totals do not match')
        if sum(decimal(row['priced_usd']) for row in value[collection]) != decimal(total['priced_usd']):
            raise ValueError('usage snapshot price totals do not match')
    return value


def number(value, language):
    return f'{value:,}'.replace(',', '.') if language == 'pt-BR' else f'{value:,}'


def money(value, language):
    text = format(decimal(value).quantize(Decimal('.01'), rounding=ROUND_HALF_UP), '.2f')
    return text.replace('.', ',') if language == 'pt-BR' else text


def headings(language):
    return ('Consumo de tokens e custo', 'Consumo de tokens e custo por modelo') if language == 'pt-BR' else ('Token usage and cost', 'Token usage and cost by model')


def summary(value, language='en'):
    total = value['total']
    tokens = number(total['tokens']['output'], language)
    price = money(total['priced_usd'], language)
    no_priced_messages = total['unpriced_messages'] == total['messages']
    if language == 'pt-BR':
        cost = 'US$ ' + price + (' no subtotal com preço' if total['cost_usd'] is None else ' a preço de lista da API')
        if no_priced_messages:
            cost = 'custo indisponível'
        result = tokens + ' tokens de saída; ' + cost + ' (custo equivalente, não fatura).'
        if total['cost_usd'] is None:
            result += ' Há mensagens sem preço.'
        if value['coverage'] != 'complete':
            result += ' Coleta incompleta.'
    else:
        cost = 'US$ ' + price + (' priced subtotal' if total['cost_usd'] is None else ' at API list prices')
        if no_priced_messages:
            cost = 'cost unavailable'
        result = tokens + ' output tokens; ' + cost + ' (equivalent cost, not an invoice).'
        if total['cost_usd'] is None:
            result += ' Some messages are unpriced.'
        if value['coverage'] != 'complete':
            result += ' Collection incomplete.'
    return result


def markdown(value, language='en'):
    validate_snapshot(value)
    pt = language == 'pt-BR'
    columns = ('Modelo', 'Mensagens', 'Entrada', 'Escrita de cache', 'Leitura de cache', 'Saída', 'US$') if pt else ('Model', 'Messages', 'Input', 'Cache writes', 'Cache reads', 'Output', 'US$')
    lines = ['## ' + headings(language)[1], '', '| ' + ' | '.join(columns) + ' |', '|---|---:|---:|---:|---:|---:|---:|']
    for row in [*sorted(value['models'], key=lambda r: -decimal(r['priced_usd'])), value['total']]:
        counts = row['tokens']
        cells = [row['name'].replace('|', '\\|').replace('\n', ' '),
                 *[number(n, language) for n in (row['messages'], counts['input'],
                     counts['cache_write_5m'] + counts['cache_write_1h'] + counts.get('cache_write_unknown', 0),
                     counts['cache_read'], counts['output'])],
                 money(row['cost_usd'], language) if row['cost_usd'] is not None else ('sem preço' if pt else 'unpriced')]
        lines.append('| ' + ' | '.join(cells) + ' |')
    note = ('Valores a preço de lista da API (custo equivalente); não refletem a fatura.' if pt else 'API list-price equivalents; these values do not represent an invoice.')
    lines += ['', note + ' ' + ('Preços' if pt else 'Prices') + ': ' + str(value['prices']['source']) + ', ' + str(value['prices']['as_of']) + '.',
              ('Escopo: milestone; coleta Claude Code. Janela: ' if pt else 'Scope: milestone; Claude Code collection. Window: ') + value['window']['since'] + ' — ' + value['window']['cutoff'] + '.',
              'Os totais somam as mensagens observadas na janela.' if pt else 'Totals sum the messages observed within this window.']
    if value['coverage'] != 'complete' or value['total']['cost_usd'] is None:
        lines += ['', summary(value, language)]
    return '\n'.join(lines) + '\n'


def attach(repo, archive, member, value, adopt_thread=None):
    if __package__:
        from . import pr_editorial
    else:
        import pr_editorial
    path = pr_editorial.publication_path(repo, archive, member)
    # Attach only before the ship commit. Archived contracts remain read-only.
    if _common.run_git(repo, 'cat-file', '-e', 'HEAD:' + path.relative_to(repo).as_posix()).returncode == 0:
        raise ValueError('committed publication contracts are immutable')
    document = pr_editorial.load(repo, archive, member)
    document['usage'] = validate_snapshot(value)
    if adopt_thread is not None:
        if type(adopt_thread) is not int or adopt_thread < 1:
            raise ValueError('adopted usage thread must be positive')
        document['usage_thread'] = adopt_thread
    _common.atomic_write(path, json.dumps(document, ensure_ascii=False, indent=2) + '\n')
    return {'status': 'attached', 'path': str(path), 'coverage': value['coverage']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--prices', type=Path); parser.add_argument('--aliases', type=Path)
    parser.add_argument('--snapshot', type=Path, help='read a portable report or publication without the local ledger')
    parser.add_argument('--language', choices=('en', 'pt-BR'), default='en')
    parser.add_argument('--format', choices=('json', 'markdown', 'summary'), default='json')
    parser.add_argument('--attach', action='store_true'); parser.add_argument('--archive')
    parser.add_argument('--member', default='coordinator')
    parser.add_argument('--adopt-thread', type=int)
    args = parser.parse_args()
    if args.attach and args.format != 'json':
        parser.error('--attach returns a JSON status; render the saved snapshot separately')
    try:
        if args.snapshot:
            stored = json.loads(usage_collect.safe(args.snapshot).read_text(encoding='utf-8'))
            value = validate_snapshot(stored.get('usage', stored))
        else:
            value = report(args.repo, args.prices, args.aliases)
        if args.attach:
            if not args.archive:
                parser.error('--attach requires --archive')
            value = attach(args.repo.resolve(), args.archive, args.member, value, args.adopt_thread)
        print(json.dumps(value, ensure_ascii=False, indent=2) if args.format == 'json' else
              markdown(value, args.language) if args.format == 'markdown' else summary(value, args.language))
        return 0
    except (ValueError, OSError, KeyError) as error:
        print(json.dumps({'status': 'error', 'reason': str(error)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
