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
## Author(s): J. Victor Martins <jvdm@mandriva.com>
##            Paulo Belloni <paulo@mandriva.com>
##
"""Task classes and task worker for mdvpkg."""


import logging
import gobject
import dbus
import dbus.service
import dbus.service
import uuid
import functools

import mdvpkg
import mdvpkg.worker
import mdvpkg.exceptions


## Finish status
EXIT_SUCCESS = 'exit-success'
EXIT_FAILED = 'exit-failed'
EXIT_CANCELLED = 'exit-cancelled'

## Error status
ERROR_TASK_EXCEPTION = 'error-task-exception'

## Task state
# The task is being setup
STATE_SETTING_UP = 'state-setting-up'
# The task has been queued for running
STATE_QUEUED = 'state-queued'
# The task runner has finished
STATE_READY = 'state-ready'
# The task has been requested to be cancelled
STATE_CANCELLING = 'state-cancelling'
# The task has just start running
STATE_RUNNING = 'state-running'
# The task is listing requested data
STATE_LISTING = 'state-listing'
# The task is Searching packages
STATE_SEARCHING = 'state-searching'
# Task is resolving dependencies of packages
STATE_SOLVING = 'state-resolving'
# Task is downloading packages
STATE_DOWNLOADING = 'state-downloading'
# Task is installing packages
STATE_INSTALLING = 'state-installing'

log = logging.getLogger('mdvpkgd.task')


def mdvpkg_coroutine_run(corountine_run):
    """Run method decorator for tasks run methods with co-routine
    implementation (without backend).
    """
    @functools.wraps(corountine_run)
    def run(self, monitor_gen, urpmi, *args):
        def _coroutine(task_gen):
            try:
                error = task_gen.next()
            except StopIteration:
                monitor_gen.close()
            except Exception as e:
                try:
                    monitor_gen.throw(e)
                except StopIteration:
                    pass
            else:
                try:
                    monitor_gen.send(error)
                except StopIteration:
                    self.state = STATE_CANCELLING
                    task_gen.close()
                else:
                    gobject.idle_add(_coroutine, task_gen)
        _coroutine(corountine_run(self, urpmi))
    return run


