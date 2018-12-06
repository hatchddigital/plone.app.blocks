# -*- coding: utf-8 -*-
from datetime import date
from DateTime import DateTime
from lxml.html import fromstring
from plone.app.blocks.layoutbehavior import ILayoutAware
from plone.app.blocks.layoutbehavior import ILayoutBehaviorAdaptable
from plone.app.textfield import RichTextValue
from plone.indexer.decorator import indexer
from plone.namedfile.file import NamedFile, NamedBlobFile
from plone.tiles.data import ANNOTATIONS_KEY_PREFIX
from Products.CMFPlone.utils import safe_unicode, getToolByName
from zope.annotation.interfaces import IAnnotations
from z3c.relationfield.relation import RelationValue
from zope.component import adapter
from zope.interface import implementer
import logging
import pkg_resources


try:
    pkg_resources.get_distribution('collective.dexteritytextindexer')
except pkg_resources.DistributionNotFound:
    HAS_DEXTERITYTEXTINDEXER = False
else:
    from collective.dexteritytextindexer.interfaces import IDynamicTextIndexExtender  # noqa
    HAS_DEXTERITYTEXTINDEXER = True

try:
    from plone.app.contenttypes import indexers
    concat = indexers._unicode_save_string_concat
except ImportError:
    def concat(*args):
        result = ''
        for value in args:
            if isinstance(value, unicode):
                value = value.encode('utf-8', 'replace')
            if value:
                result = ' '.join((result, value))
        return result


@indexer(ILayoutBehaviorAdaptable)
def LayoutSearchableText(obj):
    text = [obj.id]
    try:
        text.append(obj.text.output)
    except AttributeError:
        pass
    try:
        text.append(safe_unicode(obj.title))
    except AttributeError:
        pass
    try:
        text.append(safe_unicode(obj.description))
    except AttributeError:
        pass

    behavior_data = ILayoutAware(obj)
    # get data from tile data
    annotations = IAnnotations(obj)
    for key in annotations.keys():
        if key.startswith(ANNOTATIONS_KEY_PREFIX):
            data = annotations[key]
            for field_name in data:
                build_layout_indexed_text(obj, text, data[field_name])

    try:
        if behavior_data.content:
            dom = fromstring(behavior_data.content)
            for el in dom.xpath('//text()'):
                build_layout_indexed_text(obj, text, tostring(el))
    except AttributeError:
        pass

    try:
        if behavior_data.customLayout:
            dom = fromstring(behavior_data.customLayout)
            for el in dom.xpath('//text()'):
                build_layout_indexed_text(obj, text, tostring(el))
    except AttributeError:
        pass

    return concat(*set(text))


def build_layout_indexed_text(obj, indexed_text, value):
    if not value:
        return
    if isinstance(value, (bool, int, date, NamedFile, NamedBlobFile, DateTime)):
        # We can't do anything with these
        return
    elif isinstance(value, basestring):
        indexed_text.append(value)
    elif isinstance(value, (RichTextValue, basestring)):
        if isinstance(value, RichTextValue):
            transforms = getToolByName(obj, 'portal_transforms')
            indexed_text.append(
                transforms.convertTo(
                    'text/plain',
                    value.raw,
                    mimetype='text/html')
                .getData()
                .strip()
            )
    elif isinstance(value, dict):
        for key in value:
            build_layout_indexed_text(obj, indexed_text, value[key])
    elif isinstance(value, (list, set, tuple)):
        for row in value:
            build_layout_indexed_text(obj, indexed_text, row)
    elif isinstance(value, RelationValue):
        indexed_text.append(value.to_object.Title())
    else:
        logger = logging.getLogger(__name__)
        logger.error('Could not do anything with %s (type %s)' % (value, type(value)))


if HAS_DEXTERITYTEXTINDEXER:

    @implementer(IDynamicTextIndexExtender)
    @adapter(ILayoutBehaviorAdaptable)
    class LayoutSearchableTextIndexExtender(object):

        def __init__(self, context):
            self.context = context

        def __call__(self):
            return LayoutSearchableText(self.context)()
