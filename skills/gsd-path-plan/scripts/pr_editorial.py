#!/usr/bin/env python3
# gsd-path project runtime
"""Validate authored PR copy and bind Azure delivery actions to reviewed evidence.

The ship owner writes the prose; this runtime validates and publishes it. The
persistent publication is outside immutable milestone archives. Mutable forge
receipts live in the Git common directory, and never substitute merge proofs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
if __package__:
    from . import _common, azure_repos, azure_work_items
else:
    import _common, azure_repos, azure_work_items

SCHEMA = 'gsd-path/pr-editorial/v1'
LANGUAGES = {
    'en': ('Problem', 'Changes', 'Scope', 'Validation', 'Risks and limitations', 'Work items'),
    'pt-BR': ('Problema', 'O que muda', 'Escopo', 'Validação', 'Riscos e limitações', 'Itens de trabalho'),
}
SECTIONS = ('problem', 'changes', 'scope', 'validation', 'risks')
TITLE = re.compile(r'(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([a-z0-9._/-]+\))?!?: \S[^\r\n]*')
SHA = re.compile(r'[0-9a-f]{40}')


class EditorialError(ValueError):
    pass


def git(repo, *args):
    result = _common.run_git(repo, *args)
    if result.returncode:
        raise EditorialError(result.stderr.strip() or 'cannot read publication Git identity')
    return result.stdout.strip()


def regular(path, missing=False):
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise EditorialError('publication paths must not contain symlinks')
    if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise EditorialError('publication must be a regular unlinked file')
    if not missing and not path.is_file():
        raise EditorialError('required editorial artifact is missing: ' + str(path))
    return path


def archive_name(archive):
    parts = Path(archive).parts
    if len(parts) != 3 or parts[:2] != ('.project', 'archive') or not re.fullmatch(r'\d{3,}-[a-z0-9-]+', parts[2]):
        raise EditorialError('publication requires a canonical archive path')
    return parts[2]


def publication_path(repo, archive, member='coordinator'):
    repo = repo.resolve()
    if not re.fullmatch(r'[a-z][a-z0-9-]*', member):
        raise EditorialError('invalid publication member')
    return repo / '.project/publications' / archive_name(archive) / (member + '.json')


def sources(repo, archive):
    repo = repo.resolve()
    archive_name(archive)
    result = {}
    for relative in ('intent/INTENT.md', 'MANIFEST.md', 'review/FINAL.md'):
        path = regular(repo / archive / relative)
        result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def language(repo):
    if __package__:
        from . import path_config
    else:
        import path_config
    local = path_config.preferences(repo / '.project/config.json')
    return local.get('editorial_language', path_config.user_defaults().get('editorial_language', 'en'))


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise EditorialError(label + ' must contain authored text')
    if _common.without_tool_attribution(value) != value:
        raise EditorialError(label + ' contains tool attribution')
    if re.search(r'(?<!\w)(?:/home/|/tmp/|[A-Za-z]:\\)', value):
        raise EditorialError(label + ' must not depend on local or temporary paths')
    if re.fullmatch(r'<[^>]+>', value.strip()) or re.search(r'<(?:TODO|fill|write|insert|describe)[^>]*>|\bTODO\b', value, re.I):
        raise EditorialError(label + ' contains a placeholder')
    return value.strip()


def validate_document(value):
    if not isinstance(value, dict) or value.get('schema') != SCHEMA:
        raise EditorialError('invalid editorial schema')
    archive_name(value.get('archive', ''))
    if (value.get('language') not in LANGUAGES or not isinstance(value.get('reviewed_head'), str)
            or not SHA.fullmatch(value['reviewed_head'])):
        raise EditorialError('invalid publication language or reviewed HEAD')
    if not TITLE.fullmatch(text(value.get('title'), 'title')) or value['title'] != value['title'].strip():
        raise EditorialError('PR title must be a Conventional Commit subject')
    sections = value.get('sections')
    if not isinstance(sections, dict) or set(sections) != set(SECTIONS):
        raise EditorialError('editorial requires problem, changes, scope, validation and risks')
    for key in SECTIONS:
        text(sections[key], key)
    items = value.get('work_items', [])
    if not isinstance(items, list):
        raise EditorialError('work_items must be a list')
    seen = set()
    for item in items:
        if (not isinstance(item, dict) or type(item.get('id')) is not int or item['id'] < 1
                or item['id'] in seen or item.get('disposition') not in {'complete', 'partial', 'obsolete'}):
            raise EditorialError('invalid, duplicate or unscoped work item disposition')
        seen.add(item['id'])
        for field in ('summary', 'evidence'):
            text(item.get(field), 'work item ' + field)
        if item['disposition'] == 'partial':
            text(item.get('remaining'), 'partial work remaining')
        if item['disposition'] == 'obsolete':
            text(item.get('reason'), 'obsolete work item reason')
    if value.get('usage') is not None:
        if __package__:
            from . import usage_report
        else:
            import usage_report
        usage_report.validate_snapshot(value['usage'])
    if value.get('usage_thread') is not None and (type(value['usage_thread']) is not int or value['usage_thread'] < 1):
        raise EditorialError('usage thread must be a positive native thread id')
    return value


def require_body_format(body, selected_language):
    for heading in LANGUAGES[selected_language][:5]:
        found = re.search(r'(?m)^## ' + re.escape(heading) + r'[ \t]*\n([\s\S]*?)(?=\n## |\Z)', body)
        if found is None or not found.group(1).strip():
            raise EditorialError('PR description requires a populated ' + heading + ' section')


def discover_work_items(content):
    patterns = (r'_workitems/edit/(\d+)', r'\bAB#(\d+)\b', r'(?<!\w)#(\d+)\b',
                r'\b(?:board|work[ -]item)\s+(\d+)\b')
    return sorted({int(value) for pattern in patterns for value in re.findall(pattern, content, re.I) if int(value) > 0})


def prepare(repo, archive, member, reviewed_head, authored, call=None):
    """Bind authored copy to immutable evidence and current Azure acceptance."""
    repo = repo.resolve()
    value = dict(authored, schema=SCHEMA, archive=archive, member=member,
                 reviewed_head=reviewed_head, language=language(repo), sources=sources(repo, archive))
    usage_status = None
    if not value.get('usage') and (repo / '.project/STATE.md').exists():
        if __package__:
            from . import usage_collect, usage_report
        else:
            import usage_collect, usage_report
        try:
            config, _ = usage_collect.location(repo)
            if config.exists():
                usage_status = usage_collect.observe(repo)
                value['usage'] = usage_report.report(repo)
        except (OSError, ValueError) as error:
            usage_status = {'status': 'incomplete', 'reason': str(error)}
    validate_document(value)
    checkout = repo
    if member != 'coordinator':
        if __package__:
            from . import members
        else:
            import members
        listed = [item for item in members.read_members(repo) if item['name'] == member]
        if len(listed) != 1:
            raise EditorialError('publication member is not registered')
        checkout = Path(listed[0]['checkout'])
    value['remote'] = git(checkout, 'config', '--get', 'remote.origin.url')
    azure = azure_repos.parse_remote(value['remote'])
    if value.get('work_items'):
        if azure is None or call is None:
            raise EditorialError('Azure work items require the member Azure repository and CLI')
        client = azure_work_items.Client(azure, call)
        mentioned = discover_work_items((repo / archive / 'intent/INTENT.md').read_text(encoding='utf-8'))
        if any(item['id'] not in mentioned for item in value['work_items']):
            raise EditorialError('work item is not explicitly referenced by the approved intent')
        value['work_items'] = [dict(item, snapshot=client.snapshot(item['id'])) for item in value['work_items']]
    path = regular(publication_path(repo, archive, member), missing=True)
    existing = _common.run_git(repo, 'cat-file', '-e', 'HEAD:' + path.relative_to(repo).as_posix())
    if existing.returncode == 0:
        raise EditorialError('committed publication contracts are immutable')
    path.parent.mkdir(parents=True, exist_ok=True)
    _common.atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    result = {'status': 'prepared', 'path': str(path), 'title': value['title']}
    if usage_status is not None:
        result['usage_collection'] = usage_status
    return result


def load(repo, archive, member, reviewed_head=None):
    path = publication_path(repo, archive, member)
    if not path.exists():
        raise EditorialError('editorial-required: prepare ' + str(path) + ' before publishing the PR')
    value = validate_document(json.loads(regular(path).read_text(encoding='utf-8')))
    if value.get('member') != member or value['archive'] != archive:
        raise EditorialError('editorial artifact belongs to a different publication')
    if reviewed_head is not None and value['reviewed_head'] != reviewed_head:
        raise EditorialError('editorial is stale for the reviewed source HEAD')
    if value.get('sources') != sources(repo, archive):
        raise EditorialError('editorial is stale for the approved milestone evidence')
    if value['language'] != language(repo):
        raise EditorialError('editorial language changed; prepare the copy again')
    return value


def render(value):
    headings = LANGUAGES[value['language']]
    result = '\n\n'.join('## ' + heading + '\n\n' + value['sections'][key].strip()
                           for key, heading in zip(SECTIONS, headings))
    items = value.get('work_items', [])
    if items:
        azure = azure_repos.parse_remote(value['remote'])
        if azure is None:
            raise EditorialError('work items cannot be published for a non-Azure repository')
        from urllib.parse import quote
        lines = [f"- [#{item['id']}]({azure.organization_url}/{quote(azure.project, safe='')}/_workitems/edit/{item['id']}) — {item['summary']}" for item in items]
        result += '\n\n## ' + headings[5] + '\n\n' + '\n'.join(lines)
    if __package__:
        from . import usage_publish
    else:
        import usage_publish
    return usage_publish.description(result + '\n', value)


def client(value, repository, call):
    if not isinstance(repository, azure_repos.Repository):
        if __package__:
            from . import members
        else:
            import members
        expected = members._remote_identity('https://github.com/' + repository + '.git')
        if members._remote_identity(value['remote']) != expected:
            raise EditorialError('publication repository differs from approved GitHub origin')
        if value.get('work_items'):
            raise EditorialError('Azure work items require an Azure publication')
        return None
    recorded = azure_repos.parse_remote(value['remote'])
    if recorded is None or recorded.identity != repository.identity:
        raise EditorialError('publication repository differs from approved Azure origin')
    return azure_work_items.Client(repository, call)


def link(value, repository, pull, call):
    api = client(value, repository, call)
    return api.link(pull['number'], value.get('work_items', [])) if api else []


def synchronize(repo, archive, member, reviewed_head, repository, pull, landing, call, source_head=None):
    """Integration owners call this *after* their normal landing/tag validation."""
    path = publication_path(repo, archive, member)
    if not path.exists():
        return {'status': 'not-configured', 'items': []}
    value = load(repo, archive, member, reviewed_head)
    if (pull.get('merged_at') is None or pull.get('state') != 'closed'
            or pull.get('merge_commit_sha') != landing or pull.get('head', {}).get('sha') != (source_head or reviewed_head)):
        raise EditorialError('Boards synchronization requires the exact validated merged PR')
    api = client(value, repository, call)
    if api is None or not value.get('work_items'):
        return {'status': 'complete', 'items': []}
    common = Path(git(repo, 'rev-parse', '--path-format=absolute', '--git-common-dir'))
    receipt = regular(common / 'gsd-path/work-item-sync' / archive_name(archive) / (member + '.json'), missing=True)
    results = []
    try:
        expected = {item['id'] for item in value['work_items']}
        if not expected <= api.linked(pull['number']):
            raise EditorialError('approved work items are no longer linked to the merged PR')
        for item in value['work_items']:
            results.append(api.deliver(item, pull, landing, value['language']))
        output = {'status': 'complete', 'items': results, 'landing': landing, 'pull_request': pull['html_url']}
    except (ValueError, OSError, RuntimeError) as error:
        output = {'status': 'pending', 'items': results, 'landing': landing,
                  'pull_request': pull['html_url'], 'reason': str(error)}
    output['publication_hash'] = hashlib.sha256(path.read_bytes()).hexdigest()
    receipt.parent.mkdir(parents=True, exist_ok=True)
    _common.atomic_write(receipt, json.dumps(output, ensure_ascii=False, indent=2) + '\n')
    return output


def require_synchronized(repo, archive, member, landing):
    path = publication_path(repo, archive, member)
    if not path.exists():
        return
    value = validate_document(json.loads(regular(path).read_text(encoding='utf-8')))
    if not value.get('work_items'):
        return
    common = Path(git(repo, 'rev-parse', '--path-format=absolute', '--git-common-dir'))
    receipt = common / 'gsd-path/work-item-sync' / archive_name(archive) / (member + '.json')
    if not receipt.exists():
        raise EditorialError('work items synchronization pending; resume integration')
    result = json.loads(regular(receipt).read_text(encoding='utf-8'))
    if (result.get('status') != 'complete' or result.get('landing') != landing
            or result.get('publication_hash') != hashlib.sha256(path.read_bytes()).hexdigest()):
        raise EditorialError('work items synchronization pending; resume integration')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('context', 'prepare', 'show', 'publish'))
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--member', default='coordinator')
    parser.add_argument('--reviewed-head')
    parser.add_argument('--input', type=Path)
    args = parser.parse_args(argv)
    try:
        repo = args.repo.resolve()
        if args.action == 'context':
            result = {'language': language(repo), 'archive': args.archive, 'member': args.member,
                      'sources': sources(repo, args.archive),
                      'candidate_work_items': discover_work_items((repo / args.archive / 'intent/INTENT.md').read_text(encoding='utf-8')),
                      'output': str(publication_path(repo, args.archive, args.member)),
                      'required_sections': list(SECTIONS), 'title_format': 'Conventional Commit'}
        elif args.action == 'prepare':
            if args.input is None or args.reviewed_head is None:
                parser.error('prepare requires --input and --reviewed-head')
            if __package__:
                from . import integration
            else:
                import integration
            result = prepare(repo, args.archive, args.member, args.reviewed_head,
                             json.loads(regular(args.input.parent.resolve() / args.input.name).read_text(encoding='utf-8')), integration.azure_cli_json)
        elif args.action == 'publish':
            if __package__:
                from . import integration, pipeline_state
            else:
                import integration, pipeline_state
            state, _, _ = pipeline_state.load_state(repo)
            if (state.phase not in ('ship', 'shipped') or state.archive != args.archive
                    or state.branch != git(repo, 'branch', '--show-current')):
                raise EditorialError('publication must belong to the bound ship transaction')
            document = load(repo, args.archive, args.member, args.reviewed_head)
            if args.member == 'coordinator':
                checkout, bound, default = repo, state.branch, state.default_branch
                source = git(repo, 'rev-parse', 'HEAD')
            else:
                checkout, _, bound, _, _, default = integration._member_names(repo, args.member, args.archive)
                source = document['reviewed_head']
            repository = integration.pull_request_repository(checkout)
            pull = integration.find_pull_request(repository, bound, source, default)
            if pull is None or pull['state'] != 'open' or pull['merged_at'] is not None:
                raise EditorialError('editorial publication requires the existing open PR at the reviewed HEAD')
            updated = integration.publish_editorial(repo, repository, pull, document, bound, source, default)
            result = {'status': 'published', 'pull_request': updated['html_url'], 'title': updated.get('title'),
                      'linked_work_items': [item['id'] for item in document.get('work_items', [])]}
            if updated.get('usage_publication'):
                result['usage_publication'] = updated['usage_publication']
        else:
            value = load(repo, args.archive, args.member, args.reviewed_head)
            result = {'title': value['title'], 'body': render(value), 'path': str(publication_path(repo, args.archive, args.member))}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, RuntimeError) as error:
        print(json.dumps({'status': 'error', 'error': str(error)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
