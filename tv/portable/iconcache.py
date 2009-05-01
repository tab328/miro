# Miro - an RSS based video player application
# Copyright (C) 2005-2009 Participatory Culture Foundation
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301 USA
#
# In addition, as a special exception, the copyright holders give
# permission to link the code of portions of this program with the OpenSSL
# library.
#
# You must obey the GNU General Public License in all respects for all of
# the code used other than OpenSSL. If you modify file(s) with this
# exception, you may extend this exception to your version of the file(s),
# but you are not obligated to do so. If you do not wish to do so, delete
# this exception statement from your version. If you delete this exception
# statement from all source files in the program, then also delete it here.

import os
import logging

from miro import httpclient
from fasttypes import LinkedList
from miro import eventloop
from miro.database import DDBObject, ObjectNotFoundError
from miro.download_utils import nextFreeFilename, getFileURLPath
from miro.util import unicodify
from miro.plat.utils import unicodeToFilename
from miro import config
from miro import prefs
from miro import fileutil
import random

RUNNING_MAX = 3

class IconCacheUpdater:
    def __init__(self):
        self.idle = LinkedList()
        self.vital = LinkedList()
        self.runningCount = 0
        self.inShutdown = False

    @eventloop.asIdle
    def request_update(self, item, is_vital=False):
        if is_vital:
            item.dbItem.confirm_db_thread()
            if (item.filename and fileutil.access(item.filename, os.R_OK)
                   and item.url == item.dbItem.get_thumbnail_url()):
                is_vital = False
        if self.runningCount < RUNNING_MAX:
            eventloop.addIdle(item.request_icon, "Icon Request")
            self.runningCount += 1
        else:
            if is_vital:
                self.vital.prepend(item)
            else:
                self.idle.prepend(item)

    def update_finished(self):
        if self.inShutdown:
            self.runningCount -= 1
            return

        if len(self.vital) > 0:
            item = self.vital.pop()
        elif len(self.idle) > 0:
            item = self.idle.pop()
        else:
            self.runningCount -= 1
            return

        eventloop.addIdle(item.request_icon, "Icon Request")

    @eventloop.asIdle
    def clear_vital(self):
        self.vital = LinkedList()

    @eventloop.asIdle
    def shutdown(self):
        self.inShutdown = True

