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


import os
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
import mdvpkg.exceptions
from mdvpkg.urpmi.media import UrpmiMedia
from mdvpkg.urpmi.packages import RpmPackage
from mdvpkg.urpmi.packages import Package


log = logging.getLogger('mdvpkgd.urpmi')


def expand_line(line):
    """Look for $HOST, $ARCH and $RELEASE to expand."""
    _, host, _, _, arch = os.uname()
    release = ''
    with open('/etc/release') as f:
        m = re.search('release (\d+\.\d+).*for (\w+)', f.read())
        if m is not None:
            release, _arch = m.groups()
            if _arch:
                arch = _arch
            if re.match('cooker', release):
                release = 'cooker'
    for name, value in {'HOST': host,
                        'ARCH': arch,
                        'RELEASE': release}.items():
        line = line.replace('$%s' % name, value)
    return line


def parse_configuration(conf_path):
    """Parse urpmi configuration file at conf_path and return its data
    in a tuple."""
    with open(conf_path, 'r') as conf_file:
        lines = conf_file.readlines()
    medias = []
    temp_block = None
    global_block = None
    for line in lines:
        line = line.strip()
        if not line or re.match('\s*#', line):
            continue
        line = expand_line(line)
        if temp_block is None:
            m = re.search('^(.*?[^\\\])\s+(?:(.*?[^\\\])\s+)?{$', line)
            if m is not None:
                name, url = m.groups()
                name = re.sub('\\\(\s)', '\\1', name)
                if name in [media['name'] for media in medias]:
                    msg = 'configuration file: duplicated ' \
                          'definition: %s' % name
                    raise ValueError, msg
                temp_block = {'name': name}
                if url:
                    temp_block['url'] = url
            elif line == '{':
                if global_block is not None:
                    # found two global blocks:
                    raise Exception, 'syntax error'
                temp_block = {'name': None}  # global block -> name = None
        else:
            if line.endswith('{'):
                # nested block definition:
                raise Exception, 'syntax error'
            if line.endswith('}'):
                if temp_block['name'] is None:
                    del temp_block['name']
                    global_block = temp_block
                else:
                    medias.append(temp_block)
                temp_block = None
                continue

            # Ignored, kept for compatibility ...
            if line in {'modified', 'hdlist', 'synthesis'}:
                continue

            # Check for key-ids ...
            m = re.match('^key[-_]ids\s*:\s*[\'"]?(.*?)[\'"]?$', line)
            if m is not None:
                temp_block['key-ids'] = m.group(1)
                continue

            # Positive only fields ...
            m = re.match('(update|ignore|synthesis|noreconfigure'
                             '|no-suggests|no-media-info|static'
                             '|virtual|disable-certificate-check)',
                         line)
            if m is not None:
                temp_block[m.group(1)] = True
                continue

            m = re.match('^(hdlist|list|with_hdlist|with_synthesis'
                             '|with-dir|mirrorlist|media_info_dir'
                             '|removable|md5sum|limit-rate'
                             '|nb-of-new-unrequested-pkgs-between'
                             '-auto-select-orphans-check|xml-info'
                             '|excludepath|split-(?:level|length)'
                             '|priority-upgrade|prohibit-remove'
                             '|downloader|retry|default-media'
                             '|(?:curl|rsync|wget|prozilla|aria2)'
                             '-options)\s*:\s*[\'"]?(.*?)[\'"]?$',
                         line)
            if m:
                temp_block[m.group(1)] = m.group(2)
                continue

            m = re.match('^(no-)?(verify-rpm|norebuild|fuzzy'
                         '|allow-(?:force|nodeps)'
                         '|(?:pre|post)-clean|excludedocs|compress'
                         '|keep|ignoresize|auto|repackage'
                         '|strict-arch|nopubkey|resume)'
                         '(?:\s*:\s*(.*))?$',
                         line)
            if m is not None:
                no, key, value = m.groups()
                if value in {'yes', 'on', '1'}:
                    temp_block[key] = not bool(no)
                else:
                    temp_block[key] = bool(no)
                continue

            # unknown flag or property
            raise Exception, 'syntax error: %s' % line
    return global_block, medias


