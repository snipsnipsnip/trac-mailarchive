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
        dbm = DatabaseManager(self.env)
        dbm.create_tables(SCHEMA)
        dbm.set_database_version(PLUGIN_VERSION, PLUGIN_NAME)

    def environment_needs_upgrade(self):
        dbm = DatabaseManager(self.env)
        return dbm.needs_upgrade(PLUGIN_VERSION, PLUGIN_NAME)

    def upgrade_environment(self):
        dbm = DatabaseManager(self.env)
        if dbm.get_database_version(PLUGIN_NAME) == 0:
            dbm.create_tables(SCHEMA)
            dbm.set_database_version(PLUGIN_VERSION, PLUGIN_NAME)
        else:
            dbm.upgrade(PLUGIN_VERSION, PLUGIN_NAME, 'mailarchive.upgrades')
