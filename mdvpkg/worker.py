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

import mdvpkg.tasks


log = logging.getLogger('mdvpkgd.worker')


class BackendError(Exception):
    pass


class Backend(object):
    """Represents a urpmi backend process instance."""

    def __init__(self, path):
        self.path = path        
        self.proc = None
        self._task = None
        self._runner_gen = None

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
        """Send SIGTERM to the backend child and wait its death."""
        if not self.running:
            raise Exception, "kill() called and backend's not running"
        self.proc.send_signal(signal.SIGTERM)
        # wait for child to terminate
        self.proc.communicate()
        self.proc = None
        log_backend.debug('Backend killed')

    def install_packages(self, runner_gen, task, names):
        if self._task:
            raise Exception, 'already running a task'
        self._task = task
        self._runner_gen = runner_gen
        self._send_task('install_packages', *names)

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
        if line.startswith('%MDVPKG\t') and self._task:
            tag, arg_str = line.rstrip('\n').split('\t', 2)[1:]
            try:
                handler = getattr(self, '_handle_%s' % tag)
            except AttributeError:
                self._handle_EXCEPTION(
                    'unknown response from backend: %s' % tag
                )
            else:
                handler(eval(arg_str))
                if tag != 'SIGNAL':
                    self._clean()
        return True

    def _error_callback(self, stdout, condition):
        self._handle_EXCEPTION('backend pipe error')
        self._clean()
        self.kill()

    def _clean(self):
        self._task = None
        self._runner_gen = None

    #
    # Response handlers
    #

    def _handle_SIGNAL(self, args):
        signal_name = args[0]
        args = args[1:]
        getattr(self._task, signal_name)(*args)

    def _handle_EXCEPTION(self, args):
        try:
            self._runner_gen.throw(BackendError, args[0])
        except StopIteration:
            pass

    def _handle_ERROR(self, args):
        self._runner_gen.send(*args)

    def _handle_DONE(self, args):
        log.debug('done received')
        self._runner_gen.close()
        log.debug('generator closed')


class Runner(object):
    """Queue and controls the `run()` co-routine method of mdvpkg
    tasks."""

    def __init__(self, urpmi, backend_path):
        self._urpmi = urpmi
        self._backend = Backend(backend_path)
        self.queue = collections.OrderedDict()

    def push(self, task):
        """Add a task in the run queue."""
        log.debug('task queued: %s', task.path)
        if not self.queue:
            self.run_next_task()
        self.queue[task.path] = task
        task.state = mdvpkg.tasks.STATE_QUEUED

    def remove(self, task):
        """Remove a task in the queue."""
        self.queue.pop(task.path)

    def run_next_task(self):
        """Run next task in the next loop iteration."""
        gobject.idle_add(self._run_next_task)

    def _run_next_task(self):
        """Get the next task in queue to run."""
        try:
            _, task = self.queue.popitem(last=False)
        except KeyError:
            log.info('queue is empty, no more tasks to run')
        else:
            task.state = mdvpkg.tasks.STATE_RUNNING
            runner_gen = self._task_monitor(task)
            try:
                runner_gen.send(None)
            except StopIteration:
                log.error('task canceled while in queue and not removed')
            else:
                task.run(runner_gen, self._urpmi, self._backend)

    def _task_monitor(self, task):
        """Return a generator to listen for task status in co-routine
        manner.

        Run methods will be notified of task cancellation by catching
        StopIteration from our generator (which is thrown when the
        method returns).
        """
        while True:
            if task.canceled is True:
                gobject.idle_add(task.on_cancel)
                break
            try:
                # if error is None the task is running with no errors:
                error = yield
            except GeneratorExit:
                task.state = mdvpkg.tasks.STATE_READY
                task.on_ready()
                break
            except Exception as e:
                log.exception('task finished with exception')
                task.on_exception(e.message)
                break
            else:
                if error is not None:
                    task.on_error(*error)
                    break
        self.run_next_task()
