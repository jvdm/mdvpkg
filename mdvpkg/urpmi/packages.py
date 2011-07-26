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
"""Classes and functions for rpm package representations."""

import rpm
import gobject
import logging
import bisect


log = logging.getLogger('mdvpkgd.urpmi')


class RpmEVRD(object):
    """Represents a EVR + Distepoch of a RPM package."""

    def __init__(self, pkgdict):
        self.epoch = int(pkgdict.get('epoch', 0))
        self.version = pkgdict['version']
        self.release = pkgdict['release']
        self.distepoch = pkgdict.get('distepoch')

    def __cmp__(self, other):
        return rpm.evrCompare(self.__repr__(), other.__repr__())

    def __hash__(self):
        return (self.epoch,
                self.version,
                self.release,
                self.distepoch).__hash__()

    def __repr__(self):
        evr = '%s:%s-%s' % (self.epoch, self.version, self.release)
        if self.distepoch:
            evr += ':' + self.distepoch
        return evr


class RpmPackage(object):
    """Represents a package version in the rpm/urpmi database with its
    data.
    """

    def __init__(self, pkgdict):
        self.name = pkgdict['name']
        self.arch = pkgdict['arch']
        self.group = pkgdict['group']
        self.summary = pkgdict['summary']
        self.size = int(pkgdict['size'])

        self.evrd = RpmEVRD(pkgdict)
        self.disttag = pkgdict.get('disttag')
        self.media = pkgdict.get('media')
        self.installtime = pkgdict.get('installtime')
        # FIXME Currently installed packages won't come with
        #       capabilities information:
        self.requires = pkgdict.get('requires', [])
        self.provides = pkgdict.get('provides', [])
        self.conflict = pkgdict.get('conflict', [])
        self.obsoletes = pkgdict.get('obsoletes', [])

    @property
    def distepoch(self):
        return self.evrd.distepoch

    @property
    def epoch(self):
        return self.evrd.epoch

    @property
    def version(self):
        return self.evrd.version

    @property
    def release(self):
        return self.evrd.release

    @property
    def na(self):
        """Package (name, arch) tuple."""
        return (self.name, self.arch)

    @property
    def denvra(self):
        return (self.distepoch, self.disttag, self.epoch,
                self.name, self.version, self.release, self.arch)

    def __eq__(self, other):
        return self.denvra == other.denvra

    def __ne__(self, other):
        return not self.__eq__(other)

    def __le__(self, other):
        if self == other:
            return True
        else:
            return NotImplemented

    def __ge__(self, other):
        return self.__le__(other)

    def __cmp__(self, other):
        if not isinstance(other, self.__class__):
            raise ValueError("Not instance of '%s'" %
                             self.__class__.__name__)
        if self.name != other.name:
            raise ValueError('Name mismatch %s != %s'
                             % (self.name, other.name))
        return self.evrd.__cmp__(other.evrd)

    def __str__(self):
        return '%s-%s-%s.%s' % (self.name, self.version,
                                self.release, self.arch)

    def __repr__(self):
        return '%s(%s:%s)' % (self.__class__.__name__,
                              self,
                              self.epoch)


