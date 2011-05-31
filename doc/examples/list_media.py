#!/usr/bin/python

import dbus
import mdvpkg
import gobject
from dbus.mainloop.glib import DBusGMainLoop

DBusGMainLoop(set_as_default=True)
loop = gobject.MainLoop()

bus = dbus.SystemBus()
proxy = bus.get_object(mdvpkg.DBUS_SERVICE, mdvpkg.DBUS_PATH)
task_path = proxy.ListMedias(dbus_interface=mdvpkg.DBUS_INTERFACE)

def media_cb(*args):
    print 'media %s (update=%s, ignore=%s)' % args

def finished_cb():
    loop.quit()

proxy = bus.get_object(mdvpkg.DBUS_SERVICE, task_path)
proxy.connect_to_signal('Media',
                        media_cb,
                        dbus_interface=mdvpkg.DBUS_TASK_INTERFACE)
proxy.connect_to_signal('Finished',
                        finished_cb,
                        dbus_interface=mdvpkg.DBUS_TASK_INTERFACE)
proxy.Run(dbus_interface=mdvpkg.DBUS_TASK_INTERFACE)

loop.run()
