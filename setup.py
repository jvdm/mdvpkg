#!/usr/bin/env python

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
##            Wiliam Souza <wiliam@mandriva.com>
##
"""Distutil setup call for mdvpkg."""

from glob import glob
from distutils.core import setup

import mdvpkg


with open('README') as file:
    long_description = file.read()

setup(
    name='mdvpkg',
    version=mdvpkg.__version__,
    description="Mandriva's package management daemon",
    long_description=long_description,
    author='J. Victor Martins',
    author_email='jvdm@mandriva.com',
    license='GPLv2+',
    url='https://github.com/jvdm/mdvpkg',
    packages=['mdvpkg', 'mdvpkg.urpmi'],
    scripts=['bin/mdvpkgd'],
    data_files=[
        ('/etc/dbus-1/system.d/', glob('dbus/*.conf')),
        ('/usr/share/dbus-1/system-services/', glob('dbus/*.service')),
        ('/usr/share/polkit-1/actions/', glob('policykit/*.policy')),
        (mdvpkg.DEFAULT_BACKEND_DIR, ['backend/urpmi_backend.pl',
                                      'backend/resolve.pl',
                                      'backend/mdvpkg.pm']),
    ],
    options={
        'install': { 'install_purelib': mdvpkg.DEFAULT_DATA_DIR,
                     'install_scripts': '/usr/sbin',
                     'install_data': mdvpkg.DEFAULT_DATA_DIR }
    },
)
