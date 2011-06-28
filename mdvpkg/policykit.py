##
## Copyright (C) 2011 Mandriva S.A <http://www.mandriva.com>
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
## Author(s): Wiliam Souza <wiliam@mandriva.com>
##


import dbus
import dbus.exceptions

from mdvpkg.exceptions import AuthorizationFailed


def check_authorization(sender, connection, action):
    """Check policykit authorization.

    @param sender:
    @param connection:
    @param action:

    @raise dbus.exceptions.DBusException
    """

    dbus_proxy = connection.get_object(
        'org.freedesktop.DBus',
        '/org/freedesktop/DBus/Bus'
    )
    dbus_interface = dbus.Interface(
        dbus_proxy,
        'org.freedesktop.DBus'
    )

    pid = dbus_interface.GetConnectionUnixProcessID(sender)
    bus = dbus.SystemBus()

    policykit_proxy = bus.get_object(
        'org.freedesktop.PolicyKit1',
        '/org/freedesktop/PolicyKit1/Authority'
    )

    policykit_interface = dbus.Interface(
        policykit_proxy,
        'org.freedesktop.PolicyKit1.Authority'
    )

    subject = (
        'unix-process',
        { 'pid': dbus.UInt32(pid, variant_level=1),
          'start-time': dbus.UInt64(0, variant_level=1) }
    )

    detail = {'': ''}
    flags = dbus.UInt32(1)
    cancellation = ''
    (is_auth, _, details) = policykit_interface.CheckAuthorization(
                                subject,
                                action,
                                detail,
                                flags,
                                cancellation,
                                timeout=600
                            )
    if not is_auth:
        raise AuthorizationFailed