class TaskBase(dbus.service.Object):
    """Base class for all tasks."""

    def __init__(self, daemon, sender, runner):
        self._bus = daemon.bus
        self.path = '%s/%s' % (mdvpkg.DBUS_TASK_PATH, uuid.uuid4().get_hex())
        dbus.service.Object.__init__(
            self,
            dbus.service.BusName(mdvpkg.DBUS_SERVICE, self._bus),
            self.path
            )

        self._sender = sender
        self._runner = runner
        self.state = STATE_SETTING_UP
        self.canceled = False

        # Passed to backend when call_backend is called ...
        self.backend_args = []
        self.backend_kwargs = {}

        # Watch for sender (which is a unique name) changes:
        self._sender_watch = self._bus.watch_name_owner(
                                     self._sender,
                                     self._sender_owner_changed
                                 )
        log.debug('task created: %s, %s', self._sender, self.path)

    @property
    def state(self):
        """Task state."""
        return self._state

    @state.setter
    def state(self, state):
        self._state = state
        self.StateChanged(state)

    #
    # D-Bus methods
    #

    @dbus.service.method(mdvpkg.TASK_IFACE,
                         in_signature='',
                         out_signature='',
                         sender_keyword='sender')
    def Run(self, sender):
        """Run the task."""
        log.debug('Run(): %s, %s', sender, self.path)
        self._check_same_user(sender)
        self._check_if_has_run()
        self._runner.push(self)

    @dbus.service.method(mdvpkg.TASK_IFACE,
                         in_signature='',
                         out_signature='',
                         sender_keyword='sender')
    def Cancel(self, sender):
        """Cancel and remove the task."""
        log.debug('Cancel(): %s, %s', sender, self.path)
        self._check_same_user(sender)
        self.canceled = True
        if self.state == STATE_QUEUED:
            self._runner.remove(self)
        if self.state in {STATE_QUEUED, STATE_SETTING_UP, STATE_READY}:
            self.on_cancel()
        # else the task is being monitored by the runner and it will
        # be cancelled by it.

    #
    # D-Bus signals
    #

    @dbus.service.signal(dbus_interface=mdvpkg.TASK_IFACE,
                         signature='s')
    def Finished(self, status):
        """Signals that the task has finished successfully."""
        log.debug('Finished(%s): %s', status, self.path)

    @dbus.service.signal(dbus_interface=mdvpkg.TASK_IFACE,
                         signature='ss')
    def Error(self, status, message):
        """Signals a task error during running."""
        log.debug('Error(%s, %s): %s',
                  status,
                  message,
                  self.path)

    @dbus.service.signal(dbus_interface=mdvpkg.TASK_IFACE,
                         signature='s')
    def StateChanged(self, state):
        """Signals the task state has changed."""
        log.debug('StateChanged(%s): %s',
                  state,
                  self.path)

    def run(self, monitor_gen, urpmi, backend):
        """Default runner, must be implemented in childs."""
        raise NotImplementedError()


    #
    # Task runner callbacks ...
    #

    def on_ready(self):
        """Task run() has finished succesfully."""
        self.Finished(EXIT_SUCCESS)
        self._remove_and_cleanup()

    def on_cancel(self):
        """Task was running and it has been cancelled."""
        self.Finished(EXIT_CANCELLED)
        self._remove_and_cleanup()

    def on_exception(self, message):
        """Task run() has thrown an exception."""
        self.Error(ERROR_TASK_EXCEPTION, message)
        self.Finished(EXIT_FAILED)
        self._remove_and_cleanup()

    def on_error(self, code, message):
        """Task was failed with error."""
        self.Error(code, message)
        self.Finished(EXIT_FAILED)
        self._remove_and_cleanup()

    def _remove_and_cleanup(self):
        """Remove the task from the bus and clean up."""
        self._sender_watch.cancel()
        self.remove_from_connection()
        log.info('task removed: %s', self.path)

    def _sender_owner_changed(self, connection):
        """Called when the sender owner changes."""
        # Since we are watching a unique name this will be only called
        # when the name is acquired and when the name is released; the
        # latter will have connection == None:
        if not connection:
            log.info('task sender disconnected: %s', self.path)
            # mimic the sender cancelling the task:
            self.Cancel(self._sender)

    def _check_same_user(self, sender):
        """Check if the sender is the task owner created the task."""
        if self._sender != sender:
            log.info('attempt method call from different sender: %s',
                     sender)
            raise mdvpkg.exceptions.NotOwner()

    def _check_if_has_run(self):
        """Check if Run() has been called."""
        if self.state != STATE_SETTING_UP:
            log.info('attempt to configure task not in STATE_SETTING_UP')
            raise mdvpkg.exceptions.TaskBadState


class ListMediasTask(TaskBase):
    """List all available medias."""

    @dbus.service.signal(dbus_interface=mdvpkg.TASK_IFACE,
                         signature='sbb')
    def Media(self, media_name, update, ignore):
        log.debug('Media(%s, %s, %s)', media_name, update, ignore)

    @mdvpkg_coroutine_run
    def run(self, urpmi):
        self.state = STATE_LISTING
        for media in urpmi.list_medias():
            self.Media(media.name, media.update, media.ignore)
            yield


class ListGroupsTask(TaskBase):
    """List all available groups."""

    @dbus.service.signal(dbus_interface=mdvpkg.TASK_IFACE,
                         signature='su')
    def Group(self, group, count):
        log.debug('Group(%s, %s)', group, count)

    @mdvpkg_coroutine_run
    def run(self, urpmi):
        self.state = STATE_LISTING
        for (group, count) in urpmi.list_groups():
            self.Group(group, count)
            yield


