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
import mdvpkg.urpmi.db
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
        log.info('Starting daemon')

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
            log.critical('Someone is using %s service name...',
                         mdvpkg.SERVICE)
            sys.exit(1)
        dbus.service.Object.__init__(self, bus_name, mdvpkg.PATH)

        self.urpmi = mdvpkg.urpmi.db.UrpmiDB(backend_dir=backend_dir)
        self.urpmi.configure_medias()
        self.urpmi.load_packages()
        self.runner = mdvpkg.worker.Runner(self.urpmi, backend_dir)

        log.info('Daemon is ready')

    def run(self):
        try:
            self._loop.run()
        except KeyboardInterrupt:
            self.Quit(None)

    @dbus.service.signal(dbus_interface=mdvpkg.IFACE,
                         signature='sbb')
    def Media(self, media_name, update, ignore):
        """A media found during media listing."""
        log.debug('Media(%s, %s, %s)', media_name, update, ignore)

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
        log.info('Shutdown was requested')
        log.debug('Quitting main loop...')
        self._loop.quit()

    def _create_task(self, task_class, sender, *args):
        task = task_class(self, sender, self.runner, *args)
        return task.path

    def _quit_handler(self, signum, frame):
        """Handler for quiting signals."""
        self.Quit(None)


class DBusPackageList(dbus.service.Object):
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
        self._sender = sender
        # Watch for sender (which is a unique name) changes:
        self._sender_watch = self._bus.watch_name_owner(
                                 self._sender,
                                 self._sender_owner_changed
                             )
        self._list = mdvpkg.urpmi.db.PackageList(urpmi)

    @dbus.service.method(mdvpkg.PACKAGE_LIST_IFACE,
                         in_signature='',
                         out_signature='u',
                         sender_keyword='sender')
    def Size(self, sender):
        log.debug('Size() called')
        self._check_owner(sender)
        return len(self._list)

    @dbus.service.method(mdvpkg.PACKAGE_LIST_IFACE,
                         in_signature='sb',
                         out_signature='',
                         sender_keyword='sender')
    def Sort(self, key, reverse, sender):
        log.debug('Sort(%s, %s) called', key, reverse)
        self._check_owner(sender)
        self._list.sort(key, reverse)

    @dbus.service.method(mdvpkg.PACKAGE_LIST_IFACE,
                         in_signature='sasb',
                         out_signature='',
                         sender_keyword='sender')
    def Filter(self, name, matches, exclude, sender):
        log.debug('Filter(%s, %s, %s) called', name, matches, exclude)
        self._check_owner(sender)
        if name not in self._list.filter_names:
            raise Exception, 'invalid filter name: %s' % name
        getattr(self._list, 'filter_%s' % name)(matches, exclude)

    @dbus.service.method(mdvpkg.PACKAGE_LIST_IFACE,
                         in_signature='uas',
                         out_signature='',
                         sender_keyword='sender')
    def Get(self, index, attributes, sender):
        log.debug('Get(%s, %s)', index, attributes)
        self._check_owner(sender)
        pkg_info = self._list.get(index)
        for key in pkg_info.keys():
            if pkg_infO[key] is None:
                pkg_info[key] = ''
        details = {}
        for attr in attributes:
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
        for group, count in self._list.get_groups().iteritems():
            self.Group(group, count)
        self.Ready()

    @dbus.service.method(mdvpkg.PACKAGE_LIST_IFACE,
                         in_signature='',
                         out_signature='',
                         sender_keyword='sender')
    def GetAllGroups(self, sender):
        log.debug('GetAllGroups() called')
        self._check_owner(sender)
        for group, count in self._list.get_all_groups().iteritems():
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

    @dbus.service.method(mdvpkg.PACKAGE_LIST_IFACE,
                           in_signature='as',
                           out_signature='s',
                           sender_keyword='sender',
                           connection_keyword='connection')
    def Install(self, index, sender, connection):
        """Mark a package and its dependencies for installation."""
        log.debug('Install(%s) called', index)
        self._list.install(index)

    @dbus.service.method(mdvpkg.PACKAGE_LIST_IFACE,
                         in_signature='',
                         out_signature='o',
                         sender_keyword='sender',
                         connection_keyword='connection')
    def ProcessActions(self, sender, connection):
	check_authorization(sender,
                            connection,
                            'org.mandrivalinux.mdvpkg.auth_admin_keep')
        raise NotImplementedError
        
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

    def on_delete(self):
        """List must be deleted."""
        self._sender_watch.cancel()
        self.remove_from_connection()
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