class Package(gobject.GObject):
    """Represents a package, in terms of versions and updates, in the
    urpmi database cache.

    Each package is identified by an name, arch tuple, and forms a
    list of package versions: installed, upgrades and downgrades.
    """

    __gsignals__ = {
        'deleted': (
            gobject.SIGNAL_RUN_FIRST,
            gobject.TYPE_NONE,
            ()
        ),
        'installed-version': (
            gobject.SIGNAL_RUN_FIRST,
            gobject.TYPE_NONE,
            (gobject.TYPE_PYOBJECT,)
        ),
        'removed-version': (
            gobject.SIGNAL_RUN_FIRST,
            gobject.TYPE_NONE,
            (gobject.TYPE_PYOBJECT,)
        ),
        'new-version': (
            gobject.SIGNAL_RUN_FIRST,
            gobject.TYPE_NONE,
            (gobject.TYPE_PYOBJECT,)
        ),
    }

    def __init__(self, na, urpmi):
        """Create a new instance in the update state."""
        gobject.GObject.__init__(self)
        self.na = na
        self.urpmi = urpmi
        self._versions = {}  # { rpm.na: {'rpm': RPM, 'type': TYPE} }
        self._types = {'installed': [],
                       'upgrade': [],
                       'downgrade': []}
        self.in_progress = None
        self.progress = None

    @property
    def name(self):
        return self.na[0]

    @property
    def arch(self):
        return self.na[1]

    @property
    def status(self):
        """Package entry status."""
        if self.in_progress is not None:
            return self.in_progress
        return self.current_status

    @property
    def current_status(self):
        """Package status prior to action."""
        if self.has_installs:
            if self.has_upgrades:
                return 'upgrade'
            return 'installed'
        return 'new'

    @property
    def has_installs(self):
        return bool(self._types['installed'])

    @property
    def has_upgrades(self):
        return bool(self._types['upgrade'])

    @property
    def has_downgrades(self):
        return bool(self._types['downgrade'])

    @property
    def installs(self):
        """List of installed rpms."""
        return [ver['rpm'] for ver in self._list_by_type('installed')]

    @property
    def upgrades(self):
        """List of upgrade rpms."""
        return [ver['rpm'] for ver in self._list_by_type('upgrade')]

    @property
    def downgrades(self):
        """List of downgrade rpms."""
        return [ver['rpm'] for ver in self._list_by_type('downgrade')]

    @property
    def latest_installed(self):
        """Most recent installed rpm."""
        return self._latest_by_type('installed')

    @property
    def latest_upgrade(self):
        """Most recent upgrade rpm."""
        return self._latest_by_type('upgrade')

    @property
    def latest(self):
        """The latest representative package, based on status."""
        if self.in_progress == 'installing':
            return self.latest_upgrade
        elif self.in_progress == 'removing':
            return self.latest_installed
        elif self.current_status in {'new'}:
            return self.latest_upgrade
        else:
            return self.latest_installed

    def add_version(self, rpm):
        version_dict = self._versions.get(rpm.evrd)
        if version_dict is None:
            # Create a new version dict for this package and set its
            # type.  Update type of any other update versions if this
            # is the most recent installed version ...
            version_dict = {'rpm': rpm}
            if rpm.installtime is not None:
                self._set_latest_installed(rpm)
                self._set_type(version_dict, 'installed')
            else:
                self._set_type(version_dict, self._get_update_type(rpm))
            self._versions[rpm.evrd] = version_dict
        else:
            if version_dict['type'] == 'installed':
                if rpm.installtime is not None:
                    log.error('found two versions installed of the '
                              'same package %s: %s and %s',
                              self.na,
                              rpm.evrd,
                              version_dict['rpm'].evrd)
                elif version_dict['rpm'].media is not None:
                    log.warning('found versions of package %s '
                                'in two diferent medias: %s and %s',
                                self.na,
                                rpm.media,
                                version_dict['rpm'].media)
                else:
                    version_dict['rpm'].media = rpm.media
            elif rpm.installtime is not None:
                rpm.media = version_dict['rpm'].media
                version_dict['rpm'] = rpm
                self._set_type(version_dict, 'installed')
            else:
                version_dict['rpm'] = rpm
                self._set_type(version_dict, self._get_update_type(rpm))
       
    def update(self, other):
        """Update our version list from another package's version
        list.
        """
        assert self.na == other.na
        if not other._versions:
            self.emit('deleted')
            self.clear()
        else:
            # Check deleted version ...
            for evrd in self._versions.keys():
                if evrd not in other._versions:
                    self.emit('version-deleted', evrd)
                    del self._versions[evrd]
            # Check for new versions and compare installed status to
            # signal new installations or removals ...
            while True:
                try:
                    evrd, version_dict = other._versions.popitem()
                except KeyError:
                    break
                else:
                    old_dict = self._versions.get(evrd)
                    if old_dict is None:
                        self.emit('new-version', evrd)
                    else:
                        if old_dict['type'] != version_dict['type']:
                            if old_dict['type'] == 'installed':
                                self.emit('removed-version', evrd)
                            elif version_dict['type'] == 'installed':
                                self.emit('installed-version', evrd)
                    self._versions[evrd] = version_dict
                    self._types = other._types

    def on_install(self, evrd):
        """React to the installation event of a upgrade evrd."""
        if self.in_progress != 'installing':
            msg = 'not installing package: %s %s' % (self.na, evrd)
            raise ValueError, msg
        log.debug('%s-%s installed', self.na, evrd)
        self.in_progress = None
        evrd = RpmEVRD(evrd)
        version = self._versions.get(evrd)
        if version is None:
            raise ValueError, 'not version of %s: %s' % (self.na, evrd)
        self._set_latest_installed(version['rpm'])
        self._set_type(version, 'installed')


    def on_remove(self, evrd):
        """React to the removal of an installed evrd."""
        if self.in_progress != 'removing':
            msg = 'not removing package: %s %s' % (self.na, evrd)
            raise ValueError, msg
        log.debug('%s-%s removed', self.na, evrd)
        self.in_progress = None
        evrd = RpmEVRD(evrd)
        version = self._versions.get(evrd)
        if version is None:
            raise ValueError, 'not version of %s: %s' % (self.na, evrd)
        self._set_type(version, 'upgrade')


    def _set_latest_installed(self, rpm):
        if not self.has_installs or rpm > self.latest_installed:
            for updict in self._list_by_type('upgrade'):
                if updict['rpm'] < rpm:
                    self._set_type(updict, 'downgrade')
            for updict in self._list_by_type('downgrade'):
                if updict['rpm'] > rpm:
                    self._set_type(updict, 'upgrade')

    def _list_by_type(self, type):
        """Return a list of version dicts of specified type."""
        return map(lambda evrd: self._versions[evrd],
                   self._types[type])

    def _latest_by_type(self, type):
        """Return the latest package of specified type."""
        na = sorted(self._types[type])[-1]
        return self._versions[na]['rpm']

    def _set_type(self, version_dict, type):
        rpm = version_dict['rpm']
        old_type = version_dict.get('type')
        if old_type is not None:
            self._types[old_type].remove(rpm.evrd)
        self._types[type].append(rpm.evrd)
        version_dict['type'] = type

    def _get_update_type(self, rpm):
        if not self.has_installs or rpm > self.latest_installed:
            return 'upgrade'
        else:
            return 'downgrade'

    def __repr__(self):
        return '%s%s:%s' % (self.__class__.__name__,
                            self.na,
                            id(self))
