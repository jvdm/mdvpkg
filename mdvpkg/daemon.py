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
##
"""Main daemon class and running script."""


import logging
import logging.handlers
import sys
import dbus
import dbus.exceptions
import dbus.mainloop.glib
import dbus.service
import gobject
import signal
import uuid

import mdvpkg
from mdvpkg.urpmi.db import UrpmiDB
from mdvpkg.urpmi.db import PackageList
import mdvpkg.tasks
import mdvpkg.worker
from mdvpkg.policykit import check_authorization


log = logging.getLogger('mdvpkgd')
# setup default dbus mainloop:
dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)


class MdvPkgDaemon(dbus.service.Object):
    """Represents the daemon, which provides the dbus interface (by
    default at the system bus)."""

    def __init__(self, bus=None, backend_dir=None):
        log.info('starting daemon')

        signal.signal(signal.SIGQUIT, self._quit_handler)
        signal.signal(signal.SIGTERM, self._quit_handler)

        if not bus:
            bus = dbus.SystemBus()
        self.bus = bus
        if not backend_dir:
            backend_dir = mdvpkg.DEFAULT_BACKEND_DIR
        self._loop = gobject.MainLoop()
        try:
            bus_name = dbus.service.BusName(mdvpkg.SERVICE,
                                            self.bus,
                                            do_not_queue=True)
        except dbus.exceptions.NameExistsException:
            log.critical('someone is using %s service name',
                         mdvpkg.SERVICE)
            sys.exit(1)
        dbus.service.Object.__init__(self, bus_name, mdvpkg.PATH)

        self.urpmi = UrpmiDB(backend_dir=backend_dir)
        self.urpmi.connect('task-queued', self.TaskQueued)
        self.urpmi.connect('task-running', self.TaskRunning)
        self.urpmi.connect('task-progress', self.TaskProgress)
        self.urpmi.connect('task-done', self.TaskDone)
        self.urpmi.configure_medias()
        self.urpmi.load_packages()

        log.info('daemon is ready')

    def run(self):
        try:
            self._loop.run()
        except KeyboardInterrupt:
            self.Quit(None)

    #
    # Media related signals
    #

    @dbus.service.signal(dbus_interface=mdvpkg.IFACE,
                         signature='sbb')
    def Media(self, media_name, update, ignore):
        """A media found during media listing."""
        log.debug('Media(%s, %s, %s)', media_name, update, ignore)

    #
    # Task related signals
    # 

    @dbus.service.signal(dbus_interface=mdvpkg.IFACE,
                         signature='s')
    def TaskQueued(self, task_id):
        log.debug('TaskQueued(%s)', task_id)
        
    @dbus.service.signal(dbus_interface=mdvpkg.IFACE,
                         signature='s')
    def TaskRunning(self, task_id):
        log.debug('TaskRunning(%s)', task_id)

    @dbus.service.signal(dbus_interface=mdvpkg.IFACE,
                         signature='suu')
    def TaskProgress(self, task_id, count, total):
        log.debug('TaskProgress(%s, %s, %s)', task_id, count, total)

    @dbus.service.signal(dbus_interface=mdvpkg.IFACE,
                         signature='s')
    def TaskDone(self, task_id):
        log.debug('TaskDone(%s)', task_id)

    #
    # Daemon methods
    #

    @dbus.service.method(mdvpkg.IFACE,
                         in_signature='',
                         out_signature='o',
                         sender_keyword='sender')
    def GetList(self, sender):
        log.info('GetList() called')
        list = DBusPackageList(self.urpmi, sender, self.bus)
        return list.path

    @dbus.service.method(mdvpkg.IFACE,
                         in_signature='',
                         out_signature='',
                         sender_keyword='sender')
    def Quit(self, sender):
        """Request a shutdown of the service."""
        log.info('shutdown was requested')
        log.debug('quitting main loop ...')
        self._loop.quit()

    def _quit_handler(self, signum, frame):
        """Handler for quiting signals."""
        self.Quit(None)