class ListPackagesTask(TaskBase):
    """List all available packages."""

    def __init__(self, daemon, sender, runner, attributes):
        TaskBase.__init__(self, daemon, sender, runner)
        self.filters = {'name': {'sets': {},
                                 'match_func': self._match_name},
                        'media': {'sets': {},
                                  'match_func': self._match_media},
                        'group': {'sets': {},
                                  'match_func': self._match_group},
                        'status': {'sets': {},
                                   'match_func': self._match_status},}
        # TODO Sanitize attributes by checking PackageCache entry and
        #      UrpmiPackage attributes.
        self.attributes = attributes
        self._create_list = False
        self._package_list = []

    @dbus.service.signal(dbus_interface=mdvpkg.TASK_IFACE,
                         signature='ussaa{sv}aa{sv}')
    def Package(self, index, name, status, install_details, upgrade_details):
        log.debug('Package(%s, %s, %s)', index, name, status)

    @dbus.service.signal(dbus_interface=mdvpkg.TASK_IFACE,
                         signature='u')
    def Ready(self, list_size):
        log.debug('Ready(%s)', list_size)

    @dbus.service.method(mdvpkg.TASK_IFACE,
                         in_signature='asb',
                         out_signature='',
                         sender_keyword='sender')
    def FilterName(self, names, exclude, sender):
        log.debug('FilterName(%s, %s)', names, exclude)
        self._check_same_user(sender)
        self._check_if_has_run()
        self._append_or_create_filter('name', exclude, names)

    @dbus.service.method(mdvpkg.TASK_IFACE,
                         in_signature='asb',
                         out_signature='',
                         sender_keyword='sender')
    def FilterMedia(self, medias, exclude, sender):
        log.debug('FilterMedia(%s, %s)', medias, exclude)
        self._check_same_user(sender)
        self._check_if_has_run()
        self._append_or_create_filter('media', exclude, medias)

    @dbus.service.method(mdvpkg.TASK_IFACE,
                         in_signature='asb',
                         out_signature='',
                         sender_keyword='sender')
    def FilterGroup(self, groups, exclude, sender):
        log.debug('FilterGroup(%s, %s)', groups, exclude)
        self._check_same_user(sender)
        self._check_if_has_run()
        self._append_or_create_filter('group', exclude, groups)

    @dbus.service.method(mdvpkg.TASK_IFACE,
                         in_signature='b',
                         out_signature='',
                         sender_keyword='sender')
    def FilterUpgrade(self, exclude, sender):
        log.debug('FilterUpgrade(%s)', exclude)
        self._check_same_user(sender)
        self._check_if_has_run()
        self._append_or_create_filter('status', exclude, {'upgrade'})

    @dbus.service.method(mdvpkg.TASK_IFACE,
                         in_signature='b',
                         out_signature='',
                         sender_keyword='sender')
    def FilterNew(self, exclude, sender):
        log.debug('FilterNew(%s)', exclude)
        self._check_same_user(sender)
        self._check_if_has_run()
        self._append_or_create_filter('status', exclude, {'new'})

    @dbus.service.method(mdvpkg.TASK_IFACE,
                         in_signature='b',
                         out_signature='',
                         sender_keyword='sender')
    def FilterInstalled(self, exclude, sender):
        log.debug('FilterInstalled(%s)', exclude)
        self._check_same_user(sender)
        self._check_if_has_run()
        self._append_or_create_filter('status', exclude, {'installed'})

    @dbus.service.method(mdvpkg.TASK_IFACE,
                         in_signature='uas',
                         out_signature='',
                         sender_keyword='sender')
    def Get(self, index, attributes, sender):
        log.debug('Get(%s)', index)
        self._check_same_user(sender)
        if self.state != STATE_READY:
            log.info('attempt to call Get() without STATE_READY')
            raise mdvpkg.exceptions.TaskBadState
        package, installs, upgrades = self._package_list[index]
        self._emit_package(index,
                           package,
                           attributes,
                           installs,
                           upgrades)

    @dbus.service.method(mdvpkg.TASK_IFACE,
                         in_signature='sb',
                         out_signature='',
                         sender_keyword='sender')
    def Sort(self, key, reverse, sender):
        """Sort the a cached resulls with a key."""
        log.debug('Sort(%s, reverse=%s)', key, reverse)
        self._check_same_user(sender)
        if self.state != STATE_READY:
            log.info('attempt to call Sort() without STATE_READY')
            raise mdvpkg.exceptions.TaskBadState
        if key in {'status', 'name'}:
            key_func = lambda data: getattr(data[0], key)
        else:
            key_func = lambda data: getattr(data[0].latest, key)
        self._package_list.sort(key=key_func, reverse=reverse)

    @dbus.service.method(mdvpkg.TASK_IFACE,
                         in_signature='',
                         out_signature='',
                         sender_keyword='sender')
    def SetCached(self, sender):
        """Set ListPackage to hold results in cache.
        
        The task will be removed from bus only if sender call
        Release() or inactive.
        """
        log.debug('SetCached()')
        self._check_same_user(sender)
        self._check_if_has_run()
        self._create_list = True

    @mdvpkg_coroutine_run
    def run(self, urpmi):
        self.state = STATE_LISTING
        count = 0
        for package in urpmi.list_packages():
            ## Apply filters to package entries ...
            if self._is_filtered(package.name, 'name') \
                    or self._is_filtered(package.status, 'status'):
                continue

            ## Apply filters to package version and select only
            ## entries with versions available ...
            installs = self._select_versions(package.installs.values())
            upgrades = self._select_versions(package.upgrades.values())
            if installs or upgrades:
                if self._create_list:
                    self._package_list.append((package, installs, upgrades))
                else:
                    self._emit_package(count,
                                       package,
                                       self.attributes,
                                       installs,
                                       upgrades)
                    count += 1
            yield

    def on_ready(self):
        if self._create_list:
            self.Ready(len(self._package_list))
        else:
            TaskBase.on_ready(self)

    def _select_versions(self, version_list):
        selected = []
        for rpm in version_list:
            if self._is_filtered(rpm.media, 'media') \
                    or self._is_filtered(rpm.group, 'group'):
                continue
            selected.append(rpm)
        return selected

    def _emit_package(self, count, package, attributes, installs, upgrades):
        inst_details = dbus.Array()
        upgr_details = dbus.Array()
        for rpm in installs:
            inst_details.append(self._select_version_attrs(rpm, attributes))
        for rpm in upgrades:
            upgr_details.append(self._select_version_attrs(rpm, attributes))
        self.Package(count,
                     package.name,
                     package.status,
                     inst_details,
                     upgr_details)

    def _select_version_attrs(self, rpm, attributes):
        details = {}
        for attr in attributes:
                value = getattr(rpm, attr)
                if value == None:
                    value = ''
                # bypass type guessing in case of empty lists:
                if type(value) is list and len(value) == 0:
                    value = dbus.Array(value, signature='s')
                details[attr] = value
        return details

    #
    # Filter callbacks and helpers
    #

    def _append_or_create_filter(self, filter_name, exclude, data):
        """Append more data to the filter set (selected by exclude
        flag), or create and initialize the set if it didn't existed.
        """
        sets = self.filters[filter_name]['sets']
        _set = sets.get(exclude)
        if not _set:
            _set = set()
            sets[exclude] = _set
        _set.update(data)

    def _match_name(self, candidate, patterns):
        for pattern in patterns:
            if candidate.find(pattern) != -1:
                return True
        return False

    def _match_media(self, media, medias):
        return media in medias

    def _match_group(self, group, groups):
        folders = group.split('/')
        for i in range(1, len(folders) + 1):
            if '/'.join(folders[:i]) in groups:
                return True
        return False

    def _match_status(self, status, statuses):
        return status in statuses

    def _is_filtered(self, candidate, filter_name):
        """Check if candidate should be filtered by the rules of filter
        filter_name.
        """
        match_func = self.filters[filter_name]['match_func']
        for (exclude, data) in self.filters[filter_name]['sets'].items():
            if exclude ^ (not match_func(candidate, data)):
                return True
        return False


