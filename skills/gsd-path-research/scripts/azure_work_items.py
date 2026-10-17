#!/usr/bin/env python3
# gsd-path project runtime
"""Scoped Azure Boards publication and optimistic, repeat-safe delivery updates."""
from __future__ import annotations

import hashlib
import html
import json
import tempfile
from pathlib import Path


class WorkItemError(ValueError):
    pass


def records(value):
    result = value.get('value') if isinstance(value, dict) else value
    if not isinstance(result, list):
        raise WorkItemError('Azure Boards returned an invalid collection')
    return result


class Client:
    def __init__(self, repository, call):
        self.repository, self.call = repository, call

    def invoke(self, resource, route, query=(), patch=None):
        args = ['devops', 'invoke', '--organization', self.repository.organization_url,
                '--detect', 'false', '--area', 'wit', '--resource', resource,
                '--route-parameters', 'project=' + self.repository.project, *route,
                '--api-version', '7.1']
        if query:
            args += ['--query-parameters', *query]
        if patch is None:
            return self.call(*args)
        with tempfile.TemporaryDirectory(prefix='work-item-update-') as temporary:
            path = Path(temporary) / 'patch.json'
            path.write_text(json.dumps(patch, ensure_ascii=False), encoding='utf-8')
            return self.call(*args, '--http-method', 'PATCH', '--in-file', str(path),
                             '--media-type', 'application/json-patch+json')

    def item(self, number):
        value = self.call('boards', 'work-item', 'show', '--id', str(number),
                          '--organization', self.repository.organization_url)
        if (not isinstance(value, dict) or value.get('id') != number
                or not isinstance(value.get('rev'), int) or not isinstance(value.get('fields'), dict)):
            raise WorkItemError('Azure Boards returned the wrong work item')
        fields = value['fields']
        if (not isinstance(fields.get('System.TeamProject'), str)
                or fields['System.TeamProject'].casefold() != self.repository.project.casefold()):
            raise WorkItemError('work item belongs to a different Azure project')
        for key in ('System.Title', 'System.State', 'System.WorkItemType'):
            if not isinstance(fields.get(key), str) or not fields[key]:
                raise WorkItemError('work item has no ' + key)
        return value

    def snapshot(self, number):
        return self.snapshot_value(self.item(number))

    @staticmethod
    def snapshot_value(value):
        fields = value['fields']
        contract = {key: fields.get(key) for key in (
            'System.TeamProject', 'System.WorkItemType', 'System.Title',
            'System.Description', 'Microsoft.VSTS.Common.AcceptanceCriteria')}
        digest = hashlib.sha256(json.dumps(contract, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return {'id': value['id'], 'type': fields['System.WorkItemType'], 'title': fields['System.Title'],
                'state': fields['System.State'], 'requirements_hash': digest}

    def linked(self, pull):
        values = records(self.call('repos', 'pr', 'work-item', 'list', '--id', str(pull),
                                   '--organization', self.repository.organization_url, '--detect', 'false'))
        result = set()
        for item in values:
            number = item.get('id') if isinstance(item, dict) else None
            if not str(number).isdigit() or int(number) < 1:
                raise WorkItemError('pull request returned an invalid work item link')
            result.add(int(number))
        return result

    def link(self, pull, items):
        expected = {item['id'] for item in items}
        if not expected:
            return []
        for item in items:
            observed = self.snapshot(item['id'])
            if observed['requirements_hash'] != item['snapshot']['requirements_hash']:
                raise WorkItemError('work item acceptance changed before PR publication')
        missing = expected - self.linked(pull)
        if missing:
            self.call('repos', 'pr', 'work-item', 'add', '--id', str(pull),
                      '--work-items', *map(str, sorted(missing)),
                      '--organization', self.repository.organization_url, '--detect', 'false')
        if not expected <= self.linked(pull):
            raise WorkItemError('Azure did not retain all approved work item links')
        return sorted(expected)

    def history_contains(self, number, marker):
        skip, seen = 0, set()
        while True:
            page = records(self.invoke('updates', [f'id={number}'], [f'$top=100', f'$skip={skip}']))
            for update in page:
                identifier = update.get('id')
                if identifier in seen:
                    raise WorkItemError('Azure work item history pagination repeated an update')
                seen.add(identifier)
                history = ((update.get('fields') or {}).get('System.History') or {}).get('newValue') or ''
                if marker in history:
                    return True
            if len(page) < 100:
                return False
            skip += len(page)

    def target_state(self, item, disposition, preferred=None):
        if disposition == 'partial':
            return None
        category = 'Completed' if disposition == 'complete' else 'Removed'
        states = records(self.invoke('workItemTypeStates', ['type=' + item['fields']['System.WorkItemType']]))
        candidates = [state['name'] for state in states if state.get('category') == category]
        current = item['fields']['System.State']
        current_category = next((state.get('category') for state in states if state.get('name') == current), None)
        if ((disposition == 'complete' and current_category == 'Removed')
                or (disposition == 'obsolete' and current_category == 'Completed')):
            raise WorkItemError('work item was closed or removed differently after editorial review')
        if current in candidates:
            return current
        if preferred is not None:
            if preferred not in candidates:
                raise WorkItemError('approved target state is not in category ' + category)
            return preferred
        if len(candidates) != 1:
            raise WorkItemError('select an explicit state in category ' + category)
        return candidates[0]

    def deliver(self, planned, pull, landing, language):
        """Called only after the integration validator proves this landing."""
        number = planned['id']
        marker = 'Delivery-Ref: ' + hashlib.sha256(
            f"{pull['html_url']}\n{landing}\n{number}".encode()).hexdigest()
        # The service history closes the crash window after PATCH and before the
        # local receipt. An already applied delivery never undoes a human reopen.
        if self.history_contains(number, marker):
            return {'id': number, 'status': 'already-applied'}
        item = self.item(number)
        snapshot = self.snapshot_value(item)
        if snapshot['requirements_hash'] != planned['snapshot']['requirements_hash']:
            raise WorkItemError('work item acceptance changed after editorial review')
        target = self.target_state(item, planned['disposition'], planned.get('target_state'))
        pt = language == 'pt-BR'
        labels = ('Entrega', 'Validação', 'Pendências', 'Motivo') if pt else ('Delivery', 'Validation', 'Remaining work', 'Reason')
        rows = [(labels[0], planned['summary']), (labels[1], planned['evidence'])]
        if planned.get('remaining'):
            rows.append((labels[2], planned['remaining']))
        if planned.get('reason'):
            rows.append((labels[3], planned['reason']))
        message = '<p><a href="' + html.escape(pull['html_url'], quote=True) + '">PR #' + str(pull['number']) + '</a>'
        message += ' · commit <code>' + html.escape(landing) + '</code></p>'
        message += ''.join('<p><strong>' + label + ':</strong> ' + html.escape(text) + '</p>' for label, text in rows)
        message += '<p>' + marker + '</p>'
        patch = [{'op': 'test', 'path': '/rev', 'value': item['rev']}]
        if target is not None and item['fields']['System.State'] != target:
            patch.append({'op': 'add', 'path': '/fields/System.State', 'value': target})
        patch.append({'op': 'add', 'path': '/fields/System.History', 'value': message})
        updated = self.invoke('workitems', [f'id={number}'], patch=patch)
        if not isinstance(updated, dict) or updated.get('id') != number:
            raise WorkItemError('Azure returned the wrong updated work item')
        if target is not None and updated.get('fields', {}).get('System.State') != target:
            raise WorkItemError('Azure did not apply the approved work item state')
        if not self.history_contains(number, marker):
            raise WorkItemError('Azure did not retain the delivery comment')
        return {'id': number, 'status': 'applied', 'state': updated.get('fields', {}).get('System.State')}
