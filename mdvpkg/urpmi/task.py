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
"""Class to represent a urpmi task."""


import gobject
import uuid
import collections
import os.path
import logging
import subprocess


log = logging.getLogger('mdvpkgd.urpmi.task')

ROLE_INSTALL = 'role-install'

STATE_QUEUED = 'state-queued'
STATE_RUNNING = 'state-running'
STATE_DONE = 'state-done'


def create_task(callback, role, args):
    """Create a new task dict."""
    return {'id': uuid.uuid4(),
            'role': role,
            'args': args,
            'callback': callback}


class UrpmiRunner(object):
    """Queue and controls urpmi tasks.  Some of them using urpmi
    backend.
    """

    def __init__(self, backend_dir):
        self._queue = collections.OrderedDict()
        self._task = None  # None == not running any task
        self._backend_path = os.path.join(backend_dir, 'urpmi_backend.pl')
        self._backend_proc = None
        self._role_handlers = {ROLE_INSTALL: self._handle_install}

    @property
    def backend_is_running(self):
        """True if the urpmi backend is running."""
        if self._backend_proc != None:
            return self._backend_proc.poll() == None
        return False

    @property
    def backend(self):
        """The running instance of the subprocess backend object."""
        if not self.backend_is_running:
            self.start_backend()
        return self._backend_proc

    def start_backend(self):
        """Starts the backend process."""
        if self.backend_is_running:
            raise Exception, 'backend already running'
        self._backend_proc = subprocess.Popen(
                                 '',
                                 executable=self._backend_path,
                                 stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE
                             )
        gobject.io_add_watch(self._backend_proc.stdout,
                             gobject.IO_IN | gobject.IO_PRI,
                             self._backend_reply_callback)
        gobject.io_add_watch(self._backend_proc.stdout,
                             gobject.IO_ERR | gobject.IO_HUP,
                             self._backend_error_callback)
        log.debug('backend started')

    def kill_backend(self):
        """Send SIGTERM to the backend process and wait its death.

        This will cancel any task running.
        """
        if not self.backend_is_running:
            raise Exception, 'attempt to kill a not not running backend'
        self._backend_proc.send_signal(signal.SIGTERM)
        self._backend_proc.communicate()
        self._backend_proc = None
        log.debug('backend killed')

    def push(self, task):
        """Add a task to the run queue."""
        log.debug('task queued: %s', task['id'])
        if self._task is None:
            gobject.idle_add(self._run_next_task)
        self._queue[task['id']] = task
        task['callback'].run_state(STATE_QUEUED)

    def _run_next_task(self):
        """Get the next task in queue to run."""
        self._task = None
        try:
            _, self._task = self._queue.popitem(last=False)
        except KeyError:
            log.info('queue is empty, no more tasks to run')
        else:
            self._task['callback'].run_state(STATE_RUNNING)
            self._role_handlers[self._task['role']](self._task['args'])

    #
    # Role handlers ...
    #

    def _handle_install(self, args):
        args.insert(0, 'install')
        self.backend.stdin.write('%s\n' % '\t'.join(args))

    #
    # Backend I/O callbacks ...
    #

    def _backend_reply_callback(self, stdout, condition):
        if self._task is not None:
            # readline() may block, but we're expecting backend
            # process to always emit data linewise, so if there is
            # data a line will come shortly:
            line = stdout.readline()
            if line.startswith('<mdvpkg> '):
                _, line = line.split(' ', 1)
                log.debug('backend response: %s', line)
                response, line = line.rstrip('\n').split('\t', 1)
                if response == 'callback':
                    name, args = line.split('\t', 1)
                    getattr(self._task['callback'], name)(args.split('\t'))
                else:
                    try:
                        handler = getattr(self, '_on_backend_%s' % response)
                    except AttributeError:
                        line = "unknown handler for '%s'" % response
                        handler = self._on_backend_exception
                    else:
                        handler(self._task['callback'], line)
        return True

    def _backend_error_callback(self, stdout, condition):
        if self._task is not None:
            self._task['callback'].backend_error('backend pipe error')
            self._task = None
        else:
            raise Exception, 'backend pipe error'
        self.kill_backend()

    #
    # Reply callbacks ...
    #

    def _on_backend_done(self, callback, line):
        callback.done()
        self._run_next_task()

    def _on_backend_error(self, callback, line):
        callback.error(line)
        self._run_next_task()

    def _on_backend_exception(self, callback, line):
        callback.exception(line)
        self._run_next_task()