class UrpmiDB(mdvpkg.ConnectableObject):
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
        self._medias = None
        self._conf = None

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
        super(UrpmiDB, self).__init__(signals=['download-start',
                                               'download-progress',
                                               'download-end',
                                               'download-error',
                                               'install-start',
                                               'install-progress',
                                               'remove-start',
                                               'remove-progress',
                                               'preparing',
                                               'package-changed',
                                               'task-queued',
                                               'task-running',
                                               'task-progress',
                                               'task-done'])

    def configure_medias(self):
        """Read configuration file, locate and populate the list of
        configured medias.
        """
        log.debug('reading %s to list medias', self._conf_path)
        self._config, media_blocks = parse_configuration(self._conf_path)
        self._medias = {}
        for media in media_blocks:
            self._medias[media['name']] \
                = UrpmiMedia(media['name'],
                             media,
                             data_dir=self._data_dir)

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

    def resolve_deps(self, installs=[], removes=[]):
        """Resolve all install deps to install package and return a
        dictionary of actions.
        """

        selected = {'action-install': [],
                    'action-auto-install': [],
                    'action-remove': [],
                    'action-auto-remove': []}
        rejected = {}
        backend = subprocess.Popen(os.path.join(self.backend_dir,
                                                'resolve.pl'),
                                   shell=True,
                                   stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE)
        # ATTENTION: We're using RpmPackage.__str__() as argument to
        #            the backend.
        args = []
        for install in installs:
            name = '%s' % (self._cache[install].latest_upgrade)
            args.append(name)
        for remove in removes:
            name = '%s' % (self._cache[remove].latest_installed)
            args.append('r:' + name)
        backend.stdin.write('%s\n' % '\t'.join(args))
        for line in backend.communicate()[0].split('\n'):
            if line.startswith('%MDVPKG '):
                fields = line.replace('%MDVPKG ', '', 1).split('\t')
                if fields[0] == 'ERROR':
                    msg = 'Backend error: %s' % ' '.join(fields[1:])
                    raise mdvpkg.exceptions.MdvPkgError, msg
                elif fields[0] == 'SELECTED':
                    action, na_evrd = fields[1:]
                    na, evrd = eval(na_evrd)
                    if self._cache[na].in_progress is not None:
                        raise PackageInProgressConflict
                    selected[action].append((na, evrd))
                elif fields[0] == 'REJECTED':
                    reason, na_evrd = fields[1:3]
                    na, evrd = eval(na_evrd)
                    if reason == 'reject-install-unsatisfied':
                        subjects = fields[3:]
                    elif reason in {'reject-install-conflicts',
                                    'reject-install-rejected-dependency',
                                    'reject-remove-depends'}:
                        subjects = []
                        for na_s, evrd_s in [eval(pt) for pt in fields[3:]]:
                            subjects.append(self._cache[na_s][evrd_s])
                    else:
                        subjects = None
                    rej_list = rejected.get(reason)
                    if rej_list is None:
                        rej_list = []
                        rejected[reason] = rej_list
                    rej = { 'package':
                                self._cache[na],
                            'rpm':
                                self._cache[na][evrd] }
                    if subjects:
                        rej['subjects'] = subjects
                    rej_list.append(rej)
        return selected, rejected

    def auto_select(self):
        """Resolve all install deps to install all upgradable packages
        and return a dictionary of actions.
        """
        raise NotImplementedError

    def run_task(self, install=[], remove=[]):
        """Create task to install names, a list of (name, arch)
        tuples.
        """
        remove_names = []
        for pkg in [self._cache[na] for na in remove]:
            remove_names.append(pkg.latest_installed.__str__())
        install_names = []
        for pkg in [self._cache[na] for na in install]:
            install_names.append(pkg.latest_upgrade.__str__())
        return self._runner.push(self,
                                 mdvpkg.urpmi.task.ROLE_COMMIT,
                                 (install_names,remove_names))

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
        self.emit('task-queued', task_id)

    def on_task_running(self, task_id):
        self.emit('task-running', task_id)

    def on_task_progress(self, task_id, count, total):
        self.emit('task-progress', task_id, count, total)

    def on_task_done(self, task_id):
        self.emit('task-done', task_id)

    def on_task_error(self, task_id, message):
        log.debug('task error: %s', message)

    def on_task_exception(self, task_id, message):
        log.debug('task exception: %s: %s', task_id, message)

    def on_download_start(self, task_id, na_evrd):
        na, evrd = eval(na_evrd)
        package = self._cache[na]
        package.on_download_start(evrd)
        self.emit('download-start', task_id, package)

    def on_download_progress(self, task_id, na_evrd, percent,
                             total, eta, speed):
        na, evrd = eval(na_evrd)
        package = self._cache[na]
        package.on_download_progress(evrd, float(percent) / 100.0)
        self.emit('download-progress',
                  task_id, package, percent, total, eta, speed)

    def on_download_end(self, task_id, na_evrd):
        na, evrd = eval(na_evrd)
        package = self._cache[na]
        package.on_download_done(evrd)
        self.emit('download-end', task_id, package)

    def on_download_error(self, na_evrd, message):
        na, evrd = eval(na_evrd)
        package = self._cache[na]
        package.on_download_done(evrd)
        self.emit('download-error', task_id, package, message)

    def on_preparing(self, task_id, total):
        self.emit('preparing', task_id, total)

    def on_install_start(self, task_id, na_evrd, total, count):
        na, evrd = eval(na_evrd)
        package = self._cache[na]
        package.on_install_start(evrd)
        self.emit('install-start', task_id, package, total, count)

    def on_install_progress(self, task_id, na_evrd, amount, total):
        na, evrd = eval(na_evrd)
        package = self._cache[na]
        package.on_install_progress(evrd, float(amount) / float(total))
        self.emit('install-progress', task_id, package, amount, total)

    def on_install_end(self, task_id, na_evrd):
        na, evrd = eval(na_evrd)
        package = self._cache[na]
        package.on_install_done(evrd)
        self.emit('package-changed')

    def on_remove_start(self, task_id, na_evrd, total, count):
        na, evrd = eval(na_evrd)
        package = self._cache[na]
        package.on_remove_start(evrd)
        self.emit('remove-start', task_id, package, total, count)

    def on_remove_progress(self, task_id, na_evrd, amount, total):
        na, evrd = eval(na_evrd)
        package = self._cache[na]
        package.on_remove_progress(evrd, float(amount) / float(total))
        self.emit('remove-progress', task_id, package, amount, total)

    def on_remove_end(self, task_id, na_evrd):
        na, evrd = eval(na_evrd)
        package = self._cache[na]
        package.on_remove_done(evrd)
        self.emit('package-changed')


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
        self.filter_names = {'name', 'group', 'status', 'media', 'action'}
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
                 'download-error': self._on_download_error,
                 'download-end': self._on_download_end,
                 'install-start': self._on_install_start,
                 'install-progress': self._on_install_progress,
                 'remove-start': self._on_remove_start,
                 'remove-progress': self._on_remove_progress,
                 'package-changed': self._on_package_changed,
                 'preparing': self._on_preparing,}.iteritems():
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
                       'rpm': package.latest,
                       'progress': package.progress}
        return return_dict

    def remove(self, index):
        na = self._names[index]
        pkg = self._urpmi.get_package(na)
        if pkg.has_installs is not True:
            raise ValueError, '%s.%s not installed' % na
        elif pkg.in_progress is not None:
            raise mdvpkg.exceptions.PackageInProgressConflict
        self._items[na]['action'] = ACTION_REMOVE
        return self._solve()

    def install(self, index):
        na = self._names[index]
        pkg = self._urpmi.get_package(na)
        if pkg.status == 'installed':
            raise ValueError, '%s.%s already installed' % na
        elif pkg.in_progress is not None:
            raise mdvpkg.exceptions.PackageInProgressConflict
        self._items[na]['action'] = ACTION_INSTALL
        return self._solve()

    def no_action(self, index):
        na = self._names[index]
        item = self._items[na]
        if item['action'] in {ACTION_AUTO_INSTALL, ACTION_AUTO_REMOVE}:
            msg = 'package is required for action: %s' % item['action']
            raise mdvpkg.exceptions.MdvPkgError, msg
        item['action'] = ACTION_NO_ACTION
        return self._solve()

    def _solve(self):
        """Select all packages with actions, solve dependencies
        updating actions.  Return lists of selections and rejections.
        """
        installs = []
        removes = []
        items_with_actions = []

        for na, item in self._items.iteritems():
            if item['action'] == ACTION_INSTALL:
                installs.append(na)
            elif item['action'] == ACTION_REMOVE:
                removes.append(na)
            if item['action'] != ACTION_NO_ACTION:
                items_with_actions.append(item)

        action_list, reject_list \
            = self._urpmi.resolve_deps(installs=installs,
                                       removes=removes)
        if not reject_list:
            for item in items_with_actions:
                item['action'] = ACTION_NO_ACTION

        installs_rej = []
        removes_rej = []
        for reason, rejects in reject_list.iteritems():
            for reject in rejects:
                if reason.startswith('reject-install-'):
                    installs_rej.append( (reject['rpm'],
                                          reason,
                                          reject.get('subjects', [])) )
                else:
                    removes_rej.append( (reject['rpm'],
                                         reason,
                                         reject.get('subjects', [])) )
        installs_fn = []
        removes_fn = []
        for action, names in action_list.iteritems():
            for na, evrd in names:
                rpm = self._urpmi.get_package(na)[evrd]
                if not reject_list:
                    log.debug('action changed for %s: %s', rpm, action)
                    self._items[na]['action'] = action
                if action in {ACTION_INSTALL, ACTION_AUTO_INSTALL}:
                    installs_fn.append(rpm)
                elif action in {ACTION_REMOVE, ACTION_AUTO_REMOVE}:
                    removes_fn.append(rpm)

        self._sort_and_filter()

        return installs_fn, removes_fn, installs_rej, removes_rej

    def process_actions(self):
        """Process the selected actions and their dependencies.
        """
        installs = []
        auto_installs = []
        removes = []
        auto_removes = []
        for na, item in self._items.iteritems():
            if item['action'] == ACTION_INSTALL:
                installs.append(na)
            elif item['action'] == ACTION_AUTO_INSTALL:
                auto_installs.append(na)
            elif item['action'] == ACTION_REMOVE:
                removes.append(na)
            elif item['action'] == ACTION_AUTO_REMOVE:
                auto_removes.append(na)
        if not installs and not removes:
            raise ValueError('no action was selected')
        for in_progress, na_list in zip(['installing', 'installing',
                                             'removing', 'removing'],
                                        [installs, auto_installs,
                                             removes, auto_removes]):
            for na in na_list:
                self._items[na]['action'] = ACTION_NO_ACTION
                pkg = self._urpmi.get_package(na)
                pkg.in_progress = in_progress
                pkg.progress = 0.0
        self._sort_and_filter()
        return self._urpmi.run_task(install=installs, remove=removes)

    def get_medias(self):
        """Return the list of medias of filtered packages."""
        return self._count_medias(self._names)

    def get_all_medias(self):
        """Return the list of medias of filtered packages."""
        return self._count_medias(self._items.iterkeys())

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

    def _count_medias(self, na_iter):
        medias = set()
        for na in na_iter:
            medias.add(self._urpmi.get_package(na).latest.media)
        return medias

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
            if re.match(name, self._urpmi.get_package(na).name):
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

    def _on_download_start(self, task_id, package):
        pass

    def _on_download_progress(self, task_id, package, percent, 
                              total, eta, speed):
        pass

    def _on_download_error(self, task_id, package, message):
        pass

    def _on_download_end(self, task_id, package):
        pass

    def _on_install_start(self, task_id, package, total, count):
        pass

    def _on_install_progress(self, task_id, package, amount, total):
        pass

    def _on_preparing(self, task_id, total):
        pass

    def _on_remove_start(self, task_id, package, total, count):
        pass

    def _on_remove_progress(self, task_id, package, amount, total):
        pass

    def _on_package_changed(self):
        self._sort_and_filter()
