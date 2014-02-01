# -*- coding: utf-8 -*-

import imaplib
import datetime

from trac.admin import IAdminCommandProvider
from trac.db.api import DatabaseManager
from trac.core import *
from trac.env import IEnvironmentSetupParticipant

from mailarchive.model import ArchivedMail, SCHEMA

PLUGIN_NAME = 'MailArchivePlugin'
PLUGIN_VERSION = 1

class MailArchiveAdmin(Component):

    implements(IEnvironmentSetupParticipant, IAdminCommandProvider)

    # IAdminCommandProvider methods

    def get_admin_commands(self):
        yield ('mailarchive fetch', '<host> <username> <password>',
               'Download mails to the archive (via IMAP4)',
               None, self._do_fetch)
        
    def _do_fetch(self, host, username, password):
        imap_conn = imaplib.IMAP4_SSL(host)
        imap_conn.login(username, password)
        imap_conn.select()

        # Search for mails since yesterday
        yesterday = (datetime.date.today() - datetime.timedelta(1)).strftime("%d-%b-%Y")
        typ, data = imap_conn.uid('search', None, '(OR UNSEEN (SINCE %s))' % (yesterday,))
        for uid in data[0].split():
        
            # No duplicates
            if ArchivedMail.select_by_id(self.env, uid) is not None:
                print "Skipping mail with UID %s" % (uid,)
                continue

            typ, data = imap_conn.uid('fetch', uid, '(RFC822)')
            source = data[0][1]
            mail, msg = ArchivedMail.parse(uid, source)
            ArchivedMail.add(self.env, mail)
            ArchivedMail.storeattachments(self.env, mail, msg)
        imap_conn.close()
        imap_conn.logout()

    # IEnvironmentSetupParticipant

    def environment_created(self):
        db_connector, _ = DatabaseManager(self.env).get_connector()
        with self.env.db_transaction as db:
            cursor = db.cursor()
            for table in SCHEMA:
                for stmt in db_connector.to_sql(table): 
                    cursor.execute(stmt) 
            cursor.execute(""" 
                INSERT INTO system (name, value) 
                VALUES (%s, %s) 
                """, (PLUGIN_NAME, PLUGIN_VERSION)) 

    def environment_needs_upgrade(self, db):
        rows = self.env.db_query("""
                SELECT value FROM system WHERE name='%s'
                """ % PLUGIN_NAME)
        dbver = int(rows[0][0]) if rows else 0
        if dbver == PLUGIN_VERSION:
            return False
        elif dbver > PLUGIN_VERSION:
            self.env.log.info("%s database schema version is %s, should be %s",
                         PLUGIN_NAME, dbver, PLUGIN_VERSION)
        return True

    def upgrade_environment(self, db):
        db_connector, _ = DatabaseManager(self.env).get_connector() 
        cursor = db.cursor()
        for table in SCHEMA:
            for stmt in db_connector.to_sql(table): 
                cursor.execute(stmt) 
        cursor.execute(""" 
            INSERT INTO system (name, value) 
            VALUES (%s, %s) 
            """, (PLUGIN_NAME, PLUGIN_VERSION))
