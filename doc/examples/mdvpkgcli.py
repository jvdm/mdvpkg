#!/usr/bin/python
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
"""Simple command line interface to call mdvpkgd methods.

The script will read from stdin for commands in the form:

> MethodName TUPLE_PYTHON_STRING

MethodName will then be called on current dbus object (between <>)
passing TUPLE_PYTHON_STRING as arguments.
"""


import sys
import dbus
import mdvpkg
import gobject
import re
from dbus.mainloop.glib import DBusGMainLoop


DBusGMainLoop(set_as_default=True)
loop = gobject.MainLoop()


def method_call(task_name='mdvpkgd'):
    try:
        line = raw_input('<%s> ' % task_name)
    except EOFError:
        print
        line = None
    if not line:
        return None, None
    parts = line.strip().split(' ', 1)
    if not len(parts) > 1:
        parts.append('()')
    return parts

def signal_callback(*args, **kwargs):
    signal = kwargs['signal']
    print '[SIGNAL %s] %s' % (signal, args)
    if signal in {'Finished'}:
        loop.quit()

if __name__ == '__main__':
    # Parse command line
    if len(sys.argv) > 1 and sys.argv[1] in {'--session', '-s'}:
        bus = dbus.SessionBus()
    else:
        bus = dbus.SystemBus()

    bus.add_signal_receiver(signal_callback,
                            dbus_interface=mdvpkg.DBUS_INTERFACE,
                            member_keyword='signal')
    bus.add_signal_receiver(signal_callback,
                            dbus_interface=mdvpkg.DBUS_TASK_INTERFACE,
                            member_keyword='signal')

    proxy = bus.get_object(mdvpkg.DBUS_SERVICE, mdvpkg.DBUS_PATH)

    while True:
        (task_name, args) = method_call()
        if task_name is None:
            break
        path = getattr(proxy, task_name)(*eval(args))
        task_proxy = dbus.Interface(
                         bus.get_object(mdvpkg.DBUS_SERVICE, path),
                         dbus_interface=mdvpkg.DBUS_TASK_INTERFACE
                     )
        while True:
            method, args = method_call(task_name)
            if method is None:
                continue
            getattr(task_proxy, method)(*eval(args))
            if method == 'Run':
                done = False
                try:
                    loop.run()
                    break
                except KeyboardInterrupt:
                    task_proxy.Cancel()
