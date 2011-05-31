##
## Copyright (C) 2010-2011 Mandriva S.A <http://www.mandriva.com>
## All rights reserved
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License along
## with this program; if not, write to the Free Software Foundation, Inc.,
## 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
##
##
## Author(s): J. Victor Martins <jvdm@mandriva.com>
##
"""Mdvpkg exceptions and errors."""


import dbus


class MdvPkgError(dbus.DBusException):
    """Base error class for mdvpkg."""

    def __init__(self):
        name = self.__class__.__name__
        self._dbus_error_name = 'org.mandrivalinux.mdvpkg.%s' % name


class TaskAlreadyRunning(MdvPkgError):
    """Raised if a class is tried to be runned twice."""


class NotOwner(MdvPkgError):
    """Raised if a different sender tries to execute task methods."""

class TaskBadState(MdvPkgError):
    """Raised if an attempt to call methods on a task is made and its
    state is invalid."""
