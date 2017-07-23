# -*- coding: utf-8 -*-

from datetime import datetime, tzinfo
import email
from email.header import decode_header
from email.utils import parsedate_tz, mktime_tz
import re
from tempfile import TemporaryFile
import unicodedata

from trac.attachment import Attachment
from trac.db import Table, Column, Index
from trac.mimeview.api import KNOWN_MIME_TYPES
from trac.resource import Resource
from trac.util.datefmt import from_utimestamp, to_utimestamp, utc
from trac.util.text import stripws


SCHEMA = [
    Table('mailarchive', key='id')[
        Column('id'),
        Column('subject'),
        Column('fromheader'),
        Column('toheader'),
        Column('date', type='int64'),
        Column('body'),
        Column('allheaders'),
        Index(['date']),
    ],
]


EXT_MAP = dict((t, exts[0]) for t, exts in KNOWN_MIME_TYPES.items())
EXT_MAP['image/gif'] = 'gif'
EXT_MAP['image/jpeg'] = 'jpeg'
EXT_MAP['image/png'] = 'png'
EXT_MAP['image/tiff'] = 'tiff'
EXT_MAP['image/svg+xml'] = 'svg'

DELETE_CHARS_RE = re.compile(
    '[' +
    ''.join(filter(lambda c: unicodedata.category(c) == 'Cc',
                   map(unichr, xrange(0x10000)))) +
    '\\/:*?"<>|' +
    ']')

def normalized_filename(filename):
    filename = DELETE_CHARS_RE.sub(' ', filename)
    filename = stripws(filename)
    return filename

def header_to_unicode(header):
    if header is None:
        return None
    if isinstance(header, unicode):
        return header
    return u''.join(unicode(part, charset or 'ASCII', errors='replace')
                    for part, charset in decode_header(header))

def to_unicode(s, charset):
    return None if s is None else unicode(s, charset, errors='replace')

def get_charset(m, default='ASCII'):
    return m.get_content_charset() or m.get_charset() or default

def terms_to_clauses(terms):
    """Split list of search terms and the 'or' keyword into list of lists of search terms."""
    clauses = [[]]
    for term in terms:
        if term == 'or':
            clauses.append([])
        else:
            clauses[-1].append(term)
    return clauses

def search_clauses_to_sql(db, columns, clauses):
    """Convert a search query into an SQL WHERE clause and corresponding
    parameters.

    Similar to trac.search.search_to_sql but supports 'or' clauses.

    The result is returned as an `(sql, params)` tuple.
    """
    assert columns and clauses

    likes = ['%s %s' % (i, db.like()) for i in columns]
    c = ' OR '.join(likes)
    sql = '(' + ') OR ('.join('(' + ') AND ('.join([c] * len(clause)) + ')' for clause in clauses) + ')'
    args = []
    for clause in clauses:
        for term in clause:
            args.extend(['%' + db.like_escape(term) + '%'] * len(columns))
    return sql, tuple(args)

