# -*- coding: utf-8 -*-

import re
from email.utils import getaddresses
from pkg_resources import resource_filename
from genshi.builder import tag

from trac.attachment import AttachmentModule, ILegacyAttachmentPolicyDelegate
from trac.config import Option
from trac.core import *
from trac.perm import IPermissionRequestor
from trac.resource import IResourceManager, Resource, ResourceNotFound, resource_exists
from trac.search import ISearchSource, shorten_result
from trac.util.html import escape
from trac.util.datefmt import format_datetime
from trac.util.presentation import Paginator
from trac.web import IRequestHandler
from trac.web.chrome import (INavigationContributor, ITemplateProvider, 
                             add_link, add_script, prevnext_nav, web_context)
from trac.wiki.formatter import format_to_html
from trac.wiki.macros import WikiMacroBase
from trac.wiki.api import IWikiSyntaxProvider, parse_args

from mailarchive.model import ArchivedMail


def render_mailto(addresses):
    parts = []
    for name, addr in getaddresses([addresses]):
        label = escape(name or addr)
        mailto = tag.a(tag.span(label,
                                class_='icon'),
                       href='mailto:' + addr,
                       class_='mail-link',
                       title="%s <%s>" % (label, addr))
        parts.append(mailto)
        parts.append(', ')
    return tag.span(parts[:-1])


