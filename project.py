from collections import defaultdict
from dateutil.parser import parse
from datetime import datetime
from html import escape
from urllib.parse import quote

from utils import fetch_labels_mapping, fetch_allowed_labels, convert_label


def _child(item, name):
    """Returns the first child element called ``name`` or None if it does not exist."""
    return item.find(name)


def _children(item, path):
    """Returns all elements matching ``path`` (may be empty)."""
    return item.findall(path)


class Project:

    def __init__(self, config):
        self.config = config
        self.name = config.jira_project
        self.doneStatusCategoryId = str(config.jira_done_id)
        self.jiraBaseUrl = config.jira_url_normalized
        self._project = {'Milestones': defaultdict(int), 'Components': defaultdict(
            int), 'Labels': defaultdict(int), 'Types': defaultdict(int), 'Issues': []}

        self.labels_mapping = fetch_labels_mapping(config.labels_mapping_file)
        self.approved_labels = fetch_allowed_labels(config.allowed_labels_file)

    def get_milestones(self):
        return self._project['Milestones']

    def get_components(self):
        return self._project['Components']

    def get_issues(self):
        return self._project['Issues']

    def get_types(self):
        return self._project['Types']

    def get_all_labels(self):
        merge = self._project['Components'].copy()
        merge.update(self._project['Labels'])
        merge.update(self._project['Types'])
        merge.update({'imported-jira-issue': 0})
        return merge

    def get_labels(self):
        merge = self._project['Labels'].copy()
        merge.update({'imported-jira-issue': 0})
        return merge

    def add_item(self, item):
        itemProject = self._projectFor(item)
        if itemProject != self.name:
            print('Skipping item ' + item.key.text + ' for project ' +
                  itemProject + ' current project: ' + self.name)
            return

        self._append_item_to_project(item)

        self._add_milestone(item)

        self._add_labels(item)

        self._add_subtasks(item)

        self._add_parenttask(item)

        self._add_comments(item)

        self._add_relationships(item)

    def prettify(self):
        def hist(h):
            for key in h.keys():
                print(('%30s (%5d): ' + h[key] * '#') % (key, h[key]))
            print

        print(self.name + ':\n  Milestones:')
        hist(self._project['Milestones'])
        print('  Types:')
        hist(self._project['Types'])
        print('  Components:')
        hist(self._project['Components'])
        print('  Labels:')
        hist(self._project['Labels'])
        print
        print('Total Issues to Import: %d' % len(self._project['Issues']))

    def _projectFor(self, item):
        try:
            result = item.project.get('key')
        except AttributeError:
            result = item.key.text.split('-')[0]
        return result

    def _user_link(self, user):
        username = user.get('username') or str(user)
        return ('<a title="' + escape(str(user)) + '" href="' + self.jiraBaseUrl
                + '/secure/ViewProfile.jspa?name=' + quote(username) + '">' + escape(username) + '</a>')

    def _append_item_to_project(self, item):
        closed = str(item.statusCategory.get('id')) == self.doneStatusCategoryId
        resolved = _child(item, 'resolved')
        closed_at = ''
        if closed and resolved is not None and resolved.text:
            closed_at = self._convert_to_iso(resolved.text)

        description = _child(item, 'description')
        if description is None or not (description.text or '').strip():
            body = 'No Description'
        else:
            body = self._htmlentitydecode(description.text)

        # metadata: original author & link
        summary = item.title.text[item.title.text.index("]") + 2:]
        reporter = _child(item, 'reporter')
        body = body + '\n\n---\n<details><summary><i>'
        if reporter is not None:
            body = body + 'Originally reported by ' + self._user_link(reporter) + ', '
        body = body + 'imported from: <a href="' + self.jiraBaseUrl + '/browse/' + item.key.text + '" target="_blank">' + escape(summary) + '</a></i></summary>'
        body = body + '\n<i><ul>'

        assignee = _child(item, 'assignee')
        if assignee is not None and assignee.get('username') not in (None, '-1'):
            body = body + '\n<li><b>assignee</b>: ' + self._user_link(assignee)
        for field in ('status', 'priority', 'resolution'):
            value = _child(item, field)
            if value is not None and value.text:
                body = body + '\n<li><b>' + field + '</b>: ' + escape(value.text)
        if resolved is not None and resolved.text:
            body = body + '\n<li><b>resolved</b>: ' + self._convert_to_iso(resolved.text)
        body = body + '\n<li><b>imported</b>: ' + datetime.today().strftime('%Y-%m-%d')
        body = body + '\n</ul></i>\n</details>'

        # retrieve jira components and labels as github labels
        labels = []

        # set a default component if empty or missing
        components = [c.text for c in _children(item, 'component') if c.text] or ['miscellaneous']
        for component in components:
            if self.config.include_component_in_labels:
                labels.append('jira-component:' + component.lower())
                labels.append(component.lower())

        if len(_children(item, 'labels/label')) == 0:
            pass
        else:
            labels.append(self._jira_type_mapping(item.type.text.lower()))

        for label in _children(item, 'labels/label'):
            converted_label = convert_label(label.text.strip().lower(), self.labels_mapping, self.approved_labels)
            if converted_label is not None:
                labels.append(converted_label)

        labels.append('imported-jira-issue')

        unique_labels = list(set(labels))

        self._project['Issues'].append({'title': item.title.text,
                                        'key': item.key.text,
                                        'body': body,
                                        'created_at': self._convert_to_iso(item.created.text),
                                        'closed_at': closed_at,
                                        'updated_at': self._convert_to_iso(item.updated.text),
                                        'closed': closed,
                                        'labels': unique_labels,
                                        'comments': [],
                                        'duplicates': [],
                                        'is-duplicated-by': [],
                                        'is-related-to': [],
                                        'depends-on': [],
                                        'blocks': []
                                        })
        if not self._project['Issues'][-1]['closed_at']:
            del self._project['Issues'][-1]['closed_at']

    def _jira_type_mapping(self, issue_type):
        if issue_type == 'bug':
            return 'bug'
        if issue_type == 'improvement':
            return 'rfe'
        if issue_type == 'new feature':
            return 'rfe'
        if issue_type == 'task':
            return 'rfe'
        if issue_type == 'story':
            return 'rfe'
        if issue_type == 'patch':
            return 'rfe'
        if issue_type == 'epic':
            return 'epic'

    def _convert_to_iso(self, timestamp):
        dt = parse(timestamp)
        return dt.isoformat()

    def _add_milestone(self, item):
        # GitHub supports a single milestone per issue, so the first fixVersion is used
        versions = [v.text.strip() for v in _children(item, 'fixVersion') if v.text and v.text.strip()]
        if versions:
            self._project['Milestones'][versions[0]] += 1
            # this prop will be replaced by the GitHub milestone number later:
            self._project['Issues'][-1]['milestone_name'] = versions[0]

    def _add_labels(self, item):
        try:
            self._project['Components'][item.component.text] += 1
            tmp_l = item.component.text.trim()
            if tmp_l == 'Bug':
                tmp_l = 'bug'

            self._project['Issues'][-1]['labels'].append(tmp_l)
        except AttributeError:
            pass
        
        try:
            for label in item.labels.label:
                self._project['Labels'][label.text] += 1
                tmp_l = label.text.trim()
                if tmp_l == 'Bug':
                    tmp_l = 'bug'

                self._project['Issues'][-1]['labels'].append(tmp_l)
        except AttributeError:
            pass

        try:
            self._project['Types'][item.type.text] += 1
            tmp_l = item.type.text.trim()
            if tmp_l == 'Bug':
                tmp_l = 'bug'

            self._project['Issues'][-1]['labels'].append(tmp_l)
        except AttributeError:
            pass

    def _add_subtasks(self, item):
        try:
            subtaskList = ''
            for subtask in item.subtasks.subtask:
                subtaskList = subtaskList + '- ' + subtask + '\n'
            if subtaskList != '':
                print('-> subtaskList: ' + subtaskList)
                self._project['Issues'][-1]['comments'].append(
                    {"created_at": self._convert_to_iso(item.created.text),
                     "body": 'Subtasks:\n\n' + subtaskList})
        except AttributeError:
            pass

    def _add_parenttask(self, item):
        try:
            parentTask = item.parent.text
            if parentTask != '':
                print('-> parentTask: ' + parentTask)
                self._project['Issues'][-1]['comments'].append(
                    {"created_at": self._convert_to_iso(item.created.text),
                     "body": 'Subtask of parent task ' + parentTask})
        except AttributeError:
            pass

    def _add_comments(self, item):
        try:
            for comment in item.comments.comment:
                self._project['Issues'][-1]['comments'].append(
                    {"created_at": self._convert_to_iso(comment.get('created')),
                     "body": '<i><a href="' + self.jiraBaseUrl + '/secure/ViewProfile.jspa?name=' + comment.get('author') + '">' + comment.get('author') + '</a>:</i>\n' + self._htmlentitydecode(comment.text)
                     })
        except AttributeError:
            pass

    def _add_relationships(self, item):
        try:
            for issuelinktype in item.issuelinks.issuelinktype:
                for outwardlink in issuelinktype.outwardlinks:
                    for issuelink in outwardlink.issuelink:
                        for issuekey in issuelink.issuekey:
                            tmp_outward = outwardlink.get("description").replace(' ', '-')
                            if tmp_outward in self._project['Issues'][-1]:
                                self._project['Issues'][-1][tmp_outward].append(issuekey.text)
        except AttributeError:
            pass
        except KeyError:
            print('1. KeyError at ' + item.key.text)
        try:
            for issuelinktype in item.issuelinks.issuelinktype:
                for inwardlink in issuelinktype.inwardlinks:
                    for issuelink in inwardlink.issuelink:
                        for issuekey in issuelink.issuekey:
                            tmp_inward = inwardlink.get("description").replace(' ', '-')
                            if tmp_inward in self._project['Issues'][-1]:
                                self._project['Issues'][-1][tmp_inward].append(issuekey.text)
        except AttributeError:
            pass
        except KeyError:
            print('2. KeyError at ' + item.key.text)

        for customfield in _children(item, 'customfields/customfield'):
            if customfield.get('key') == 'com.pyxis.greenhopper.jira:gh-epic-link':
                epic_key = customfield.customfieldvalues.customfieldvalue
                self._project['Issues'][-1]['epic-link'] = epic_key

    def _htmlentitydecode(self, s):
        """
        Prepares Jira's HTML for GitHub. The text already is HTML, so entities must stay
        encoded (decoding would turn escaped code like List&lt;String&gt; into tags).
        Runs of 8 spaces (Jira's export indentation) are removed so GitHub's Markdown
        renderer does not turn them into code blocks.
        """
        if s is None:
            return ''
        return s.replace(' ' * 8, '')
