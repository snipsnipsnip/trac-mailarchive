#!/usr/bin/env python
# -*- coding: utf-8 -*-

from setuptools import setup

setup(
    name = 'MailArchive',
    version = '1.3',
    author = 'Peter Suter',
    author_email = 'peter@lucid.ch',
    description = 'Mail Archive',
    packages = ['mailarchive'],
    package_data = {'mailarchive': ['templates/*.html']},

    entry_points = {'trac.plugins': [
            'mailarchive.admin = mailarchive.admin',
            'mailarchive.web_ui = mailarchive.web_ui',
        ]
    },
)