class DBusPackageList(PackageList, dbus.service.Object):
    """DBus interface representing a PackageList."""

    def __init__(self, urpmi, sender, bus=None):
        if bus is None:
            bus = dbus.SystemBus()
        self._bus = bus
        self.path = "%s/%s" % (mdvpkg.PACKAGE_LIST_PATH,
                               uuid.uuid4().get_hex())
        dbus.service.Object.__init__(
            self,
            dbus.service.BusName(mdvpkg.SERVICE,
                                 self._bus),
            self.path
        )
        PackageList.__init__(self, urpmi)
        self._sender = sender
        # Watch for sender (which is a unique name) changes:
        self._sender_watch = self._bus.watch_name_owner(
                                 self._sender,
                                 self._sender_owner_changed
                             )
        # Auto load packages ...
        self.load()

    @dbus.service.method(mdvpkg.PACKAGE_LIST_IFACE,
                         in_signature='',
                         out_signature='u',
                         sender_keyword='sender')
    def Size(self, sender):
        log.debug('Size() called')
        self._check_owner(sender)
        return len(self)

    @dbus.service.method(mdvpkg.PACKAGE_LIST_IFACE,
                         in_signature='sb',
                         out_signature='',
                         sender_keyword='sender')
    def Sort(self, key, reverse, sender):
        log.debug('Sort(%s, %s) called', key, reverse)
        self._check_owner(sender)
        self.sort(key, reverse)

    @dbus.service.method(mdvpkg.PACKAGE_LIST_IFACE,
                         in_signature='sasas',
                         out_signature='',
                         sender_keyword='sender')
    def Filter(self, name, include, exclude, sender):
        log.debug('Filter(%s) called', name)
        self._check_owner(sender)
        if name not in self.filter_names:
            raise Exception, 'invalid filter name: %s' % name
        getattr(self, 'filter_%s' % name)(include, exclude)

    @dbus.service.method(mdvpkg.PACKAGE_LIST_IFACE,
                         in_signature='uas',
                         out_signature='',
                         sender_keyword='sender')
    def Get(self, index, attributes, sender):
        log.debug('Get(%s, %s)', index, attributes)
        self._check_owner(sender)
        if index < 0 or index >= len(self):
            raise mdvpkg.exceptions.MdvPkgError(
                      'index out of range: %s' % index
                  )
        pkg_info = self.get(index)
        for key in pkg_info.keys():
            if pkg_info[key] is None:
                if key == 'progress':
                    pkg_info[key] = 1.0
                else:
                    pkg_info[key] = ''
        details = {}
        for attr in attributes:
            if attr in {'progress'}:
                details['progress'] = pkg_info[attr]
            else:
                value = getattr(pkg_info['rpm'], attr)
                if value is None:
                    value = ''
                details[attr] = value
        self.Package(index, pkg_info['name'], pkg_info['arch'],
                     pkg_info['status'], pkg_info['action'], details)

    @dbus.service.method(mdvpkg.PACKAGE_LIST_IFACE,
                         in_signature='',
                         out_signature='',
                         sender_keyword='sender')
    def GetGroups(self, sender):
        log.debug('GetGroups() called')
        self._check_owner(sender)
        for group, count in self.get_groups().iteritems():
            self.Group(group, count)
        self.Ready()

    @dbus.service.method(mdvpkg.PACKAGE_LIST_IFACE,
                         in_signature='',
                         out_signature='',
                         sender_keyword='sender')
    def GetAllGroups(self, sender):
        log.debug('GetAllGroups() called')
        self._check_owner(sender)
        for group, count in self.get_all_groups().iteritems():
            self.Group(group, count)
        self.Ready()

    @dbus.service.method(mdvpkg.PACKAGE_LIST_IFACE,
                         in_signature='',
                         out_signature='',
                         sender_keyword='sender')
    def Delete(self, sender):
        """Cancel and remove the list from bus."""
        log.debug('Delete(): %s', self.path)
        self._check_owner(sender)
        self.on_delete()

    @dbus.service.method(
        mdvpkg.PACKAGE_LIST_IFACE,
        in_signature='u',
        out_signature='a(ssss)a(ssss)a(s(ssss)v)a(s(ssss)v)',
        sender_keyword='sender',
        connection_keyword='connection'
    )
    def Install(self, index, sender, connection):
        """Mark a package and its dependencies for installation."""
        log.debug('Install(%s) called', index)
        self._check_owner(sender)
        return self._convert_actions(*self.install(index))

    @dbus.service.method(
        mdvpkg.PACKAGE_LIST_IFACE,
        in_signature='u',
        out_signature='a(ssss)a(ssss)a((ssss)sv)a((ssss)sv)',
        sender_keyword='sender',
        connection_keyword='connection'
    )
    def Remove(self, index, sender, connection):
        """Mark a package and its dependencies for removal."""
        log.debug('Remove(%s) called', index)
        self._check_owner(sender)
        return self._convert_actions(*self.remove(index))

    def _convert_actions(self, ins_sel, rm_sel, ins_rej, rm_rej):
        ins_sel = map(lambda rpm: rpm.nvra, ins_sel)
        ins_rej = map(self._convert_rej, ins_rej)
        rm_sel = map(lambda rpm: rpm.nvra, rm_sel)
        rm_rej = map(self._convert_rej, rm_rej)
        return ins_sel, rm_sel, ins_rej, rm_rej

    def _convert_rej(self, rej):
        rpm2nvra = lambda rpm: rpm.nvra
        list = []
        list.append(rej[0])
        list.append(rpm2nvra(rej[1]))
        # Convert subjects that are RpmPackage objects ...
        if rej[0] in {'reject-install-conflicts',
                      'reject-install-rejected-dependency'}:
            list.append(map(lambda rpm: rpm.nvra, rej[2]))
        else:
            list.append(rej[2])
        return tuple(list)

    @dbus.service.method(mdvpkg.PACKAGE_LIST_IFACE,
                         in_signature='u',
                         out_signature='',
                         sender_keyword='sender')
    def NoAction(self, index, sender):
        """Unmark a package for installation or removal."""
        log.debug('NoAction(%s) called', index)
        self._check_owner(sender)
        self.no_action(index)

    @dbus.service.method(mdvpkg.PACKAGE_LIST_IFACE,
                         in_signature='',
                         out_signature='s',
                         sender_keyword='sender',
                         connection_keyword='connection')
    def ProcessActions(self, sender, connection):
	# check_authorization(sender,
        #                     connection,
        #                     'org.mandrivalinux.mdvpkg.auth_admin_keep')
        try:
            return self.process_actions()
        except ValueError:
            raise mdvpkg.exceptions.MdvPkgError('no action selected')
        
    #
    # DBus signals
    #

    @dbus.service.signal(dbus_interface=mdvpkg.PACKAGE_LIST_IFACE,
                         signature='ussssa{sv}')
    def Package(self, index, name, arch, status, action, details):
        log.debug('Package(%s, %s, %s, %s, %s) called',
                  index, name, arch, status, action)

    @dbus.service.signal(dbus_interface=mdvpkg.PACKAGE_LIST_IFACE,
                         signature='su')
    def Group(self, group, count):
        log.debug('Group(%s, %s)', group, count)

    @dbus.service.signal(dbus_interface=mdvpkg.PACKAGE_LIST_IFACE,
                         signature='')
    def Ready(self):
        log.debug('Ready() called')

    @dbus.service.signal(dbus_interface=mdvpkg.PACKAGE_LIST_IFACE,
                         signature='ss')
    def Error(self, code, message):
        log.debug('Error(%s, %s) called', code, message)

    @dbus.service.signal(dbus_interface=mdvpkg.PACKAGE_LIST_IFACE,
                         signature='sv')
    def DownloadStart(self, task_id, index):
        log.debug('DownloadStart(%s) called', index)

    @dbus.service.signal(dbus_interface=mdvpkg.PACKAGE_LIST_IFACE,
                         signature='svssss')
    def DownloadProgress(self, task_id, index, percent, total, eta, speed):
        log.debug('DownloadProgress(%s, %s) called', index, percent)

    @dbus.service.signal(dbus_interface=mdvpkg.PACKAGE_LIST_IFACE,
                         signature='ss')
    def Preparing(self, task_id, total):
        log.debug('Preparing(%s) called', total)

    @dbus.service.signal(dbus_interface=mdvpkg.PACKAGE_LIST_IFACE,
                         signature='svss')
    def InstallStart(self, task_id, index, total, count):
        log.debug('InstallStart(%s, %s) called', index, total)

    @dbus.service.signal(dbus_interface=mdvpkg.PACKAGE_LIST_IFACE,
                         signature='svss')
    def InstallProgress(self, task_id, index, amount, total):
        log.debug('InstallProgress(%s, %s) called', index, amount)

    @dbus.service.signal(dbus_interface=mdvpkg.PACKAGE_LIST_IFACE,
                         signature='svss')
    def RemoveStart(self, task_id, index, total, count):
        log.debug('RemoveStart(%s) called', index)

    @dbus.service.signal(dbus_interface=mdvpkg.PACKAGE_LIST_IFACE,
                         signature='svss')
    def RemoveProgress(self, task_id, index, amount, total):
        log.debug('RemoveProgress(%s, %s) called', index, amount)


    def on_delete(self):
        """List must be deleted."""
        self._sender_watch.cancel()
        self.remove_from_connection()
        self.delete()
        # TODO Disconnect all signals, otherwise the reference will
        #      persist.
        log.info('package list deleted: %s', self.path)

    def _check_owner(self, sender):
        """Check if the sender is the list owner, the one who created
        it.
        """
        if self._sender != sender:
            log.info('attempt method call from different sender: %s',
                     sender)
            raise mdvpkg.exceptions.NotOwner()

    def _sender_owner_changed(self, connection):
        """Called when the sender owner changes."""
        # Since we are watching a unique name this will be only called
        # when the name is acquired and when the name is released; the
        # latter will have connection == None:
        if not connection:
            log.info('task sender disconnected: %s', self.path)
            # mimic the sender deleting the list:
            self.Delete(self._sender)

    #
    # Urpmi signal callbacks
    #

    def _on_download_start(self, task_id, package):
        try:
            index = self._names.index(package.na)
        except ValueError:
            index = package.latest.nvra
        self.DownloadStart(task_id, index)

    def _on_download_progress(self, task_id, package, percent,
                              total, eta, speed):
        try:
            index = self._names.index(package.na)
        except ValueError:
            index = package.latest.nvra
        self.DownloadProgress(
            task_id, index, percent, total, eta, speed
        )

    def _on_download_error(self, package, message):
        self.Error('download-error', message)

    def _on_install_start(self, task_id, package, total, count):
        try:
            index = self._names.index(package.na)
        except ValueError:
            index = package.latest.nvra
        self.InstallStart(task_id, index, total, count)

    def _on_install_progress(self, task_id, package, amount, total):
        try:
            index = self._names.index(package.na)
        except ValueError:
            index = package.latest.nvra
        self.InstallProgress(task_id, index, amount, total)

    def _on_preparing(self, task_id, total):
        self.Preparing(task_id, total)

    def _on_remove_start(self, task_id, package, total, count):
        try:
            index = self._names.index(package.na)
        except ValueError:
            index = package.latest.nvra
        self.RemoveStart(task_id, index, total, count)

    def _on_remove_progress(self, task_id, package, amount, total):
        try:
            index = self._names.index(package.na)
        except ValueError:
            index = package.latest.nvra
        self.RemoveProgress(task_id, index, amount, total)

