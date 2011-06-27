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


log = logging.getLogger('mdvpkgd')
# setup default dbus mainloop:
dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)


class MdvPkgDaemon(dbus.service.Object):
    """Represents the daemon, which provides the dbus interface (by
    default at the system bus)."""

    def __init__(self, bus=None, backend_path=None):
        log.info('Starting daemon')

        signal.signal(signal.SIGQUIT, self._quit_handler)
        signal.signal(signal.SIGTERM, self._quit_handler)

        if not bus:
            bus = dbus.SystemBus()
        self.bus = bus
        if not backend_path:
            backend_path = mdvpkg.DEFAULT_BACKEND_PATH
        self._loop = gobject.MainLoop()
        try:
            bus_name = dbus.service.BusName(mdvpkg.DBUS_SERVICE,
                                            self.bus,
                                            do_not_queue=True)
        except dbus.exceptions.NameExistsException:
            log.critical('Someone is using %s service name...',
                         mdvpkg.DBUS_SERVICE)
            sys.exit(1)
        dbus.service.Object.__init__(self, bus_name, mdvpkg.DBUS_PATH)

        self.urpmi = mdvpkg.urpmi.db.UrpmiDB()
        self.urpmi.configure_medias()
        self.urpmi.load_packages()
        self.runner = mdvpkg.worker.Runner(self.urpmi, backend_path)

        log.info('Daemon is ready')

    def run(self):
        try:
            self._loop.run()
        except KeyboardInterrupt:
            self.Quit(None)

    @dbus.service.signal(dbus_interface=mdvpkg.DBUS_TASK_INTERFACE,
                         signature='sbb')
    def Media(self, media_name, update, ignore):
        """A media found during media listing."""
        log.debug('Media(%s, %s, %s)', media_name, update, ignore)

    @dbus.service.method(mdvpkg.DBUS_INTERFACE,
                         in_signature='',
                         out_signature='o',
                         sender_keyword='sender')
    def GetList(self, sender):
        log.info('GetList() called')
        list = DBusPackageList(self.urpmi, sender, self.bus)
        return list.path

    @dbus.service.method(mdvpkg.DBUS_INTERFACE,
                         in_signature='',
                         out_signature='o',
                         sender_keyword='sender')
    def ListMedias(self, sender):
        """List configured active medias."""
        log.info('ListMedias() called')
        for media in self.urpmi.list_active_medias():
            self.Media(media.name, media.update, media.ignore)
        
    @dbus.service.method(mdvpkg.DBUS_INTERFACE,
                         in_signature='',
                         out_signature='o',
                         sender_keyword='sender')
    def ListGroups(self, sender):
        log.info('ListGroups() called')
        return self._create_task(mdvpkg.tasks.ListGroupsTask,
                                 sender)

    @dbus.service.method(mdvpkg.DBUS_INTERFACE,
                         in_signature='as',
                         out_signature='o',
                         sender_keyword='sender')
    def ListPackages(self, attributes, sender):
        log.info('ListPackages() called')
        return self._create_task(mdvpkg.tasks.ListPackagesTask,
                                 sender,
                                 attributes)

    # @dbus.service.method(mdvpkg.DBUS_INTERFACE,
    #                      in_signature='as',
    #                      out_signature='o',
    #                      sender_keyword='sender')
    # def SearchFiles(self, files, sender):
    #     log.info('SearchFiles() called: %s', files)
    #     return self._create_task(mdvpkg.tasks.SearchFilesTask,
    #                              sender,
    #                              files)

    @dbus.service.method(mdvpkg.DBUS_INTERFACE,
                         in_signature='as',
                         out_signature='o',
                         sender_keyword='sender')
    def InstallPackages(self, names, sender):
        log.info('InstallPackages() called')
        return self._create_task(mdvpkg.tasks.InstallPackagesTask,
                                 sender,
                                 names)

    @dbus.service.method(mdvpkg.DBUS_INTERFACE,
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
        self.path = "%s/%s" % (mdvpkg.DBUS_PACKAGE_LIST_PATH,
                               uuid.uuid4().get_hex())
        dbus.service.Object.__init__(
            self,
            dbus.service.BusName(mdvpkg.DBUS_PACKAGE_LIST_IFACE,
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

    @dbus.service.method(mdvpkg.DBUS_PACKAGE_LIST_IFACE,
                         in_signature='sb',
                         out_signature='',
                         sender_keyword='sender')
    def Sort(self, key, reverse, sender):
        log.debug('Sort(%s, %s) called', key, reverse)
        self._check_owner(sender)
        self._list.sort(key, reverse)

    @dbus.service.method(mdvpkg.DBUS_PACKAGE_LIST_IFACE,
                         in_signature='sasb',
                         out_signature='',
                         sender_keyword='sender')
    def Filter(self, name, matches, exclude, sender):
        log.debug('Filter(%s, %s, %s) called', attribute, matches, exclude)
        self._check_owner(sender)
        if name not in self._list.filter_names:
            raise Exception, 'invalid filter name: %s' % name
        getattr(self._list, 'filter_%s' % name)(matches, exclude)

    @dbus.service.method(mdvpkg.DBUS_PACKAGE_LIST_IFACE,
                         in_signature='uas',
                         out_signature='',
                         sender_keyword='sender')
    def Get(self, index, attributes, sender):
        log.debug('Get(%s, %s)', index, attributes)
        self._check_owner(sender)
        pkg_info = self._list.get(index)
        details = {}
        for attr in attributes:
            details[attr] = getattr(pkg_info['rpm'], attr)
        self.Package(index, pkg_info['name'], pkg_info['arch'],
                     pkg_info['status'], pkg_info['action'], details)

    @dbus.service.method(mdvpkg.DBUS_TASK_INTERFACE,
                         in_signature='',
                         out_signature='',
                         sender_keyword='sender')
    def Delete(self, sender):
        """Cancel and remove the task."""
        log.debug('Delete(): %s', self.path)
        self._check_owner(sender)
        self.on_delete()

    @dbus.service.signal(dbus_interface=mdvpkg.DBUS_PACKAGE_LIST_IFACE,
                         signature='ussssa{sv}')
    def Package(self, index, name, arch, status, action, details):
        log.debug('Package(%s, %s, %s, %s, %s) called',
                  index, name, arch, status, action)

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
    parser.add_option('-b', '--backend',
                      default=False,
                      action='store',
                      dest='backend',
                      help='Path to the urpmi backend to use.')
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

    d = MdvPkgDaemon(bus=bus, backend_path=opts.backend)
    d.run()


if __name__ == '__main__':
    run()
