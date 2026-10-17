#!/usr/bin/env python3
# gsd-path project runtime
"""Publish one informational usage comment while retaining human PR edits."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile

sys.dont_write_bytecode = True

if __package__:
    from . import _common, azure_repos, usage_collect, usage_report
else:
    import _common, azure_repos, usage_collect, usage_report


def section(document):
    language = document['language']
    title, detail = usage_report.headings(language)
    result = '## ' + title + '\n\n' + usage_report.summary(document['usage'], language)
    result += (' Tabela no comentário “' if language == 'pt-BR' else ' Table in the “') + detail
    result += '”.' if language == 'pt-BR' else '” comment.'
    return result + '\n<!-- usage-summary -->'


def receipt_path(repo, document):
    archive = document['archive'].split('/')
    if (len(archive) != 3 or archive[:2] != ['.project', 'archive']
            or not re.fullmatch(r'\d{3,}-[a-z0-9-]+', archive[-1])
            or not re.fullmatch(r'[a-z][a-z0-9-]*', document['member'])):
        raise ValueError('invalid usage publication identity')
    common = Path(usage_collect.git(repo, 'rev-parse', '--path-format=absolute', '--git-common-dir'))
    return usage_collect.safe(common / 'gsd-path/usage-publication' / archive[-1] / (document['member'] + '.json'))


def previous_summary(repo, document):
    if not document.get('usage'):
        return None
    path = receipt_path(repo, document)
    return json.loads(path.read_text(encoding='utf-8')).get('summary_sha256') if path.exists() else None


def description(body, document, previous_hash=None):
    if not document.get('usage'):
        return body
    language = document['language']
    title, _ = usage_report.headings(language)
    generated = section(document)
    pattern = r'(?m)^## ' + re.escape(title) + r'[ \t]*\n[\s\S]*?(?=^## |\Z)'
    found = re.search(pattern, body)
    if found and '<!-- usage-summary -->' in found.group():
        current = found.group().strip()
        if current != generated.strip() and hashlib.sha256(current.encode()).hexdigest() != previous_hash:
            raise ValueError('usage summary was edited by a person; preserve the edit')
    return re.sub(pattern, lambda _: generated + '\n\n', body).rstrip() + '\n' if found else body.rstrip() + '\n\n' + generated + '\n'


def table(content):
    lines = [line for line in content.splitlines() if line.startswith('|')]
    return [[cell.replace('*', '').strip() for cell in line.strip('|').split('|')] for line in lines[2:]]


def publish(repo, repository, pull, document, azure_call, github_call):
    if not document.get('usage'):
        return {'status': 'not-configured'}
    try:
        path = receipt_path(repo, document)
        lock = usage_collect.safe(path.with_suffix('.lock'))
        with _common.exclusive_lock(lock, timeout=5):
            return _publish(repo, repository, pull, document, azure_call, github_call)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print('usage publication incomplete: ' + str(error), file=sys.stderr)
        return {'status': 'incomplete', 'reason': str(error)}


def _publish(repo, repository, pull, document, azure_call, github_call):
    snapshot = usage_report.validate_snapshot(document['usage'])
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    key = document['archive'].split('/')[-1] + '/' + document['member']
    marker = '<!-- usage-report: ' + key + ' -->'
    content = usage_report.markdown(snapshot, document['language']) + '\n' + marker + '\n'
    receipt = receipt_path(repo, document)
    previous = json.loads(receipt.read_text(encoding='utf-8')) if receipt.exists() else None
    if previous and previous['pull_request'] != pull['html_url']:
        raise ValueError('usage comment receipt belongs to a different PR')
    azure = isinstance(repository, azure_repos.Repository)

    def invoke(resource, method='GET', payload=None, **route):
        args = ['devops', 'invoke', '--organization', repository.organization_url, '--detect', 'false',
                '--area', 'git', '--resource', resource, '--route-parameters',
                'project=' + repository.project, 'repositoryId=' + repository.name,
                'pullRequestId=' + str(pull['number']), *[k + '=' + str(v) for k, v in route.items()],
                '--api-version', '7.1']
        if payload is None:
            return azure_call(*args)
        with tempfile.TemporaryDirectory(prefix='usage-comment-') as directory:
            path = Path(directory) / 'body.json'
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            return azure_call(*args, '--http-method', method, '--in-file', str(path), '--media-type', 'application/json')

    if azure:
        raw = invoke('pullRequestThreads')
        threads = raw.get('value', []) if isinstance(raw, dict) else raw
        candidates = [(thread['id'], comment) for thread in threads for comment in thread.get('comments', [])
                      if not comment.get('isDeleted') and (marker in (comment.get('content') or '')
                      or thread['id'] == document.get('usage_thread'))]
        author = pull.get('azure', {}).get('createdBy', {}).get('id')
    else:
        raw = github_call(f'repos/{repository}/issues/{pull["number"]}/comments', '--paginate', '--slurp')
        comments = [row for page in raw for row in page] if raw and isinstance(raw[0], list) else raw
        candidates = [(None, comment) for comment in comments if marker in (comment.get('body') or '')]
        author = github_call('user')['id']
    if len(candidates) > 1:
        raise ValueError('multiple usage comments found; preserve them for review')
    if candidates:
        thread_id, comment = candidates[0]
        current = comment.get('content' if azure else 'body') or ''
        observed_author = comment.get('author' if azure else 'user', {}).get('id')
        if author is None or author != observed_author:
            raise ValueError('usage comment has a different author')
        if marker not in current:
            if not azure or thread_id != document.get('usage_thread') or table(current) != table(content):
                raise ValueError('existing usage comment does not match the frozen snapshot')
        elif current != content and (not previous or previous.get('content_sha256') != hashlib.sha256(current.encode()).hexdigest()):
            raise ValueError('usage comment was edited by a person; preserve the edit')
        comment_id = comment['id']
        if current != content:
            if azure:
                invoke('pullRequestThreadComments', 'PATCH', {'content': content}, threadId=thread_id, commentId=comment_id)
            else:
                github_call(f'repos/{repository}/issues/comments/{comment_id}', '--method', 'PATCH', '-f', 'body=' + content)
    else:
        if document.get('usage_thread'):
            raise ValueError('approved existing usage thread was not found')
        if azure:
            thread = invoke('pullRequestThreads', 'POST', {'status': 'closed', 'comments': [
                {'parentCommentId': 0, 'commentType': 1, 'content': content}]})
            thread_id, comment_id = thread['id'], thread['comments'][0]['id']
        else:
            comment = github_call(f'repos/{repository}/issues/{pull["number"]}/comments', '--method', 'POST', '-f', 'body=' + content)
            thread_id, comment_id = None, comment['id']
    # Read back before acknowledging success; interrupted publication is found
    # by the durable marker even if this local receipt was never written.
    observed = (invoke('pullRequestThreadComments', threadId=thread_id, commentId=comment_id) if azure else
                github_call(f'repos/{repository}/issues/comments/{comment_id}'))
    if observed.get('content' if azure else 'body') != content:
        raise ValueError('provider did not retain the usage comment')
    url = pull['html_url'] + (f'?discussionId={thread_id}' if azure else f'#issuecomment-{comment_id}')
    result = {'status': 'published', 'pull_request': pull['html_url'], 'url': url,
              'thread_id': thread_id, 'comment_id': comment_id, 'snapshot_sha256': digest,
              'summary_sha256': hashlib.sha256(section(document).strip().encode()).hexdigest(),
              'content_sha256': hashlib.sha256(content.encode()).hexdigest()}
    receipt.parent.mkdir(parents=True, exist_ok=True)
    _common.atomic_write(receipt, json.dumps(result, indent=2) + '\n')
    return result