def run():
    """Run the mdvpkg daemon from command line."""
    ## Setup logging ...
    try:
        _syslog = logging.handlers.SysLogHandler(
                      address='/dev/log',
                      facility=logging.handlers.SysLogHandler.LOG_DAEMON
                  )
        _syslog.setLevel(logging.INFO)
        _formatter = logging.Formatter('%(name)s: %(levelname)s: '
                                           '%(message)s')
        _syslog.setFormatter(_formatter)
    except:
        pass
    else:
        log.addHandler(_syslog)
    _console = logging.StreamHandler()
    _formatter = logging.Formatter('%(asctime)s %(name)s [%(levelname)s]: '
                                       '%(message)s',
                                   '%T')
    _console.setFormatter(_formatter)
    log.addHandler(_console)

    ## Parse command line options ...
    from optparse import OptionParser
    parser = OptionParser()
    parser.add_option('-s', '--session',
                      default=False,
                      action='store_true',
                      dest='session',
                      help="Connect mdvpkgd's DBus service to the session "
                           "bus (instead of the system bus).")
    parser.add_option('-d', '--debug',
                      default=False,
                      action='store_true',
                      dest='debug',
                      help='Show debug messages and information.')
    parser.add_option('-b', '--backend-dir',
                      default=False,
                      action='store',
                      dest='backend_dir',
                      help='Path to the urpmi backend directory.')
    opts, args = parser.parse_args()

    ## Setup daemon and run ...
    bus = None
    if opts.session:
        import dbus
        bus = dbus.SessionBus()
    if opts.debug:
        log.setLevel(logging.DEBUG)
    else:
        log.setLevel(logging.INFO)

    d = MdvPkgDaemon(bus=bus, backend_dir=opts.backend_dir)
    d.run()


if __name__ == '__main__':
    run()
