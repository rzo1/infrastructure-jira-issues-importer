import pytest

from project import Project
from utils import read_xml_files


@pytest.fixture
def load_project(make_config, sample_xml):
    def _load(**overrides):
        project = Project(make_config(**overrides))
        for f in read_xml_files(sample_xml):
            for item in f.channel.item:
                project.add_item(item)
        return project
    return _load


def issue(project, key):
    return next(i for i in project.get_issues() if i['key'] == key)


def test_only_items_of_configured_project_are_imported(load_project):
    project = load_project()
    assert [i['key'] for i in project.get_issues()] == ['TEST-1', 'TEST-2', 'TEST-3', 'TEST-4']


def test_minimal_item_without_optional_elements(load_project):
    project = load_project()
    minimal = issue(project, 'TEST-4')

    assert minimal['closed'] is True
    assert minimal['closed_at'] == '2025-11-08T10:00:00+00:00'
    assert minimal['comments'] == []
    assert 'Done long ago.' in minimal['body']


def test_open_issue_has_no_closed_at(load_project):
    project = load_project()
    open_issue = issue(project, 'TEST-1')

    assert open_issue['closed'] is False
    assert 'closed_at' not in open_issue
    assert open_issue['created_at'] == '2025-11-03T10:00:00+00:00'
    assert open_issue['updated_at'] == '2025-11-04T11:00:00+00:00'


def test_body_metadata(load_project):
    body = issue(load_project(), 'TEST-1')['body']

    assert body.startswith('<p>The tokenizer drops')
    assert 'href="https://jira.example.org/browse/TEST-1"' in body
    assert 'Originally reported by <a title="Bob Example"' in body
    assert '<b>assignee</b>: <a title="Alice Example"' in body
    assert '<b>status</b>: Open' in body
    assert '<b>priority</b>: Major' in body


def test_unassigned_and_empty_description(load_project):
    body = issue(load_project(), 'TEST-2')['body']

    assert body.startswith('No Description')
    assert '<b>assignee</b>' not in body


def test_comments_keep_author_and_timestamp(load_project):
    comments = issue(load_project(), 'TEST-1')['comments']

    assert comments[0]['created_at'] == '2025-11-03T12:00:00+00:00'
    assert 'name=carol">carol</a>' in comments[0]['body']
    assert comments[0]['body'].endswith('<p>Confirmed.</p>')


def test_html_entities_in_description_are_kept(load_project):
    body = issue(load_project(), 'TEST-1')['body']
    assert body.startswith('<p>The tokenizer drops &amp; loses dots.</p>')


def test_escaped_markup_is_not_turned_into_tags(make_config):
    project = Project(make_config())
    # Jira exports descriptions as HTML; escaped code like Java generics must stay escaped,
    # otherwise GitHub treats <String> as an (unknown, dropped) HTML tag
    html = '<pre>List&lt;String&gt; tokens = new ArrayList&lt;&gt;();</pre>'
    assert project._htmlentitydecode(html) == html