# TODO Fix this. It still using old urpmi backend helper ...
#
# class SearchFilesTask(TaskBase):
#     """Query for package owning file paths."""
#
#     def __init__(self, daemon, sender, runner, pattern):
#         TaskBase.__init__(self, daemon, sender, runner)
#         self.backend_kwargs['pattern'] = pattern
#
#     @dbus.service.signal(dbus_interface=mdvpkg.TASK_IFACE,
#                          signature='ssssas')
#     def PackageFiles(self, name, version, release, arch, files):
#         log.debug('PackageFiles(%s, %s)', name, files)
#
#     @dbus.service.method(mdvpkg.DBUS_TASK_INTERFACE,
#                          in_signature='b',
#                          out_signature='',
#                          sender_keyword='sender')
#     def SetRegex(self, regex, sender):
#         self._check_same_user(sender)
#         log.debug('SetRegex()')
#         """Match file names using a regex."""
#         self.args.append('fuzzy')
#
#     def run(self):
#         for r in self._backend_helper(backend, 'search_files'):
#             self.PackageFiles(r['name'], r['version'], r['release'],
#                                   r['arch'], r['files'])
#             yield

class InstallPackagesTask(TaskBase):
    """Install packages or upgrades by name."""

    def __init__(self, daemon, sender, runner, names):
        TaskBase.__init__(self, daemon, sender, runner)
        self.names = names

    @dbus.service.signal(dbus_interface=mdvpkg.TASK_IFACE,
                         signature='s')
    def PreparingStart(self, total):
        log.debug('PreparingStart(%s)', total)

    @dbus.service.signal(dbus_interface=mdvpkg.TASK_IFACE,
                         signature='ss')
    def Preparing(self, amount, total):
        log.debug('Preparing(%s, %s)', amount, total)

    @dbus.service.signal(dbus_interface=mdvpkg.TASK_IFACE,
                         signature='')
    def PreparingDone(self):
        log.debug('PreparingDone()')

    @dbus.service.signal(dbus_interface=mdvpkg.TASK_IFACE,
                         signature='s')
    def DownloadStart(self, name):
        log.debug('DownloadStart(%s)', name)

    @dbus.service.signal(dbus_interface=mdvpkg.TASK_IFACE,
                         signature='sssss')
    def Download(self, name, percent, total, eta, speed):
        log.debug('Download(%s, %s, %s, %s, %s)',
                  name, percent, total, eta, speed)

    @dbus.service.signal(dbus_interface=mdvpkg.TASK_IFACE,
                         signature='s')
    def DownloadDone(self, name):
        log.debug('DownloadDone(%s)', name)

    @dbus.service.signal(dbus_interface=mdvpkg.TASK_IFACE,
                         signature='ss')
    def DownloadError(self, name, message):
        log.debug('DownloadError(%s, %s)', name, message)

    @dbus.service.signal(dbus_interface=mdvpkg.TASK_IFACE,
                         signature='ss')
    def InstallStart(self, name, total):
        log.debug('InstallStart(%s, %s)', name, total)

    @dbus.service.signal(dbus_interface=mdvpkg.TASK_IFACE,
                         signature='sss')
    def Install(self, name, amount, total):
        log.debug('Install(%s, %s, %s)', name, amount, total)

    def run(self, monitor_gen, urpmi, backend):
        backend.install_packages(monitor_gen, self, self.names)
