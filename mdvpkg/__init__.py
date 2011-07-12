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
