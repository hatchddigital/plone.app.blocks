import logging

from lxml import etree
from lxml import html

from zope.component import queryUtility
from zope.site.hooks import getSite

from plone.subrequest import subrequest

from plone.registry.interfaces import IRegistry

from plone.app.blocks.interfaces import DEFAULT_SITE_LAYOUT_REGISTRY_KEY
from plone.app.blocks.layoutbehavior import ILayoutAware

from Acquisition import aq_inner
from Acquisition import aq_parent

from zExceptions import NotFound

from zope.interface import Interface
from zope.schema.interfaces import IField

from zope.security.interfaces import IPermission
from AccessControl import getSecurityManager

from z3c.form.interfaces import IEditForm, IFieldWidget, DISPLAY_MODE, \
                                HIDDEN_MODE
from plone.supermodel.utils import mergedTaggedValueDict, mergedTaggedValueList
from plone.autoform.interfaces import IFormFieldProvider
from plone.autoform.interfaces import OMITTED_KEY, WIDGETS_KEY, MODES_KEY
from plone.autoform.interfaces import READ_PERMISSIONS_KEY, \
                                      WRITE_PERMISSIONS_KEY
from plone.autoform.utils import mergedTaggedValuesForIRO

from plone.dexterity.interfaces import IDexterityFTI
from plone.dexterity.utils import resolveDottedName

from plone.app.blocks.interfaces import IOmittedField
from zope.component import queryUtility, getMultiAdapter


headXPath = etree.XPath("/html/head")
layoutAttrib = 'data-layout'
layoutXPath = etree.XPath("/html/@" + layoutAttrib)
gridAttrib = 'data-gridsystem'
gridXPath = etree.XPath("/html/@" + gridAttrib)
tileAttrib = 'data-tile'
headTileXPath = etree.XPath("/html/head//*[@" + tileAttrib + "]")
bodyTileXPath = etree.XPath("/html/body//*[@" + tileAttrib + "]")
panelXPath = etree.XPath("//*[@data-panel]")
gridDataAttrib = 'data-grid'
gridDataXPath = etree.XPath("//*[@" + gridDataAttrib + "]")
logger = logging.getLogger('plone.app.blocks')


def extractCharset(response, default='utf-8'):
    """Get the charset of the given response
    """

    charset = default
    if 'content-type' in response.headers:
        for item in response.headers['content-type'].split(';'):
            if item.strip().startswith('charset'):
                charset = item.split('=')[1].strip()
                break
    return charset


def resolve(url):
    """Resolve the given URL to an lxml tree.
    """

    resolved = resolveResource(url)
    return html.fromstring(resolved).getroottree()


def resolveResource(url):
    """Resolve the given URL to a unicode string. If the URL is an absolute
    path, it will be made relative to the Plone site root.
    """
    if url.startswith('/'):
        site = getSite()
        url = '/'.join(site.getPhysicalPath()) + url

    response = subrequest(url)
    if response.status == 404:
        raise NotFound(url)

    resolved = response.getBody()

    if isinstance(resolved, str):
        charset = extractCharset(response)
        resolved = resolved.decode(charset)

    if response.status != 200:
        raise RuntimeError(resolved)

    return resolved


def xpath1(xpath, node, strict=True):
    """Return a single node matched by the given etree.XPath object.
    """

    if isinstance(xpath, basestring):
        xpath = etree.XPath(xpath)

    result = xpath(node)
    if len(result) == 1:
        return result[0]
    else:
        if (len(result) > 1 and strict) or len(result) == 0:
            return None
        else:
            return result


def append_text(element, text):
    if text:
        element.text = (element.text or '') + text


def append_tail(element, text):
    if text:
        element.tail = (element.tail or '') + text


def replace_with_children(element, wrapper):
    """element.replace also replaces the tail and forgets the wrapper.text
    """
    # XXX needs tests
    parent = element.getparent()
    index = parent.index(element)
    if index == 0:
        previous = None
    else:
        previous = parent[index - 1]
    if wrapper is None:
        children = []
    else:
        if index == 0:
            append_text(parent, wrapper.text)
        else:
            append_tail(previous, wrapper.text)
        children = wrapper.getchildren()
    parent.remove(element)
    if not children:
        if index == 0:
            append_text(parent, element.tail)
        else:
            append_tail(previous, element.tail)
    else:
        append_tail(children[-1], element.tail)
        children.reverse()
        for child in children:
            parent.insert(index, child)