class MailArchiveModule(Component):
    """Archived emails."""

    implements(ILegacyAttachmentPolicyDelegate, INavigationContributor, IPermissionRequestor,
               IRequestHandler, IResourceManager, ISearchSource, ITemplateProvider, IWikiSyntaxProvider)

    help = Option('mailarchive', 'help', '**Note:** See MailArchive for help on using the mail archive.',
                  """Help text shown at bottom in wiki format.""")
               
    # ILegacyAttachmentPolicyDelegate

    def check_attachment_permission(self, action, username, resource, perm):
        if resource.parent.realm == 'mailarchive':
            if action == 'ATTACHMENT_VIEW':
                return 'MAIL_ARCHIVE_VIEW' in perm(resource.parent)
            elif action in ('ATTACHMENT_CREATE','ATTACHMENT_DELETE'): 
                return 'TRAC_ADMIN' in perm(resource.parent)

    # INavigationContributor methods

    def get_active_navigation_item(self, req):
        return 'mailarchive'

    def get_navigation_items(self, req):
        if 'MAIL_ARCHIVE_VIEW' in req.perm:
            yield ('mainnav', 'mailarchive', 
                   tag.a('Mail Archive', href=req.href.mailarchive()))

    # IPermissionRequestor methods
    
    def get_permission_actions(self):
        return ['MAIL_ARCHIVE_VIEW']

    # IResourceManager methods
        
    def get_resource_realms(self):
        yield 'mailarchive'

    def get_resource_url(self, resource, href, **kwargs):
        return href.mailarchive(resource.id)

    def get_resource_description(self, resource, format='default',
                                 context=None, **kwargs):
        if format == 'compact':
            return resource.id
        return 'Mail %s' % resource.id

    def resource_exists(self, resource):
        return ArchivedMail.select_by_id(self.env, resource.id) is not None

    # IRequestHandler methods
    
    MATCH_REQUEST_RE = re.compile(r'/mailarchive(?:/(\d+))?$')
    
    def match_request(self, req):
        match = self.MATCH_REQUEST_RE.match(req.path_info)
        if match:
            if match.group(1):
                req.args['message-id'] = match.group(1)
            return True

    def process_request(self, req):
        req.perm.require('MAIL_ARCHIVE_VIEW')

        if 'message-id' in req.args:
            id = int(req.args.get('message-id'))
            return self._render_mail(req, id)
        return self._render_list(req)

    def _render_list(self, req):
        page = int(req.args.get('page', 1))
        max_per_page = int(req.args.get('max', 40))

        mails = [{
            'subject': escape(mail.subject),
            'href': req.href.mailarchive(mail.id),
            'from': render_mailto(mail.fromheader or ''),
            'date': format_datetime(mail.date),            
        } for mail in ArchivedMail.select_all_paginated(self.env, page, max_per_page)]
        total_count = ArchivedMail.count_all(self.env)

        paginator = Paginator(mails, page - 1, max_per_page, total_count)
        if paginator.has_next_page:
            next_href = req.href.mailarchive(max=max_per_page, page=page + 1)
            add_link(req, 'next', next_href, 'Next Page')
        if paginator.has_previous_page:
            prev_href = req.href.mailarchive(max=max_per_page, page=page - 1)
            add_link(req, 'prev', prev_href, 'Previous Page')

        pagedata = []
        shown_pages = paginator.get_shown_pages(21)
        for page in shown_pages:
            pagedata.append([req.href.mailarchive(page=page), None,
                             str(page), 'Page %d' % (page,)])
        paginator.shown_pages = [dict(zip(['href', 'class', 'string', 'title'], p)) for p in pagedata]
        paginator.current_page = {'href': None, 'class': 'current',
                                'string': str(paginator.page + 1),
                                'title':None}

        context = web_context(req, 'mailarchive')
        help_html = format_to_html(self.env, context, self.help)

        data = { 'mails': mails, 'paginator': paginator, 'max_per_page': max_per_page, 'help': help_html }
        return "archivedmail-list.html", data, None

    def _render_mail(self, req, id):
        mail = ArchivedMail.select_by_id(self.env, id)
        if not mail:
            raise ResourceNotFound("Mail does not exist")

        resource = Resource('mailarchive', id)
        context = web_context(req, resource)
        data = {
            'mail': {
                'subject': escape(mail.subject),
                'from': render_mailto(mail.fromheader or ''),
                'to': render_mailto(mail.toheader or ''),
                'body': escape(mail.body),
                'allheaders': escape(mail.allheaders),
                'date': format_datetime(mail.date),
            },
            'attachments': AttachmentModule(self.env).attachment_data(context),
        }

        if ArchivedMail.select_by_id(self.env, id - 1) is not None: 
            add_link(req, 'prev', req.href.mailarchive(id - 1), 'Prev')
        add_link(req, 'up', req.href.mailarchive(), 'Up') 
        if ArchivedMail.select_by_id(self.env, id + 1) is not None: 
            add_link(req, 'next', req.href.mailarchive(id + 1), 'Next')
        prevnext_nav(req, 'Prev', 'Next', 'Up')

        add_script(req, 'common/js/folding.js')

        return "archivedmail.html", data, None

    # ISearchSource methods
    
    def get_search_filters(self, req):
        if 'MAIL_ARCHIVE_VIEW' in req.perm:
            yield ('mailarchive', 'Mail Archive', True)

    def get_search_results(self, req, terms, filters):
        if not 'mailarchive' in filters:
            return
        for mail in ArchivedMail.search(self.env, terms):
            dt = mail.date
            link = req.href.mailarchive(mail.id)
            title = escape(mail.subject)
            author = escape(mail.fromheader)
            excerpt = shorten_result(escape(mail.body), terms)
            resource = Resource('mailarchive', mail.id)
            if 'MAIL_ARCHIVE_VIEW' in req.perm(resource):
                yield (link, title, dt, author, excerpt)

    # ITemplateProvider methods
    
    def get_htdocs_dirs(self):
        return []

    def get_templates_dirs(self):
        return [resource_filename('mailarchive', 'templates')]

    # IWikiSyntaxProvider methods

    def get_link_resolvers(self):
        yield ('mail', self._format_mail_link)
        yield ('email', self._format_mail_link)
        yield ('mailarchive', self._format_mail_link)
        yield ('emailarchive', self._format_mail_link)

    def get_wiki_syntax(self):
        return []

    def _format_mail_link(self, formatter, ns, target, label):
        resource = Resource('mailarchive', target)
        if resource_exists(self.env, resource) and 'MAIL_ARCHIVE_VIEW' in formatter.perm(resource):
            return tag.a(label, href=formatter.href.mailarchive(target), title="Archived Mail %s" % (target,))
        return tag.a(label, class_='missing')


class MailQueryMacro(WikiMacroBase):
    """List all matching archived mails.
   
    The first argument is a search term.
   
    Example:
    {{{
        [[MailQuery(bgates@microsoft.com)]]
    }}}
    """

    def expand_macro(self, formatter, name, content):
        args, kw = parse_args(content)
        terms = args
        items = []
        for mail in ArchivedMail.search(self.env, terms):
            link = formatter.href.mailarchive(mail.id)
            title = escape(mail.subject)
            items.append(tag.li(tag.a(title, href=link)))
        return tag.div(tag.ul(item for item in items))
