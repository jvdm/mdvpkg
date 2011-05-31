##
## Copyright (C) 2010-2011 Mandriva S.A <http://www.mandriva.com>
## All rights reserved
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU Lesser General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with this program; if not, write to the Free Software
## Foundation, Inc., or visit: http://www.gnu.org/.
##
##
## Author(s): Eugeni Dodonov <eugeni@mandriva.com>
##            J. Victor Martins <jvdm@mandriva.com>
##
"""UrpmiDB classes."""


import os.path
import subprocess
import re
import pyinotify
import gobject
import logging

import mdvpkg.rpmutils
from mdvpkg.urpmi.media import UrpmiMedia


## Cache states:
# Cache is updated:
STATE_UPDATED = 'state-updated'
# Cache is outdated (configuration file has changed):
STATE_OUTDATED = 'state-outdated'
# Cache is broken (configuration file is missing or broken):
STATE_MISSING_CONFIG = 'state-missing-config'

log = logging.getLogger('mdvpkgd.urpmi')


class UrpmiDB(gobject.GObject):
    """Provide access to the urpmi database of medias and packages."""

    __gsignals__ = {
        'new-package': ( gobject.SIGNAL_RUN_FIRST,
                         gobject.TYPE_NONE,
                         (gobject.TYPE_STRING,) ),
        'cache-outdated': ( gobject.SIGNAL_RUN_FIRST,
                            gobject.TYPE_NONE,
                            () ),
    }

    def __init__(self, 
                 conf_dir='/etc/urpmi',
                 data_dir='/var/lib/urpmi',
                 conf_file='urpmi.cfg',
                 rpmdb_path=None):
        gobject.GObject.__init__(self)
        self._conf_dir = os.path.abspath(conf_dir)
        self._data_dir = os.path.abspath(data_dir)
        self._conf_path = '%s/%s' % (self._conf_dir, conf_file)
        if rpmdb_path is None:
            self.rpmdb_option = ''
        else:
            self.rpmdb_option = '--dbpath %s' % rpmdb_path

        ## Cache data and state ...
        self._cache_state = STATE_OUTDATED
        self._cache = {}  # package cache with data read from medias
        self._groups = {}  # list of package groups found in cache

        ## Set up inotify for changes in configuration file, use
        ## gobject.io_add_watch() for new inotify events ...
        wm = pyinotify.WatchManager()
        mask = (pyinotify.IN_DELETE
                    | pyinotify.IN_DELETE_SELF
                    | pyinotify.IN_MODIFY
                    | pyinotify.IN_MOVE_SELF)
        self.ino_watch = wm.add_watch(self._conf_dir, mask)
        self.ino_notifier \
            = pyinotify.Notifier(wm, self._conf_dir_ino_handler)
        # FIXME Should we handle error conditions in the inotify file
        #       descriptor?
        gobject.io_add_watch(wm.get_fd(),
                             gobject.IO_IN,
                             self._ino_in_callback)

    @property
    def cache_state(self):
        """Package cache state."""
        return self._cache_state

    @cache_state.setter
    def cache_state(self, value):
        if value == STATE_OUTDATED:
            self.emit('cache-outdated')
        self._cache_state = value

    def list_medias(self):
        """Visit configuration file, locate and yield all configured
        medias.
        """
        media_r = re.compile('^(.*) {([\s\S]*?)\s*}', re.MULTILINE)
        ignore_r = re.compile('.*(ignore).*')
        update_r = re.compile('.*(update).*')
        key_r = re.compile('.*key-ids:\s* (.*).*')
        url_r = re.compile('(.*) (.*://.*|/.*$)')
        log.debug('reading %s to list medias', self._conf_path)
        with open(self._conf_path, 'r') as fd:
            data = fd.read()
            res = media_r.findall(data)
            for media, values in res:
                res2 = url_r.findall(media)
                if res2:
                    # found a media with url, fixing:
                    name, url = res2[0]
                    media = name
                media = media.replace('\\', '')
                media = media.strip()
                key = ''
                ignore = False
                update = False
                keys = key_r.findall(values)
                if keys:
                    key = keys[0]
                if ignore_r.search(values):
                    ignore = True
                if update_r.search(values):
                    update = True
                yield UrpmiMedia(media, update, ignore,
                                 data_dir=self._data_dir,
                                 key=key)

    def list_packages(self):
        """Iteration over all packages entries in the database.
        
        Populate the package cache first if it's outdated.
        """
        log.info('listing packages.')
        self._check_cache_state()
        return self._cache.itervalues()

    def list_groups(self):
        """Iteration over all package groups in the database."""
        log.info('listing groups.')
        self._check_cache_state()
        return self._groups.iteritems()

    def _check_cache_state(self):
        if self.cache_state == STATE_OUTDATED:
            log.debug('cache is outdated, updating cache.')
            self._update_cache()
        elif self.cache_state == STATE_MISSING_CONFIG:
            # FIXME Is this the best way to handle it?
            raise Exception, 'urpmi configuration was deleted'

    def _update_cache(self):
        """Loads package data from urpmi database to the package cache."""
        # forget previous data:
        self.cache_state = STATE_OUTDATED
        old_cache, self._cache = self._cache, {}
        self._groups = {}

        ## Load installed packages ...
        log.info('reading installed packages.')
        rpm_command = "rpm %s -qa --qf '%s'" \
            % (self.rpmdb_option,
               '%{NAME}@%{VERSION}@%{RELEASE}@%{ARCH}@%|EPOCH?{%{EPOCH}}:{0}|'
               '@%{SIZE}@%{GROUP}@%{SUMMARY}@%{INSTALLTIME}\\n')
        rpm_p = subprocess.Popen(rpm_command,
                                 stdout=subprocess.PIPE,
                                 stdin=None,
                                 shell=True)
        for line in rpm_p.stdout:
            package_data = dict(zip(['name', 'version', 'release', 
                                     'arch', 'epoch', 'size', 
                                     'group', 'summary', 'install_time'],
                                    line.split('@')))
            if not package_data['install_time']:
                log.error('package installed without INSTALLTIME: %s',
                          package_data['name'])
            self._on_package_data(package_data)
        rpm_p.wait()

        ## Load packages from non-ignored medias ...        
        log.info('reading packages from medias.')
        for media in [ m for m in self.list_medias() if not m.ignore ]:
            for package_data in media.list():
                package_data['media'] = media.name
                self._on_package_data(package_data)

        ## Compare new packages in the cache with the old ones ...
        for name in self._cache.iterkeys():
            if name not in old_cache:
                self.emit('new-package', name)
        while True:
            try:
                name, old_entry = old_cache.popitem()
            except KeyError:
                break
            else:
                new_entry = self._cache.pop(name, None)
                if new_entry is None:
                    old_entry.emit('deleted')
                else:
                    old_entry.update(new_entry)
                    self._cache[name] = old_entry
                    old_entry.emit('updated')

        self.cache_state = STATE_UPDATED
        log.info('package cache updated.')

    def _on_package_data(self, package_data):
        """Handle package data found during cache update.

        Add the package information to the package cache, updating or
        creating entries.
        """

        # FIXME It's possible that two packages with same VR exists
        #       from different media, we assume that it won't happen.

        pkg = UrpmiPackage(package_data)

        ## Add group data ...

        # FIXME How to signal updates in group information? Clients
        #       may benefit from this to update interfaces.
        if pkg.group not in self._groups:
            self._groups[pkg.group] = 1
        else:
            self._groups[pkg.group] += 1

        ## Update cache entry ...
        entry = self._cache.get(pkg.name)
        if entry is None:
            # create new cache entry:
            entry = PackageCacheEntry(pkg.name)
            self._cache[pkg.name] = entry

        installed = entry.installs.get(pkg.vr)
        if installed is not None:
            if pkg.install_time:
                log.error('found two installed versions of '
                          'the same package: %s',
                          installed.name)
            if installed.media:
                log.warning('found same version of package %s in two '
                            'different medias: %s and %s.',
                            installed.name,
                            installed.media,
                            pkg.media)
            installed.media = pkg.media
        else:
            if not pkg.install_time:
                ## Check if upgrades or downgrades the higher installed
                ## version ...
                if entry.latest_installed is None \
                        or pkg > entry.latest_installed:
                    entry.upgrades[pkg.vr] = pkg
                else:
                    entry.downgrades[pkg.vr] = pkg
            else:
                entry.installs[pkg.vr] = pkg

    def _conf_dir_ino_handler(self, event):
        """Configuration directory ionotify event handler."""
        log.debug('changes in config dir: %s, %s',
                  event.maskname,
                  event.pathname)
        # currently only watching the configuration file:
        if event.pathname == self._conf_path:
            if event.mask & (pyinotify.IN_MODIFY):
                log.info('urpmi configuration has changed.')
                self.cache_state = STATE_OUTDATED
            elif event.mask & (pyinotify.IN_DELETE
                                   | pyinotify.IN_DELETE_SELF
                                   | pyinotify.IN_MOVE_SELF):
                log.info('urpmi configuration has been removed.')
                self.cache_state = STATE_BROKEN
            else:
                log.warning('ignored inotify event in urpmi configuration '
                            'file: %s',
                            event.eventmaskname)

    def _ino_in_callback(self, fd, condition):
        """Inotify gobject io_watch callback."""
        self.ino_notifier.read_events()
        self.ino_notifier.process_events()
        return True
        