class ArchivedMail(object):

    def __init__(self, id, subject, fromheader, toheader, body, allheaders, date):
        self.id = id
        self.subject = subject
        self.fromheader = fromheader
        self.toheader = toheader
        self.body = body
        self.allheaders = allheaders
        self.date = from_utimestamp(date)

    @classmethod
    def parse(cls, id, source):
        msg = email.message_from_string(source)
        charset = get_charset(msg)
        body = None
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == 'text/plain':
                charset = get_charset(part, get_charset(msg))
                body = part.get_payload(decode=True)
                break # Take only the first text/plain part as the body
            elif content_type == 'message/rfc822':
                if part.get('Content-Transfer-Encoding') == 'base64':
                    # This is an invalid email and Python will misdetect a 'text/plain' part that is actually a base64 encoded attachment.
                    break

        date = datetime.fromtimestamp(mktime_tz(parsedate_tz(msg['date'])), utc)

        allheaders = '\n'.join("%s: %s" % item for item in msg.items())

        mail = ArchivedMail(id,
                            header_to_unicode(msg['subject']),
                            header_to_unicode(msg['from']),
                            header_to_unicode(msg['to']),
                            to_unicode(body, charset),
                            to_unicode(allheaders, 'ASCII'),
                            to_utimestamp(date))
        return (mail, msg)

    @classmethod
    def add(cls, env, mail):
        # Insert mail
        with env.db_transaction as db:
            cursor = db.cursor()
            cursor.execute("""
            INSERT INTO mailarchive
                        (id, subject, fromheader, toheader, body, allheaders, date)
                 VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (mail.id, mail.subject, mail.fromheader, mail.toheader, mail.body, mail.allheaders, to_utimestamp(mail.date)))

    @classmethod
    def storeattachments(cls, env, mail, msg):
        def add_attachment(payload, filename):
            with TemporaryFile('w+b') as file:
                file.write(payload)
                size = file.tell()
                file.seek(0)
                attachment = Attachment(env, 'mailarchive', mail.id)
                attachment.insert(filename, file, size)

        def get_filename(part, index):
            filename = header_to_unicode(part.get_filename())
            if not filename:
                mimetype = part.get_content_type()
                ext = EXT_MAP.get(mimetype) or part.get_content_subtype() or mimetype or '_'
                filename = "unnamed-part-%s.%s" % (index, ext)
            return normalized_filename(filename)

        for index, part in enumerate(msg.walk()):
            cd = part.get('Content-Disposition')
            if cd:
                d = cd.strip().split(';')
                if d[0].lower() == 'attachment':
                    filename = get_filename(part, index)
                    if part.get_content_type() == 'message/rfc822' and part.get('Content-Transfer-Encoding') == 'base64':
                        # This is an invalid email and Python will misdetect the attachment in a separate 'text/plain' part, not here.
                        # TODO: actually extract that separate 'text/plain' attachment somehow.
                        add_attachment('Invalid attachment: message/rfc822 parts can not be base64 encoded!', filename)
                        continue
                    add_attachment(part.get_payload(decode=True), filename)
                    continue

            cid = part.get('Content-ID')
            if cid:
                filename = get_filename(part, index)
                add_attachment(part.get_payload(decode=True), filename)

    @classmethod
    def select_all(cls, env):
        with env.db_query as db:
            return [ArchivedMail(id, subject, fromheader, toheader, body, allheaders, date)
                    for id, subject, fromheader, toheader, body, allheaders, date in
                    db("""
                    SELECT id, subject, fromheader, toheader, body, allheaders, date
                    FROM mailarchive
                    """)]

    @classmethod
    def select_all_paginated(cls, env, page, max_per_page):
        with env.db_query as db:
            return [ArchivedMail(id, subject, fromheader, toheader, body, allheaders, date)
                    for id, subject, fromheader, toheader, body, allheaders, date in
                    db("""
                    SELECT id, subject, fromheader, toheader, body, allheaders, date
                    FROM mailarchive
                    ORDER BY date DESC
                    LIMIT %d OFFSET %d
                    """ % (max_per_page, max_per_page * (page - 1)))]

    @classmethod
    def count_all(cls, env):
        with env.db_query as db:
            return db("""
                    SELECT COUNT(*)
                    FROM mailarchive
                    """)[0][0]

    @classmethod
    def search(cls, env, terms):
        with env.db_query as db:
            sql_query, args = search_clauses_to_sql(db, ['body', 'allheaders'], terms_to_clauses(terms))
            return [ArchivedMail(id, subject, fromheader, toheader, body, allheaders, date)
                    for id, subject, fromheader, toheader, body, allheaders, date in
                    db("""
                    SELECT id, subject, fromheader, toheader, body, allheaders, date
                    FROM mailarchive
                    WHERE
                    """ + sql_query, args)]

    @classmethod
    def select_by_id(cls, env, id):
        rows = env.db_query("""
                SELECT id, subject, fromheader, toheader, body, allheaders, date
                FROM mailarchive
                WHERE id=%s
                """, (str(id),))
        if not rows:
            return None
        id, subject, fromheader, toheader, body, allheaders, date = rows[0]
        return ArchivedMail(id, subject, fromheader, toheader, body, allheaders, date)
