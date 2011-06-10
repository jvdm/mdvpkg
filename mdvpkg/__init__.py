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
__version__ = "0.6.2"

DBUS_SERVICE = 'org.mandrivalinux.mdvpkg'
DBUS_INTERFACE = 'org.mandrivalinux.mdvpkg'
DBUS_PATH = '/'

DBUS_TASK_PATH = '%stask' % DBUS_PATH
DBUS_TASK_INTERFACE = '%s.task' % DBUS_INTERFACE

## Those are used in setup.py to configure installation paths ...
MANDRIVA_DATA_DIR = '/usr/share/mandriva'
DEFAULT_DATA_DIR = '%s/mdvpkg' % MANDRIVA_DATA_DIR
DEFAULT_BACKEND_DIR = '%s/backend' % DEFAULT_DATA_DIR
DEFAULT_BACKEND_PATH = '%s/urpmi_backend.pl' % DEFAULT_BACKEND_DIR
