#!/usr/bin/env python3
# gsd-path project runtime
"""Collect Claude Code usage without participating in a landing transaction.

Only numeric usage and provenance enter the local Git-common ledger. Transcript
text is never copied. Explicit windows and session identities scope discovery;
the path encoding is merely a lookup hint, never an attribution key.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
if __package__:
    from . import _common
else:
    import _common

SCHEMA = 'gsd-path/usage-ledger/v1'
COUNTERS = ('input', 'cache_write_5m', 'cache_write_1h', 'cache_write_unknown', 'cache_read', 'output')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def instant(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('usage timestamps require a timezone')
    return result.astimezone(timezone.utc)


def safe(path):
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError('usage paths must not contain symlinks')
    if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise ValueError('usage data must be regular unlinked files')
    return path


def git(repo, *args):
    result = _common.run_git(repo, *args)
    if result.returncode:
        raise ValueError(result.stderr.strip() or 'cannot determine usage repository identity')
    return result.stdout.strip()


def location(repo):
    # Read the bound branch, including after shipment from main. It is stable
    # across retries and distinguishes repeated milestone slugs.
    text = (repo / '.project/STATE.md').read_text(encoding='utf-8')
    found = re.search(r'(?m)^branch: ([a-z0-9_./-]+)\s*$', text)
    if found is None:
        raise ValueError('usage requires a milestone bound branch')
    key = found.group(1).replace('/', '-')
    common = Path(git(repo, 'rev-parse', '--path-format=absolute', '--git-common-dir'))
    root = common / 'gsd-path/usage'
    return root / (key + '.config.json'), root / (key + '.jsonl')


def roots_for(repo):
    roots = {str(repo.resolve())}
    member_file = repo / '.project/MEMBERS.md'
    if member_file.exists():
        roots.update(re.findall(r'(?m)^Checkout: (.+)$', member_file.read_text(encoding='utf-8')))
    for root in list(roots):
        result = _common.run_git(Path(root), 'worktree', 'list', '--porcelain')
        if result.returncode == 0:
            roots.update(re.findall(r'(?m)^worktree (.+)$', result.stdout))
    return sorted(roots)


def configure(repo, since=None, until=None, roots=(), sessions=(), projects=None):
    config, ledger = location(repo)
    if since is None and config.exists():
        previous = json.loads(safe(config).read_text(encoding='utf-8'))
        since = previous['since']
        until = previous['until'] if until is None else until
        sessions = sessions or previous['sessions']
        projects = projects or Path(previous['projects'])
    since = since or now()
    if until and instant(until) < instant(since):
        raise ValueError('usage cutoff precedes the start')
    instant(since)
    value = {'schema': SCHEMA, 'since': since, 'until': until,
             'roots': sorted(set(roots_for(repo)) | {str(Path(p).resolve()) for p in roots}),
             'sessions': sorted(set(sessions)),
             'projects': str((projects or Path.home() / '.claude/projects').resolve())}
    safe(config); safe(ledger)
    config.parent.mkdir(parents=True, exist_ok=True)
    safe(ledger.with_suffix('.lock'))
    with _common.exclusive_lock(ledger.with_suffix('.lock'), timeout=5):
        if config.exists():
            previous = json.loads(config.read_text(encoding='utf-8'))
            if any(previous.get(k) != value[k] for k in ('since', 'until', 'sessions', 'projects')):
                raise ValueError('usage window already configured; reuse its original scope')
            value['roots'] = sorted(set(previous['roots']) | set(value['roots']))
            value = dict(previous, **value)
        _common.atomic_write(config, json.dumps(value, indent=2) + '\n')
    return {'status': 'configured', 'config': str(config), 'ledger': str(ledger), 'window': [since, until]}


def count(value):
    if type(value) is not int or value < 0:
        raise ValueError('usage counters must be non-negative integers')
    return value


def usage_counts(usage):
    if not isinstance(usage, dict) or not {'input_tokens', 'output_tokens'} <= set(usage):
        raise ValueError('unsupported assistant usage shape')
    created = count(usage.get('cache_creation_input_tokens', 0))
    split = usage.get('cache_creation') or {}
    five = count(split.get('ephemeral_5m_input_tokens', 0))
    hour = count(split.get('ephemeral_1h_input_tokens', 0))
    if five + hour > created:
        raise ValueError('cache creation split exceeds the aggregate')
    return dict(zip(COUNTERS, (count(usage['input_tokens']), five, hour, created - five - hour,
                               count(usage.get('cache_read_input_tokens', 0)), count(usage['output_tokens']))))


def classify(logical):
    if logical.startswith('build_'):
        return 'build'
    for phase in ('review', 'research', 'plan', 'inspect'):
        if logical.startswith(phase + '_'):
            return phase
    return 'orchestration' if logical == 'orchestration' else 'unattributed'


def read_transcript(path, config, cutoff):
    safe(path)
    metadata = path.with_suffix('.meta.json')
    meta = json.loads(safe(metadata).read_text(encoding='utf-8')) if metadata.exists() else {}
    subagent = path.parent.name == 'subagents'
    logical = meta.get('description') if subagent else 'orchestration'
    logical = logical if isinstance(logical, str) and re.fullmatch(r'[A-Za-z0-9_.-]+', logical) else 'unattributed'
    # A running child can expose provisional usage. Keep it explicitly partial.
    incomplete = subagent and meta.get('finished') is not True
    last_stop = None
    rows, errors, cwd = [], [], None
    lower, upper = instant(config['since']), instant(cutoff)
    # Claude Code writes U+2028, U+2029 and U+0085 raw inside JSON strings; only LF ends a record.
    for number, line in enumerate(path.read_text(encoding='utf-8').split('\n'), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError('transcript event is not an object')
            cwd = event.get('cwd') or cwd
            message = event.get('message')
            if event.get('type') != 'assistant' or not isinstance(message, dict):
                continue
            last_stop = message.get('stop_reason')
            timestamp = event.get('timestamp')
            if not timestamp or not lower <= instant(timestamp) <= upper:
                continue
            session = event.get('sessionId') or (path.parent.parent.name if subagent else path.stem)
            if config['sessions'] and session not in config['sessions']:
                continue
            if not cwd or not any(Path(cwd) == Path(root) or Path(root) in Path(cwd).parents for root in config['roots']):
                continue
            counts = usage_counts(message.get('usage'))
            if message.get('model') == '<synthetic>' and not any(counts.values()):
                continue
            version, model, identifier = event.get('version'), message.get('model'), message.get('id')
            if (not isinstance(version, str) or not re.fullmatch(r'2\.\d+\.\d+(?:[-+].*)?', version)
                    or not isinstance(model, str) or not model or not isinstance(identifier, str) or not identifier):
                raise ValueError('unsupported Claude Code version or missing message/model identity')
            agent = event.get('agentId') or (path.stem.removeprefix('agent-') if subagent else None)
            rows.append({'schema': SCHEMA, 'kind': 'message', 'message_id': identifier, 'ts': timestamp,
                         'model': model, 'tokens': counts, 'prompt_size': sum(counts[k] for k in COUNTERS if k != 'output'),
                         'session_id': session, 'agent_id': agent, 'logical_name': logical, 'phase': classify(logical),
                         'attempt_id': agent or session, 'claude_version': version, 'source': str(path),
                         'final_usage': bool(message.get('stop_reason')),
                         'attribution': 'metadata' if subagent and logical != 'unattributed' else logical})
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            errors.append({'line': number, 'reason': str(error)})
    if incomplete and rows and last_stop != 'end_turn':
        errors.append({'reason': 'subagent transcript is not marked finished'})
    return rows, errors


def ledger_rows(path):
    if not safe(path).exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def collect(repo, transcripts=(), unobserved_attempt=None):
    config_path, ledger = location(repo)
    if not safe(config_path).exists():
        return {'status': 'not-configured', 'reason': 'configure the milestone collection window first'}
    config = json.loads(config_path.read_text(encoding='utf-8'))
    cutoff = config.get('until') or now()
    config['roots'] = sorted(set(config['roots']) | set(roots_for(repo)))
    paths = {Path(p).resolve() for p in transcripts}
    for root in config['roots']:
        encoded = re.sub(r'[^A-Za-z0-9]', '-', root)
        directory = Path(config['projects']) / encoded
        if directory.is_dir() and not directory.is_symlink():
            paths.update(p for p in directory.rglob('*.jsonl') if p.is_file())
    messages, errors = {}, []
    if not paths:
        errors.append({'reason': 'no scoped Claude Code transcripts found'})
    for path in sorted(paths):
        try:
            rows, issues = read_transcript(path, config, cutoff)
            errors.extend(dict(issue, source=str(path)) for issue in issues)
            for row in rows:
                key = row['message_id']
                previous = messages.get(key)
                if previous is not None:
                    old, new = previous['tokens'], row['tokens']
                    if previous['model'] != row['model']:
                        errors.append({'reason': 'conflicting models for the same message', 'message_id': key})
                        continue
                    if previous.get('final_usage') and not row.get('final_usage'):
                        continue
                    # A completed stream corrects provisional input accounting
                    # when cache hits are reported at message_stop.
                    finalized = row.get('final_usage') and not previous.get('final_usage')
                    if not finalized and not all(new[k] >= old[k] for k in COUNTERS):
                        if all(old[k] >= new[k] for k in COUNTERS):
                            continue
                        errors.append({'reason': 'conflicting partial usage for the same message', 'message_id': key})
                        continue
                    if old == new and previous['attribution'] == 'metadata' and row['attribution'] != 'metadata':
                        continue
                messages[key] = row
        except (ValueError, OSError, UnicodeError) as error:
            errors.append({'source': str(path), 'reason': str(error)})
    if not messages:
        errors.append({'reason': 'no recognized usage messages in the configured window'})
    safe(ledger); safe(ledger.with_suffix('.lock'))
    with _common.exclusive_lock(ledger.with_suffix('.lock'), timeout=5):
        # A CLI child is outside this transcript-only collector's coverage.
        # Persist the exclusion instead of publishing an apparently complete total.
        current = json.loads(config_path.read_text(encoding='utf-8'))
        config['unobserved_attempts'] = sorted(set(current.get('unobserved_attempts', [])) |
            ({unobserved_attempt} if unobserved_attempt else set()))
        errors.extend({'reason': 'CLI child usage is outside transcript collection', 'attempt': name}
                      for name in config['unobserved_attempts'])
        previous = ledger_rows(ledger)
        old = {row['message_id']: row for row in previous if row.get('kind') == 'message'}
        for key, row in old.items():
            if key not in messages:
                messages[key] = row  # retained even after host transcript cleanup
            elif messages[key]['model'] != row['model']:
                errors.append({'reason': 'recorded model identity cannot change', 'message_id': key})
                messages[key] = row
            elif row.get('final_usage') and not messages[key].get('final_usage'):
                messages[key] = row
            elif not (messages[key].get('final_usage') and not row.get('final_usage')) and not all(messages[key]['tokens'][k] >= row['tokens'][k] for k in COUNTERS):
                errors.append({'reason': 'recorded usage cannot decrease', 'message_id': key})
                messages[key] = row
        receipt = {'schema': SCHEMA, 'kind': 'collection', 'status': 'incomplete' if errors else 'complete',
                   'since': config['since'], 'cutoff': cutoff, 'files': len(paths), 'errors': errors}
        failures = [dict(issue, schema=SCHEMA, kind='collection_failed') for issue in errors]
        output = sorted(messages.values(), key=lambda row: (row['ts'], row['message_id'])) + failures + [receipt]
        _common.atomic_write(ledger, ''.join(json.dumps(row, sort_keys=True) + '\n' for row in output))
        _common.atomic_write(config_path, json.dumps(config, indent=2) + '\n')
    return {'status': receipt['status'], 'messages': len(messages), 'issues': len(errors), 'ledger': str(ledger)}


def observe(repo, **context):
    """Best-effort callback outside strict landing/artifact transactions."""
    try:
        return collect(repo, context.get('transcripts', ()), context.get('unobserved_attempt'))
    except (OSError, ValueError, RuntimeError) as error:
        return {'status': 'incomplete', 'reason': str(error)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'collect', 'status'))
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--since'); parser.add_argument('--until')
    parser.add_argument('--root', action='append', default=[])
    parser.add_argument('--session', action='append', default=[])
    parser.add_argument('--transcript', action='append', default=[])
    parser.add_argument('--projects', type=Path)
    parser.add_argument('--unobserved-attempt')
    args = parser.parse_args()
    try:
        if args.action == 'start':
            result = configure(args.repo, args.since, args.until, args.root, args.session, args.projects)
        elif args.action == 'collect':
            result = observe(args.repo, transcripts=args.transcript, unobserved_attempt=args.unobserved_attempt)
        else:
            config, ledger = location(args.repo)
            result = {'configured': config.exists(), 'ledger': str(ledger),
                      'messages': sum(r.get('kind') == 'message' for r in ledger_rows(ledger))}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as error:
        print(json.dumps({'status': 'error', 'reason': str(error)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
