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
"""Working and queue classes for mdvpkg tasks."""


import gobject
import subprocess
import os
import signal
import collections
import logging

log = logging.getLogger('mdvpkgd.worker')


class BackendError(Exception):
    pass


class Backend(object):
    """Represents a urpmi backend process instance."""

    def __init__(self, path):
        self.path = path        
        self.proc = None
        self.task = None
        self.error = ''

    @property
    def running(self):
        if self.proc != None:
            return self.proc.poll() == None
        return False

    def start(self):
        """ Starts the backend's process. """
        if self.running:
            raise Exception, 'backend already running'
        self.proc = subprocess.Popen('',
                                     executable=self.path,
                                     stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE)
        gobject.io_add_watch(self.proc.stdout,
                             gobject.IO_IN | gobject.IO_PRI,
                             self._reply_callback)
        gobject.io_add_watch(self.proc.stdout,
                             gobject.IO_ERR | gobject.IO_HUP,
                             self._error_callback)

    def kill(self):
        """ Send SIGTERM to the backend child asking and wait it to
        exit.
        """
        if not self.running:
            raise Exception, "kill() called and backend's not running"
        self.proc.send_signal(signal.SIGTERM)
        # wait for child to terminate
        self.proc.communicate()
        self.proc = None
        log_backend.debug('Backend killed')

    def install_packages(self, task, names):
        if self.task:
            raise Exception, 'Already running a task'
        self.task = task
        self._send_task('install_packages', *names)

    def task_has_done(self):
        if not self.running:
            raise BackendError, 'Backend has died.'
        if self.error:
            raise BackendError, self.error
        return self.task == None

    def _send_task(self, task_name, *args):
        if not self.running:
            self.start()
        self.proc.stdin.write("%s\t%s\n" % (task_name, '\t'.join(args)))

    #
    # Backend I/O callbacks
    #

    def _reply_callback(self, stdout, condition):
        # readline() may block, but we're expecting backend process to
        # always emit data linewise, so if there is data a line will
        # come shortly:            
        line = stdout.readline()
        if line.startswith('%MDVPKG\t') and self.task:
            self._handle_backend_line(*line.rstrip('\n').split('\t', 2)[1:])
        return True

    def _error_callback(self, stdout, condition):
        self.error = 'Pipe error with backend.'
        self.kill()

    def _handle_backend_line(self, tag, arg_str):
        if tag.startswith('SIGNAL'):
            signal = tag.split(' ')[1]
            getattr(self.task, signal)(*eval(arg_str))
        elif tag.startswith('EXCEPTION'):
            self.error = eval(arg_str)
        elif tag.startswith('DONE'):
            self.task = None
        else:
            self.error = 'Unknown response from backend: %s' % tag


# class TaskQueue(object):
#     """Queue for ordering mdvpkg task running."""
#
#     def __init__(self, urpmi, backend):
#         self._urpmi = urpmi
#         self._backend = backend
#         self.queue = collections.OrderedDict()
#
#     def push(self, task):
#         if not self.queue:
#             gobject.idle_add(self.run_next)
#         self.queue[task.path] = task
#
#     def run_next(self):
#         try:
#             path, task = self.queue.popitem(last=False)
#         except KeyError:
#             # This will happend if the last task has been cancelled
#             # before run_next() was called ...
#             pass
#         else:
#             self.task.run()
