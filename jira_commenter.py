#!/usr/bin/env python3
"""
Adds a "moved to GitHub" comment to every migrated Jira issue, based on the mapping output
file (lines of "JIRA-KEY:github issue number") written by the import.

Authentication: JIRA_MIGRATION_JIRA_TOKEN (personal access token, sent as Bearer) or
JIRA_MIGRATION_JIRA_USER / JIRA_MIGRATION_JIRA_PASSWORD (basic auth).
The comment text can be customized with JIRA_MIGRATION_JIRA_COMMENT_TEMPLATE_FILE using the
placeholders {key}, {issue_number}, {issue_url} and {repo_url} (Jira wiki markup).

Usage: uv run jira_commenter.py [--dry-run]
Issues already commented are recorded in <mapping file>.commented and skipped on reruns.
"""

import sys

import requests

from config import load_config

DEFAULT_TEMPLATE = (
    'This issue has been migrated to GitHub: [{issue_url}]\n\n'
    'Please continue the discussion there. Further updates in Jira will not be considered.\n\n'
    '(This is an automated comment.)'
)

_TIME_OUT = 120.0


def _read_mapping(path):
    with open(path, encoding='utf-8') as f:
        pairs = [line.strip().split(':', 1) for line in f if ':' in line]
    return [(key.strip(), number.strip()) for key, number in pairs]


def _read_done(path):
    try:
        with open(path, encoding='utf-8') as f:
            return {line.strip() for line in f if line.strip()}
    except FileNotFoundError:
        return set()


def _template(config):
    if config.jira_comment_template_file:
        with open(config.jira_comment_template_file, encoding='utf-8') as f:
            return f.read().strip()
    return DEFAULT_TEMPLATE


def _render(template, **values):
    # not str.format: Jira wiki markup uses braces itself ({code}, {quote}, ...)
    for name, value in values.items():
        template = template.replace('{' + name + '}', value)
    return template


def _request_kwargs(config):
    if config.jira_token:
        return {'headers': {'Authorization': f'Bearer {config.jira_token}'}}
    if config.jira_user:
        return {'headers': {}, 'auth': (config.jira_user, config.jira_password)}
    raise RuntimeError('Set JIRA_MIGRATION_JIRA_TOKEN or JIRA_MIGRATION_JIRA_USER/_PASSWORD')


def comment_all(config, dry_run=False):
    """Comments all migrated, not yet commented issues; returns the commented keys."""
    done_file = config.mapping_output_file + '.commented'
    done = _read_done(done_file)
    template = _template(config)
    kwargs = None if dry_run else _request_kwargs(config)

    commented = []
    for key, number in _read_mapping(config.mapping_output_file):
        if key in done:
            continue
        body = _render(template, key=key, issue_number=number, repo_url=config.github_repo_url,
                       issue_url=f'{config.github_repo_url}/issues/{number}')
        url = f'{config.jira_url_normalized}/rest/api/2/issue/{key}/comment'
        if dry_run:
            print(f'[dry-run] {url}\n{body}\n')
            continue
        response = requests.post(url, json={'body': body}, timeout=_TIME_OUT, **kwargs)
        if response.status_code != 201:
            raise RuntimeError(f'Commenting {key} failed: {response.status_code} {response.text[:500]}')
        with open(done_file, 'a', encoding='utf-8') as f:
            f.write(key + '\n')
        print('commented', key)
        commented.append(key)
    return commented


if __name__ == '__main__':
    comment_all(load_config(required=('github_account', 'github_repo')), dry_run='--dry-run' in sys.argv)
