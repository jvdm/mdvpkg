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
"""Class to represent and manipulate the urpmi db."""


import os.path
import subprocess
import re
import pyinotify
import gobject
import logging
import rpm
import logging
import os.path

import mdvpkg
import mdvpkg.urpmi.task
from mdvpkg.urpmi.media import UrpmiMedia
from mdvpkg.urpmi.packages import RpmPackage
from mdvpkg.urpmi.packages import Package


log = logging.getLogger('mdvpkgd.urpmi')


class UrpmiDB(object):
    """Provide access to the urpmi database of medias and packages."""

    def __init__(self, 
                 conf_dir='/etc/urpmi',
                 data_dir='/var/lib/urpmi',
                 conf_file='urpmi.cfg',
                 rpm_dbpath=None,
                 backend_dir=None):
        self._conf_dir = os.path.abspath(conf_dir)
        self._data_dir = os.path.abspath(data_dir)
        self._conf_path = '%s/%s' % (self._conf_dir, conf_file)
        self._rpm_dbpath = rpm_dbpath
        if backend_dir is None:
            self.backend_dir = mdvpkg.DEFAULT_BACKEND_DIR
        else:
            self.backend_dir = backend_dir
        self._cache = {}  # package cache with data read from medias
        self._medias = {}

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
        self._runner = mdvpkg.urpmi.task.UrpmiRunner(self.backend_dir)
        self._signals = {'download-start': [],
                         'download-progress': [],
                         'download-error': [],
                         'install-start': [],
                         'install-progress': [],
                         'preparing': []}
        self._signals_callbacks = {}

    def emit(self, signal_name, *args, **kwargs):
        """Emit a signal calling all callbacks."""
        for handler in self._signals[signal_name]:
            callback, _ = self._signals_callbacks[handler]
            callback(*args, **kwargs)

    def connect(self, signal_name, callback):
        """Connect a callback to a signal."""
        conn_tuple = (callback, signal_name)
        handler = hash(conn_tuple)
        self._signals_callbacks[handler] = conn_tuple
        self._signals[signal_name].append(handler)
        return handler

    def disconnect(self, handler_id):
        """Disconnect a signal callback."""
        _, s_name = self._signals_callbacks.pop(handler_id)
        self._signals[s_name].remove(handler_id)

    def configure_medias(self):
        """Read configuration file, locate and populate the list of
        configured medias.
        """
        media_r = re.compile('^(.*) {([\s\S]*?)\s*}', re.MULTILINE)
        ignore_r = re.compile('.*(ignore).*')
        update_r = re.compile('.*(update).*')
        key_r = re.compile('.*key-ids:\s* (.*).*')
        url_r = re.compile('(.*) (.*://.*|/.*$)')
        log.debug('reading %s to list medias', self._conf_path)
        self._medias = {}
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
                media = UrpmiMedia(media, update, ignore,
                                   data_dir=self._data_dir,
                                   key=key)
                self._medias[media.name] = media

    def list_active_medias(self):
        """Return a list of active configured medias objects."""
        return filter(lambda media: media.ignore is False,
                      self._medias.values())

    def load_packages(self):
        """Load package information from rpmdb and active medias and
        generate the package cache.
        """
        self._load_installed_packages()
        self._load_active_media_packages()

    def get_package(self, name_arch):
        return self._cache[name_arch]

    def list_packages(self):
        """Iteration over all packages entries in the database.
        """
        return self._cache.itervalues()

    def resolve_install_deps(self, name_arch):
        """Resolve all install deps to install package and return a
        dictionary of actions.
        """

        selected = {'action-install': [],
                    'action-auto-install': []}
        backend = subprocess.Popen(os.path.join(self.backend_dir,
                                                'resolve.pl'),
                                   shell=True,
                                   stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE)
        # ATTENTION: We're using RpmPackage.__str__() as argument to
        #            the backend.
        backend.stdin.write('%s\n' % self._cache[name_arch].latest_upgrade)
        for line in backend.communicate()[0].split('\n'):
            fields = line.split()
            if fields and fields[0] == '%MDVPKG':
                if fields[1] == 'ERROR':
                    raise Exception,'Backend error: %s' % fields[2]
                elif fields[1] == 'SELECTED':
                    selected[fields[2]].append(tuple(fields[3].split('@')))
        return selected

    def auto_select(self):
        """Resolve all install deps to install all upgradable packages
        and return a dictionary of actions.
        """
        raise NotImplementedError

    def run_task(self, install=[], remove=[]):
        """Create task to install names, a list of (name, arch)
        tuples.
        """
        remove_names \
            = ['%s' % self._cache[na].name for na in remove]
        install_names \
            = ['%s' % self._cache[na].latest_upgrade for na in install]
        remove_task = mdvpkg.urpmi.task.create_task(
                           self,
                           mdvpkg.urpmi.task.ROLE_REMOVE,
                           remove_names
                      )
        install_task = mdvpkg.urpmi.task.create_task(
                           self,
                           mdvpkg.urpmi.task.ROLE_INSTALL,
                           install_names
                       )
        # self._runner.push(remove_task)
        self._runner.push(self,
                          mdvpkg.urpmi.task.ROLE_INSTALL,
                          (install_names,))

    def _load_installed_packages(self):
        """Visit rpmdb and load data from installed packages."""
        log.info('reading installed packages.')
        if self._rpm_dbpath is not None:
            rpm.addMacro('_dbpath', self._rpm_dbpath)
        for pkg in rpm.ts().dbMatch():
            # TODO Load capabilities information in the same manner
            #      Media.list_medias() will return.
            pkgdict = {}
            for attr in ('name', 'version', 'release', 'arch', 'epoch',
                         'size', 'group', 'summary', 'installtime',
                         'disttag', 'distepoch'):
                value = pkg[attr]
                if type(value) is list and len(value) == 0:
                    value = ''
                pkgdict[attr] = value

            if type(pkg['installtime']) is list:
                pkgdict['installtime'] = pkg['installtime'][0]
            if pkgdict['epoch'] is None:
                pkgdict['epoch'] = 0
            self._on_package_data(pkgdict)
        rpm.delMacro('_dbpath')

    def _load_active_media_packages(self):
        """Load packages from active medias."""
        log.info('reading packages from active medias.')
        for media in self.list_active_medias():
            for package_data in media.list():
                package_data['media'] = media.name
                self._on_package_data(package_data)

    def _on_package_data(self, package_data):
        """Handle package data found during cache update.

        Add the package information to the package cache, updating or
        creating entries.
        """

        # FIXME It's possible that two packages with same VR exists
        #       from different media, we assume that it won't happen.

        rpm = RpmPackage(package_data)

        ## Update package names ...
        pkgname = self._cache.get(rpm.na)
        if pkgname is None:
            # create new pkgname:
            pkgname = Package(rpm.na, self)
            self._cache[pkgname.na] = pkgname
        pkgname.add_version(rpm)

    def _conf_dir_ino_handler(self, event):
        """Configuration directory ionotify event handler."""
        log.debug('changes in config dir: %s, %s',
                  event.maskname,
                  event.pathname)
        # currently only watching the configuration file:
        if event.pathname == self._conf_path:
            if event.mask & (pyinotify.IN_MODIFY):
                self._on_configuration_changed()
            elif event.mask & (pyinotify.IN_DELETE
                                   | pyinotify.IN_DELETE_SELF
                                   | pyinotify.IN_MOVE_SELF):
                self._on_configuration_deleted()
            else:
                log.warning('ignored inotify event in urpmi configuration '
                            'file: %s',
                            event.eventmaskname)

    def _on_configuration_changed(self):
        log.info('urpmi configuration has changed.')
        self.configure_medias()

    def _on_configuration_deleted(self):
        log.info('urpmi configuration has been removed.')
        self._medias = {}

    def _ino_in_callback(self, fd, condition):
        """Inotify gobject io_watch callback."""
        self.ino_notifier.read_events()
        self.ino_notifier.process_events()
        return True

    def on_task_queued(self, task_id):
        pass

    def on_task_running(self, task_id):
        pass

    def on_task_done(self):
        pass

    def on_task_error(self, message):
        log.debug('task error: %s', message)

    def on_task_exception(self, message):
        log.debug('task exception: %s', message)

    def on_download_start(self, name, arch):
        package = self._cache[(name, arch)]
        self.emit('download-start', package)

    def on_download_progress(self, name, arch, percent, total, eta, speed):
        package = self._cache[(name, arch)]
        self.emit('download-progress', package, percent, total, eta, speed)

    def on_download_error(self, name, arch, message):
        package = self._cache[(name, arch)]
        self.emit('download-error', package, message)

    def on_preparing(self, total):
        self.emit('preparing', total)

    def on_install_start(self, name, arch, total, count):
        package = self._cache[(name, arch)]
        self.emit('install-start', package, total, count)

    def on_install_progress(self, name, arch, amount, total):
        package = self._cache[(name, arch)]
        self.emit('install-progress', package, amount, total)