class PackageCacheEntry(gobject.GObject):
    """Represent a package in the urpmi database cache."""

    __gsignals__ = {
        'deleted': (
            gobject.SIGNAL_RUN_FIRST,
            gobject.TYPE_NONE,
            ()
        ),
        'updated': (
            gobject.SIGNAL_RUN_FIRST,
            gobject.TYPE_NONE,
            ()
        ),
    }

    def __init__(self, name):
        gobject.GObject.__init__(self)
        self.name = name
        self.installs = {}
        self.upgrades = {}
        self.downgrades = {}

    def update(self, other_entry):
        """Update us to reflect package information from another
        entry.

        After this the two entries will essencially provide the same
        package information.
        """
        if self.name != other_entry.name:
            raise ValueError, ( 'updating entries for different '
                                'packages: %s, %s'
                                % (self.name, other_entry.name) )
        self.installs = other_entry.installs
        self.upgrades = other_entry.upgrades
        self.downgrades = other_entry.downgrades

    @property
    def status(self):
        """Package entry status."""
        if self.installs:
            if self.upgrades:
                return 'upgrade'
            return 'installed'
        return 'new'

    @property
    def latest_installed(self):
        if not self.installs:
            return None
        return sorted(self.installs.values())[-1]

    @property
    def latest_upgrade(self):
        if not self.upgrades:
            return None
        return sorted(self.upgrades.values())[-1]

    @property
    def latest(self):
        """The latest package in the entry based."""
        if self.status in {'new', 'upgrade'}:
            return self.latest_upgrade
        else:
            return self.latest_installed

    def has_version(self, vr):
        return vr not in self.installs \
               or vr not in self.upgrades \
               or vr not in self.downgrades

    def __contains__(self, item):
        return self.has_version(item)

    def __repr__(self):
        return '%s(%s:%s)' % (self.__class__.__name__,
                              self.name,
                              id(self))

