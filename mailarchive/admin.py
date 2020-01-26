# -*- coding: utf-8 -*-

from __future__ import print_function

import imaplib
import datetime
import os

from trac.admin import IAdminCommandProvider
from trac.attachment import Attachment
from trac.core import Component, TracError, implements
from trac.db.api import DatabaseManager
from trac.env import IEnvironmentSetupParticipant
from trac.util.text import exception_to_unicode
from trac.util.translation import _

from mailarchive.model import ArchivedMail, SCHEMA, normalized_filename

PLUGIN_NAME = 'MailArchivePlugin'
PLUGIN_VERSION = 2


def to_imap_date(d):
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return "%s-%s-%s" % (d.day, months[d.month - 1], d.year)


class MailArchiveAdmin(Component):

    implements(IEnvironmentSetupParticipant, IAdminCommandProvider)

    # IAdminCommandProvider methods

    def get_admin_commands(self):
        yield ('mailarchive fetch', '<host> <username> <password>',
               'Download mails to the archive (via IMAP4)',
               None, self._do_fetch)
        yield ('mailarchive fix-attachment-filenames', '',
               'Normalize old broken attachment filenames.',
               None, self._do_fix_attachment_filenames)

    def _do_fetch(self, host, username, password):
        imap_conn = imaplib.IMAP4_SSL(host)
        imap_conn.login(username, password)
        imap_conn.select()

        # Search for mails since yesterday
        yesterday = to_imap_date(datetime.date.today() - datetime.timedelta(1))
        typ, data = imap_conn.uid('search', None, '(OR UNSEEN (SINCE %s))' % (yesterday,))
        for uid in data[0].split():

            # No duplicates
            if ArchivedMail.select_by_id(self.env, uid) is not None:
                print("Skipping mail with UID %s" % (uid,))
                continue

            typ, data = imap_conn.uid('fetch', uid, '(RFC822)')
            source = data[0][1]
            mail, msg = ArchivedMail.parse(uid, source)
            ArchivedMail.add(self.env, mail)
            ArchivedMail.storeattachments(self.env, mail, msg)
        imap_conn.close()
        imap_conn.logout()

    def _do_fix_attachment_filenames(self):
        realm = 'mailarchive'
        for mail in ArchivedMail.select_all(self.env):
            for attachment in Attachment.select(self.env, realm, mail.id):
                new_filename = normalized_filename(attachment.filename)
                if new_filename != attachment.filename:
                    self._rename_attachment(attachment, new_filename)

    def _rename_attachment(self, attachment, new_filename):
        self.env.log.info("Renaming attachment of %s:%s from '%s' to '%s'",
                          attachment.parent_realm, attachment.parent_id,
                          attachment.filename, new_filename)
        new_path = attachment._get_path(self.env.attachments_dir,
                                        attachment.parent_realm,
                                        attachment.parent_id, new_filename)

        # Make sure the path to the attachment is inside the environment
        # attachments directory
        commonprefix = os.path.commonprefix([self.env.attachments_dir,
                                             new_path])
        if commonprefix != self.env.attachments_dir:
            raise TracError(_('Cannot rename attachment "%(att)s" as '
                              '%(new)s is invalid',
                              att=attachment.filename, new=new_filename))

        if os.path.exists(new_path):
            raise TracError(_('Cannot rename attachment "%(att)s" to %(new)s '
                              'as it already exists',
                              att=attachment.filename, new=new_filename))
        with self.env.db_transaction as db:
            db("""UPDATE attachment SET filename=%s
                  WHERE type=%s AND id=%s AND filename=%s
                  """, (new_filename, attachment.parent_realm,
                        attachment.parent_id, attachment.filename))
            dirname = os.path.dirname(new_path)
            if not os.path.exists(dirname):
                os.makedirs(dirname)
            path = attachment.path
            if os.path.isfile(path):
                try:
                    os.rename(path, new_path)
                except OSError as e:
                    self.env.log.error("Failed to move attachment file %s: %s",
                                       path,
                                       exception_to_unicode(e, traceback=True))
                    raise TracError(_("Could not rename attachment %(name)s",
                                      name=attachment.filename))

        attachment.filename = new_filename

        self.env.log.info("Attachment renamed: %s", new_filename)

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
