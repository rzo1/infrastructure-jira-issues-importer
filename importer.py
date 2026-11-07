import time
from html import escape
from urllib.parse import quote

import requests

from utils import fetch_labels_mapping, fetch_allowed_labels, convert_label


class Importer:
    _DEFAULT_TIME_OUT = 120.0

    def __init__(self, config, project):
        self.config = config
        self.project = project
        self.github_url = config.github_repo_api_url
        self.headers = {
            'Accept': 'application/vnd.github.golden-comet-preview+json',
            'Authorization': f'token {config.github_token}'
        }

        self.labels_mapping = fetch_labels_mapping(config.labels_mapping_file)
        self.approved_labels = fetch_allowed_labels(config.allowed_labels_file)
        self._existing_milestones = set()
        self._imported_keys = {issue['key'] for issue in project.get_issues()}

    def _get_all_pages(self, url):
        """GETs a paginated GitHub list resource, following the Link: rel="next" headers."""
        results = []
        while url:
            response = requests.get(url, headers=self.headers, timeout=Importer._DEFAULT_TIME_OUT)
            if response.status_code != 200:
                raise RuntimeError(f'Failed to list {url}: {response.status_code} {response.text}')
            results.extend(response.json())
            url = response.links.get('next', {}).get('url')
        return results

    def import_milestones(self):
        """
        Maps the gathered Jira fix versions to GitHub milestones (matched by title),
        creating the missing ones, and remembers the GitHub milestone numbers.
        """
        milestone_url = self.github_url + '/milestones'
        milestones = self.project.get_milestones()
        print('Importing milestones...', milestone_url)

        for existing in self._get_all_pages(milestone_url + '?state=all&per_page=100'):
            if existing['title'] in milestones:
                milestones[existing['title']] = existing['number']
                print(existing['title'], 'found:', existing['number'])
                self._existing_milestones.add(existing['title'])

        for title in list(milestones.keys()):
            if title in self._existing_milestones:
                continue
            r = requests.post(milestone_url, json={'title': title}, headers=self.headers,
                              timeout=Importer._DEFAULT_TIME_OUT)
            if r.status_code != 201:
                raise RuntimeError(f'Failed to create milestone {title}: {r.status_code} {r.text}')
            milestones[title] = r.json()['number']
            self._existing_milestones.add(title)
            print(title, 'created:', milestones[title])

    def import_labels(self, colour_selector):
        """
        Creates the final GitHub labels (already mapped and filtered by the project).
        Labels that already exist in the repository are left untouched.
        """
        label_url = self.github_url + '/labels'
        print('Importing labels...', label_url)

        for label in self.project.get_all_labels().keys():
            data = {'name': label, 'color': colour_selector.get_colour(label)}
            r = requests.post(label_url, json=data, headers=self.headers, timeout=Importer._DEFAULT_TIME_OUT)
            if r.status_code == 201:
                print('created label', label)
            elif r.status_code == 422 and 'already_exists' in str(r.json()):
                print('label exists', label)
            else:
                print('Failure importing label ' + label, r.status_code, r.text)

    def import_issues(self, start_from_count):
        """
        Starts the issue import into GitHub:
        First the milestone id is captured for the issue.
        Then JIRA issue relationships are converted into comments.
        After that, the comments are taken out of the issue and 
        references to JIRA issues in comments are replaced with a placeholder    
        """
        print('Importing issues...')

        count = 0

        for issue in self.project.get_issues():
            if start_from_count > count:
                count += 1
                continue

            print("Index = ", count)

            if 'milestone_name' in issue:
                issue['milestone'] = self.project.get_milestones()[
                    issue['milestone_name']]
                del issue['milestone_name']

            self.convert_relationships_to_comments(issue)

            issue_comments = issue['comments']
            del issue['comments']
            comments = list(issue_comments)

            self.import_issue_with_comments(issue, comments)
            count += 1

    def import_issue_with_comments(self, issue, comments):
        """
        Imports a single issue with its comments into GitHub.
        Importing via GitHub's normal Issue API quickly triggers anti-abuse rate limits.
        So their unofficial Issue Import API is used instead:
        https://gist.github.com/jonmagic/5282384165e0f86ef105
        This is a two-step process:
        First the issue with the comments is pushed to GitHub asynchronously.
        Then GitHub is pulled in a loop until the issue import is completed.
        Finally the issue github is noted.    
        """
        print('Issue ', issue['key'])
        issue['labels'] = [label for label in issue['labels'] if label]
        print('Labels', issue['labels'])
        jira_key = issue['key']
        del issue['key']

        print(f"issues: {issue}")
        print(f"comments: {comments}")

        response = self.upload_github_issue(issue, comments)
        status_url = response.json()['url']
        gh_issue_url = self.wait_for_issue_creation(status_url).json()['issue_url']
        gh_issue_id = int(gh_issue_url.split('/')[-1])
        issue['githubid'] = gh_issue_id
        issue['key'] = jira_key

        jira_gh = f"{jira_key}:{gh_issue_id}\n"
        with open(self.config.mapping_output_file, 'a') as f:
            f.write(jira_gh)

    def upload_github_issue(self, issue, comments):
        """
        Uploads a single issue to GitHub asynchronously with the Issue Import API.
        """
        issue_url = self.github_url + '/import/issues'
        issue_data = {'issue': issue, 'comments': comments}
        response = requests.post(issue_url, json=issue_data, headers=self.headers, 
            timeout=Importer._DEFAULT_TIME_OUT)
        if response.status_code == 202:
            return response
        elif response.status_code == 422:
            raise RuntimeError(
                "Initial import validation failed for issue '{}' due to the "
                "following errors:\n{}".format(issue['title'], response.json())
            )
        else:
            raise RuntimeError(
                "Failed to POST issue: '{}' due to unexpected HTTP status code: {}\nerrors:\n{}"
                .format(issue['title'], response.status_code, response.json())
            )

    def wait_for_issue_creation(self, status_url):
        """
        Check the status of a GitHub issue import.
        If the status is 'pending', it sleeps, then rechecks until the status is
        either 'imported' or 'failed'.
        """
        while True:  # keep checking until status is something other than 'pending'
            time.sleep(3)
            response = requests.get(status_url, headers=self.headers, 
                timeout=Importer._DEFAULT_TIME_OUT)
            if response.status_code == 404:
                continue
            elif response.status_code != 200:
                raise RuntimeError(
                    "Failed to check GitHub issue import status url: {} due to unexpected HTTP status code: {}"
                    .format(status_url, response.status_code)
                )

            status = response.json()['status']
            if status != 'pending':
                break

        if status == 'imported':
            print("Imported Issue:", response.json()['issue_url'].replace('api.github.com/repos/', 'github.com/'))
        elif status == 'failed':
            raise RuntimeError(
                "Failed to import GitHub issue due to the following errors:\n{}"
                .format(response.json())
            )
        else:
            raise RuntimeError(
                "Status check for GitHub issue import returned unexpected status: '{}'"
                .format(status)
            )
        return response

    def _issue_href(self, key):
        """
        Links to the GitHub issue (searching the key in the title) when the issue is part of
        this import, otherwise to the issue in Jira.
        """
        if key in self._imported_keys:
            return f'{self.config.github_repo_url}/issues?q=' + quote(f'is:issue in:title "{key}"', safe='')
        return f'{self.project.jiraBaseUrl}/browse/{quote(key)}'

    def convert_relationships_to_comments(self, issue):
        """Turns the collected Jira issue links and the epic link into comments."""
        links = issue.pop('links', [])
        if links:
            items = ''.join(f'\n<li>{escape(description)} <a href="{self._issue_href(key)}">{escape(key)}</a></li>'
                            for description, key in links)
            issue['comments'].append({'body': f'<i>Jira issue links:</i>\n<ul>{items}\n</ul>'})

        epic_link = issue.pop('epic-link', None)
        if epic_link:
            # format is parsed by post_process_issues.sh
            issue['comments'].append(
                {'body': f'<i>[Epic: <a href="{self._issue_href(epic_link)}">{escape(epic_link)}</a>]</i>'})
