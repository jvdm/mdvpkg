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
"""Mandriva package daemon."""

__author__  = "J. Victor Martins <jvdm@mandriva.com>"
__version__ = "0.7.1"

SERVICE = 'org.mandrivalinux.MdvPkg'
IFACE = 'org.mandrivalinux.MdvPkg'
PATH = '/'

TASK_PATH = '%stask' % PATH
TASK_IFACE = '%s.Task' % IFACE
PACKAGE_LIST_PATH = '%spackage_list' % PATH
PACKAGE_LIST_IFACE = '%s.PackageList' % IFACE

## Those are used in setup.py to configure installation paths ...
MANDRIVA_DATA_DIR = '/usr/share/mandriva'
DEFAULT_DATA_DIR = '%s/mdvpkg' % MANDRIVA_DATA_DIR
DEFAULT_BACKEND_DIR = '%s/backend' % DEFAULT_DATA_DIR


class ConnectableObject(object):
    """A object that can emit signals and call callbacks."""

    def __init__(self, signals=None):
        self.__signals = {}
        self.__signals_callbacks = {}
        if signals is not None:
            for signal_name in signals:
                self.__signals[signal_name] = []

    def connect(self, signal_name, callback):
        """Connect a callback to a signal."""
        conn_tuple = (signal_name, callback)
        handler = hash(conn_tuple)
        self.__signals_callbacks[handler] = conn_tuple
        self.__get_signal_handlers(signal_name).append(handler)
        return handler

    def disconnect(self, handler):
        """Disconnect a signal callback."""
        s_name, _ = self.__signals_callbacks.pop(handler)
        self.__signals[s_name].remove(handler)

    def emit(self, signal_name, *args):
        """Emit a signal calling all callbacks."""
        for handler in self.__get_signal_handlers(signal_name):
            _, callback = self.__signals_callbacks[handler]
            callback(*args)

    def __get_signal_handlers(self, signal_name):
        signal_handlers = self.__signals.get(signal_name)
        if signal_handlers is None:
            msg = 'attempt to emit an unknown signal: %s' % signal_name
            raise Exception, msg
        return signal_handlers