ACTION_NO_ACTION = 'action-no-action'
ACTION_INSTALL = 'action-install'
ACTION_AUTO_INSTALL = 'action-auto-install'
ACTION_REMOVE = 'action-remove'
ACTION_AUTO_REMOVE = 'action-auto-remove'


class PackageList(object):
    """Represent the list of packages in the rpm/urpmi database."""

    def __init__(self, urpmi):
        self._urpmi = urpmi
        self._items = {}
        self._names = []
        self._filters = {}
        self.filter_names = {'name', 'group','status', 'media', 'action'}
        self._reverse = False
        # urpmi transaction to perform actions ...
        self._transaction = None
        # Connect urpmi signals ...
        self._handlers = []

    def __len__(self):
        return len(self._names)

    def __getitem__(self, index):
        return self.get(index)

    def load(self):
        """Initialize the list."""
        # Load package data from urpmi ...
        for pkgname in self._urpmi.list_packages():
            self._items[pkgname.na] = {
                'action': ACTION_NO_ACTION,
                'sort_key': None,
            }
            self._names.append(pkgname.na)
        # Connect to urpmi db signals ...
        for signal, callback in \
                {'download-start': self._on_download_start,
                 'download-progress': self._on_download_progress,
                 'download-error': self._on_download_progress,
                 'install-start': self._on_install_start,
                 'install-progress': self._on_install_progress,
                 'preparing': self._on_preparing}.iteritems():
            handler = self._urpmi.connect(signal, callback)
            self._handlers.append(handler)

    def delete(self):
        """Clean up the list."""
        self._names = []
        self._items = {}
        self._filters = {}
        # Disconnect urpmi signals ...
        while True:
            try:
                handler = self._handlers.pop(0)
            except IndexError:
                break
            else:
                self._urpmi.disconnect(handler)

    def sort(self, key_name, reverse=False):
        """Sort the list of packages using key_name as key."""
        for na in self._items.iterkeys():
            item = self._items[na]
            package = self._urpmi.get_package(na)
            # Get the sort key value for an item in the list ...
            if key_name in {'status'}:
                key = package.status
            elif key_name in {'action'}:
                key = item['action']
            else:
                key = getattr(package.latest, key_name)
            self._items[na]['sort_key'] = key
        self._reverse = reverse
        self._sort_and_filter()

    def get(self, index):
        na = self._names[index]
        package = self._urpmi.get_package(na)
        item = self._items[na]
        return_dict = {'status': package.status,
                       'action': item['action'],
                       'name': package.name,
                       'arch': package.arch,
                       'rpm': package.latest}
        return return_dict

    def install(self, index):
        """Select a package for installation and all it's dependencies."""
        for action, names in self._urpmi.resolve_install_deps(
                                 self._names[index]
                             ).iteritems():
            for na in names:
                log.debug('action changed for %s: %s', na, action)
                self._items[na]['action'] = action

    def process_actions(self):
        """Process the selected actions and their dependencies.
        """
        installs = []
        removes = []
        for na, item in self._items.iteritems():
            if item['action'] == ACTION_INSTALL:
                installs.append(na)
            elif item['action'] == ACTION_REMOVE:
                removes.append(na)

        # TODO Add method for removing packages ...

        self._urpmi.run_task(install=installs, remove=removes)

    def get_groups(self):
        """Return the dict of package groups and package count in
        the filtered list.
        """
        return self._count_groups(self._names)

    def get_all_groups(self):
        """Return the dict of packages groups and package count in the
        unfiltered list.
        """
        return self._count_groups(self._items.iterkeys())

    def _count_groups(self, na_iter):
        groups_dict = {}
        for na in na_iter:
            group = self._urpmi.get_package(na).latest.group
            count = groups_dict.get(group)
            if count is None:
                groups_dict[group] = 1
            else:
                groups_dict[group] += 1
        return groups_dict

    def __getattr__(self, name):
        """Look for filter calls (self.filter_NAME) or ignore."""
        prefix, filter_name = name.split('_', 1)
        if prefix == 'filter' and filter_name in self.filter_names:
            def set_filter(include, exclude):
                self._set_filter(filter_name, include, exclude)
                self._sort_and_filter()
            return set_filter
        raise AttributeError, "'%s' object has no attribute '%s'" \
                              % (self.__class__.__name__, name)

    def _set_filter(self, name, include, exclude):
        filter = self._filters.pop(name, {})
        if include:
            filter[False] = set(include)
        else:
            filter.pop(False, None)
        if exclude:
            filter[True] = set(exclude)
        else:
            filter.pop(True, None)
        if filter:
            self._filters[name] = filter

    def _sort_and_filter(self):
        """Sort and filter the key list."""
        self._names = []
        for na in self._items:
            if self._filter(na):
                self._names.append(na)
        self._names.sort(key=lambda na: self._items[na]['sort_key'],
                         reverse=self._reverse)

    def _filter(self, na):
        for filter_name, sets in self._filters.iteritems():
            for exclude in sets:
                matches = self._filters[filter_name][exclude]
                match_func = getattr(self,
                                     '_%s_match_func' % filter_name)
                if not exclude ^ match_func(na, matches):
                    return False
        return True

    def _name_match_func(self, na, matches):
        for name in matches:
            if self._urpmi.get_package(na).name.startswith(name):
                return True
        return False

    def _status_match_func(self, na, matches):
        return self._urpmi.get_package(na).status in matches

    def _group_match_func(self, na, matches):
        folders = self._urpmi.get_package(na).latest.group.split('/')
        for i in range(1, len(folders) + 1):
            if '/'.join(folders[:i]) in matches:
                return True
        return False

    def _media_match_func(self, na, matches):
        return self._urpmi.get_package(na).latest.media in matches

    def _action_match_func(self, na, matches):
        return self._items[na]['action'] in matches

    #
    # Signal callbacks
    #

    def _on_download_start(self, package):
        pass

    def _on_download_progress(self, package, percent, total, eta, speed):
        pass

    def _on_download_error(self, package, message):
        pass

    def _on_install_start(self, package, total, count):
        pass

    def _on_install_progress(self, package, amount, total):
        if amount == total:
            self._items[package.na]['action'] = ACTION_NO_ACTION

    def _on_preparing(self, total):
        pass
