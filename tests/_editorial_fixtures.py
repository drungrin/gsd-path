"""Authored, synthetic publication contracts for integration fixtures."""
import json
from pathlib import Path
from scripts import pr_editorial

TITLE = 'feat(web): deliver reviewed behavior'


def publication_fixture(repo, archive, member, reviewed, remote, title=TITLE):
    root = repo / archive
    defaults = {'intent/INTENT.md': '# Intent\n\nA reviewed fixture change.\n',
                'MANIFEST.md': '# Archive\n\nThe fixture records its evidence.\n',
                'review/FINAL.md': 'Reviewed HEAD: ' + reviewed + '\n'}
    for relative, content in defaults.items():
        path = root / relative
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    value = {'schema': pr_editorial.SCHEMA, 'archive': archive, 'member': member,
             'reviewed_head': reviewed, 'remote': remote, 'language': 'en', 'title': title,
             'sections': {'problem': 'The fixture needs its reviewed behavior.',
                          'changes': 'Deliver the member changes covered by the fixture.',
                          'scope': 'Only the reviewed repository is included.',
                          'validation': 'The integration fixture checks the recorded landing.',
                          'risks': 'The fixture uses local bare remotes.'}, 'work_items': [],
             'sources': pr_editorial.sources(repo, archive)}
    path = pr_editorial.publication_path(repo, archive, member)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return value