iconCacheUpdater = IconCacheUpdater()
class IconCache(DDBObject):
    def setup_new(self, dbItem):
        self.etag = None
        self.modified = None
        self.filename = None
        self.url = None

        self.updating = False
        self.needsUpdate = False
        self.dbItem = dbItem
        self.removed = False

        self.request_update(is_vital=dbItem.ICON_CACHE_VITAL)

    @classmethod
    def orphaned_view(cls):
        """Downloaders with no items associated with them."""
        return cls.make_view("id NOT IN (SELECT icon_cache_id from item "
                "UNION select icon_cache_id from channel_guide "
                "UNION select icon_cache_id from feed)")

    @classmethod
    def all_filenames(cls):
        return [r[0] for r in cls.select("filename", 'filename IS NOT NULL')]

    def icon_changed(self, needsSave=True):
        try:
            self.dbItem.icon_changed(needsSave=needsSave)
        except (SystemExit, KeyboardInterrupt):
            raise
        except:
            # FIXME - bad code; what exceptions get thrown here?
            self.dbItem.signal_change(needsSave=needsSave)

    def remove(self):
        self.removed = True
        if self.filename:
            self.remove_file(self.filename)
        DDBObject.remove(self)

    def reset(self):
        if self.filename:
            self.remove_file(self.filename)
        self.filename = None
        self.url = None
        self.etag = None
        self.modified = None
        self.removed = False
        self.updating = False
        self.needsUpdate = False
        self.icon_changed()

    def remove_file(self, filename):
        try:
            fileutil.remove(filename)
        except OSError:
            pass

    def error_callback(self, url, error=None):
        self.dbItem.confirm_db_thread()

        if self.removed:
            iconCacheUpdater.update_finished()
            return

        # Don't clear the cache on an error.
        if self.url != url:
            self.url = url
            self.etag = None
            self.modified = None
            self.icon_changed()
        self.updating = False
        if self.needsUpdate:
            self.needsUpdate = False
            self.request_update(True)
        elif error is not None:
            eventloop.addTimeout(3600, self.request_update, "Thumbnail request for %s" % url)
        iconCacheUpdater.update_finished()

    def update_icon_cache(self, url, info):
        self.dbItem.confirm_db_thread()

        if self.removed:
            iconCacheUpdater.update_finished()
            return

        needsSave = False
        needsChange = False

        if info == None or (info['status'] != 304 and info['status'] != 200):
            self.error_callback(url, "bad response")
            return
        try:
            # Our cache is good.  Hooray!
            if info['status'] == 304:
                return

            needsChange = True

            # We have to update it, and if we can't write to the file, we
            # should pick a new filename.
            if self.filename and not fileutil.access(self.filename, os.R_OK | os.W_OK):
                self.filename = None

            cachedir = config.get(prefs.ICON_CACHE_DIRECTORY)
            try:
                fileutil.makedirs(cachedir)
            except OSError:
                pass

            try:
                # Write to a temp file.
                if self.filename:
                    tmp_filename = self.filename + ".part"
                else:
                    tmp_filename = os.path.join(cachedir, info["filename"]) + ".part"

                tmp_filename = nextFreeFilename(tmp_filename)
                output = fileutil.open_file(tmp_filename, 'wb')
                output.write(info["body"])
                output.close()
            except IOError:
                self.remove_file(tmp_filename)
                return

            if self.filename:
                self.remove_file(self.filename)

            # Create a new filename always to avoid browser caching in case a
            # file changes.
            # Add a random unique id
            parts = unicodify(info["filename"]).split('.')
            uid = u"%08d" % random.randint(0, 99999999)
            if len(parts) == 1:
                parts.append(uid)
            else:
                parts[-1:-1] = [uid]
            self.filename = u'.'.join(parts)
            self.filename = unicodeToFilename(self.filename, cachedir)
            self.filename = os.path.join(cachedir, self.filename)
            self.filename = nextFreeFilename(self.filename)
            needsSave = True

            try:
                fileutil.rename(tmp_filename, self.filename)
            except OSError:
                self.filename = None
                needsSave = True

            etag = unicodify(info.get("etag"))
            modified = unicodify(info.get("modified"))

            if self.etag != etag:
                needsSave = True
                self.etag = etag
            if self.modified != modified:
                needsSave = True
                self.modified = modified
            if self.url != url:
                needsSave = True
                self.url = url
        finally:
            if needsChange:
                self.icon_changed(needsSave=needsSave)
            self.updating = False
            if self.needsUpdate:
                self.needsUpdate = False
                self.request_update(True)
            iconCacheUpdater.update_finished()

    def request_icon(self):
        if self.removed:
            iconCacheUpdater.update_finished()
            return

        self.dbItem.confirm_db_thread()
        if self.updating:
            self.needsUpdate = True
            iconCacheUpdater.update_finished()
            return

        if hasattr(self.dbItem, "get_thumbnail_url"):
            url = self.dbItem.get_thumbnail_url()
        else:
            url = self.url

        # Only verify each icon once per run unless the url changes
        if (url == self.url and self.filename
                and fileutil.access(self.filename, os.R_OK)):
            iconCacheUpdater.update_finished()
            return

        self.updating = True

        # No need to extract the icon again if we already have it.
        if url is None or url.startswith(u"/") or url.startswith(u"file://"):
            self.error_callback(url)
            return

        # Last try, get the icon from HTTP.
        httpclient.grabURL(url, lambda info: self.update_icon_cache(url, info),
                lambda error: self.error_callback(url, error))

    def request_update(self, is_vital=False):
        if hasattr(self, "updating") and hasattr(self, "dbItem"):
            if self.removed:
                return

            iconCacheUpdater.request_update(self, is_vital=is_vital)

    def setup_restored(self):
        self.removed = False
        self.updating = False
        self.needsUpdate = False

    def isValid(self):
        self.dbItem.confirm_db_thread()
        return self.filename and fileutil.exists(self.filename)

    def get_filename(self):
        self.dbItem.confirm_db_thread()
        if self.url and self.url.startswith(u"file://"):
            return getFileURLPath(self.url)
        elif self.url and self.url.startswith(u"/"):
            return unicodeToFilename(self.url)
        else:
            return self.filename

def make_icon_cache(obj):
    if obj.icon_cache_id is not None:
        try:
            icon_cache = IconCache.get_by_id(obj.icon_cache_id)
        except ObjectNotFoundError:
            logging.warn("Icon Cache Not in database for %s (id: %s)" %
                    (obj, obj.icon_cache_id))
        else:
            icon_cache.dbItem = obj
            icon_cache.request_update()
            return icon_cache
    return IconCache(obj)

class IconCacheOwnerMixin(object):
    """Mixin class for objects that own IconCache instances (currently, Feed,
    Item and ChannelGuide).
    """

    def setup_new_icon_cache(self):
        self._icon_cache = IconCache(self)
        self.icon_cache_id = self._icon_cache.id

    # the icon_cache attribute is fetched lazily
    def _icon_cache_getter(self):
        try:
            return self._icon_cache
        except AttributeError:
            self._icon_cache = make_icon_cache(self)
            if self.icon_cache_id != self._icon_cache.id:
                self.icon_cache_id = self._icon_cache.id
                self.signal_change()
            return self._icon_cache
    icon_cache = property(_icon_cache_getter)

    def remove_icon_cache(self):
        if self.icon_cache_id is not None:
            self.icon_cache.remove()
            self._icon_cache = self.icon_cache_id = None