def replace_content(element, wrapper):
    """Similar to above but keeps parent tag
    """
    del element[:]
    if wrapper is not None:
        element.text = wrapper.text
        element.extend(wrapper.getchildren())


def getDefaultSiteLayout(context):
    """Get the path to the site layout to use by default for the given content
    object
    """

    # Note: the sectionSiteLayout on context is for pages *under* context, not
    # necessarily context itself

    parent = aq_parent(aq_inner(context))
    while parent is not None:
        layoutAware = ILayoutAware(parent, None)
        if layoutAware is not None:
            if getattr(layoutAware, 'sectionSiteLayout', None):
                return layoutAware.sectionSiteLayout
        parent = aq_parent(aq_inner(parent))

    registry = queryUtility(IRegistry)
    if registry is None:
        return None

    return registry.get(DEFAULT_SITE_LAYOUT_REGISTRY_KEY)


def getLayoutAwareSiteLayout(context):
    """Get the path to the site layout for a page. This is generally only
    appropriate for the view of this page. For a generic template or view, use
    getDefaultSiteLayout(context) instead.
    """

    layoutAware = ILayoutAware(context, None)
    if layoutAware is not None:
        if getattr(layoutAware, 'pageSiteLayout', None):
            return layoutAware.pageSiteLayout

    return getDefaultSiteLayout(context)

class PermissionChecker(object):

    def __init__(self, permissions, context):
        self.permissions = permissions
        self.context = context
        self.sm = getSecurityManager()
        self.cache = {}

    def allowed(self, field_name):
        permission_name = self.permissions.get(field_name, None)
        if permission_name is not None:
            if permission_name not in self.cache:
                permission = queryUtility(IPermission, name=permission_name)
                if permission is None:
                    self.cache[permission_name] = True
                else:
                    self.cache[permission_name] = bool(
                        self.sm.checkPermission(permission.title,
                                                self.context),
                    )
        return self.cache.get(permission_name, True)


def _getWidgetName(field, widgets, request):
    if field.__name__ in widgets:
        factory = widgets[field.__name__]
    else:
        factory = getMultiAdapter((field, request), IFieldWidget)
    if isinstance(factory, basestring):
        return factory
    if not isinstance(factory, type):
        factory = factory.__class__
    return '%s.%s' % (factory.__module__, factory.__name__)


def isVisible(name, omitted):
    value = omitted.get(name, False)
    if isinstance(value, basestring):
        return value == 'false'
    else:
        return not bool(value)




def extractFieldInformation(schema, context, request, prefix):
    iro = [IEditForm, Interface]
    if prefix != '':
        prefix += '-'
    omitted = mergedTaggedValuesForIRO(schema, OMITTED_KEY, iro)
    modes = mergedTaggedValuesForIRO(schema, MODES_KEY, iro)
    widgets = mergedTaggedValueDict(schema, WIDGETS_KEY)

    if context is not None:
        read_permissionchecker = PermissionChecker(
            mergedTaggedValueDict(schema, READ_PERMISSIONS_KEY),
            context,
        )
        write_permissionchecker = PermissionChecker(
            mergedTaggedValueDict(schema, WRITE_PERMISSIONS_KEY),
            context,
        )
    
    read_only = []
    for name, mode in modes.items():
        if mode == HIDDEN_MODE:
            omitted[name] = True
        elif mode == DISPLAY_MODE:
            read_only.append(name)
    for name in schema.names(True):
        if context is not None:
            if not read_permissionchecker.allowed(name):
                omitted[name] = True
            if not write_permissionchecker.allowed(name):
                read_only.append(name)
        if isVisible(name, omitted):
            field = schema[name]
            if not IField.providedBy(field):
                continue
            if not IOmittedField.providedBy(field):
                yield {
                    'id': "%s.%s" % (schema.__identifier__, name),
                    'name': prefix + name,
                    'title': schema[name].title,
                    'widget': _getWidgetName(schema[name], widgets, request),
                    'readonly': name in read_only,
                }
