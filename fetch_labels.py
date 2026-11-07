#!/usr/bin/env python3
"""Prints all Jira labels found in the configured XML export(s)."""

from config import load_config
from project import Project
from utils import read_xml_files


def main():
    config = load_config(required=('file_paths',))
    project = Project(config)

    for f in read_xml_files(config.file_paths):
        for item in f.channel.item:
            project.add_item(item)

    for key in sorted(project.get_labels().keys()):
        print(key)


if __name__ == '__main__':
    main()