class UrpmiPackage(object):
    """A package in the rpm/urpmi database."""

    def __init__(self, data):
        self.name = data['name']
        self.version = data['version']
        self.release = data['release']
        self.arch = data['arch']
        self.epoch = data['epoch']
        self.size = data['size']
        self.group = data['group']
        self.summary = data['summary']
        self.media = data.get('media', '')
        self.install_time = data.get('install_time', 0)
        # FIXME Currently installed packages won't come with
        #       capabilities information:
        self.requires = data.get('requires', [])
        self.provides = data.get('provides', [])
        self.conflict = data.get('conflict', [])
        self.obsoletes = data.get('obsoletes', [])

    @property
    def installed(self):
        """True if rpm is installed."""
        return self.install_time != 0

    @property
    def vr(self):
        """Package Version-Release: identifies a specific package
        version.
        """
        return (self.version, self.release)

    @property
    def nvra(self):
        """Package Name-Version-Release-Arch: identifies uniquely the
        package.
        """
        return (self.name, self.version, self.release, self.arch)

    def __eq__(self, other):
        return self.nvra == other.nvra

    def __ne__(self, other):
        return not self.__eq__(other)

    def __le__(self, other):
        if self == other:
            return True
        else:
            return NotImplemented

    def __ge__(self, other):
        return self.__le__(other)

    def __cmp__(self, other):
        if self.name != other.name:
            raise ValueError('Name mismatch %s != %s'
                             % (self.name, other.name))
        if self.epoch > other.epoch:
            return 1
        elif self.epoch < other.epoch:
            return -1
        cmp = mdvpkg.rpmutils.rpmvercmp(self.version, other.version)
        if cmp == 0:
            cmp = mdvpkg.rpmutils.rpmvercmp(self.release, other.release)
        return cmp

    def __str__(self):
        return '%s-%s-%s.%s' % self.nvra

    def __repr__(self):
        return '%s(%s:%s)' % (self.__class__.__name__,
                              self,
                              self.epoch)
