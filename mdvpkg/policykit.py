import dbus
import dbus.service
import dbus.mainloop.glib

dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

def checkAuthorization(sender, connection, action):
    """Check policykit authorization.

    @param sender:
    @param connection:
    @param action:

    @raise dbus.DBusException: org.mandrivalinux.mcc2.Error.NotAuthorized.
    """

    bus = dbus.SystemBus()

    dbus_proxy = connection.get_object(
        'org.freedesktop.DBus',
        '/org/freedesktop/DBus/Bus')

    dbus_interface = dbus.Interface(
        dbus_proxy,
        'org.freedesktop.DBus')

    pid = dbus_interface.GetConnectionUnixProcessID(sender)

    policykit_proxy = bus.get_object(
        'org.freedesktop.PolicyKit1',
        '/org/freedesktop/PolicyKit1/Authority')

    policykit_interface = dbus.Interface(
        policykit_proxy,
        'org.freedesktop.PolicyKit1.Authority')

    subject = (
        'unix-process',
        {'pid': dbus.UInt32(pid, variant_level=1),
         'start-time': dbus.UInt64(0, variant_level=1)}
    )

    detail = {'':''}
    flags = dbus.UInt32(1)
    cancellation = ''

    (is_auth, _, details) = policykit_interface.CheckAuthorization(
        subject, action, detail, flags, cancellation, timeout=600)

    if not is_auth:
        msg = 'org.mandrivalinux.mdvpkg.Error.NotAuthorized'
        raise dbus.exceptions.DBusException, msg
