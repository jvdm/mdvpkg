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
import logging
import bisect


log = logging.getLogger('mdvpkgd.urpmi')


def create_evrd(evrd_data):
    """Create a RpmEVRD object from dictionaries and iterables."""
    evrd_data_type = type(evrd_data)
    if evrd_data_type == dict:
        evrd = RpmEVRD(evrd_data)
    elif evrd_data_type in {tuple, list}:
        keys = ['version', 'release']
        if len(evrd_data) > 2:
            keys.insert(0, 'epoch')
        if len(evrd_data) > 3:
            keys.append('distepoch')
        evrd = RpmEVRD(dict(zip(keys, evrd_data)))
    else:
        raise ValueError, 'bad type: %s' % evrd_data_type
    return evrd


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
    def nvra(self):
        return self.name, self.version, self.release, self.arch

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


class Package(object):
    """Represents a package, in terms of versions and updates, in the
    urpmi database cache.

    Each package is identified by an name, arch tuple, and forms a
    list of package versions: installed, upgrades and downgrades.
    """

    def __init__(self, na, urpmi):
        """Create a new instance in the update state."""
        self.na = na
        self.urpmi = urpmi
        self._versions = {}  # { rpm.evrd: {'rpm': RPM, 'type': TYPE} }
        self._types = {'installed': [],
                       'upgrade': [],
                       'downgrade': []}
        self.in_progress = None
        self.progress = None

    def __getitem__(self, key):
        return self._get_version(key)['rpm']

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
            # self.emit('deleted')
            self.clear()
        else:
            # Check deleted version ...
            for evrd in self._versions.keys():
                if evrd not in other._versions:
                    # self.emit('version-deleted', evrd)
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
                        # self.emit('new-version', evrd)
                        pass
                    else:
                        if old_dict['type'] != version_dict['type']:
                            if old_dict['type'] == 'installed':
                                # self.emit('removed-version', evrd)
                                pass
                            elif version_dict['type'] == 'installed':
                                # self.emit('installed-version', evrd)
                                pass
                    self._versions[evrd] = version_dict
                    self._types = other._types

    def on_download_start(self, evrd):
        """React to the start of a version download."""
        assert self.in_progress == 'installing'
        version = self._get_version(evrd)
        assert version['type'] == 'upgrade'
        self.progress = 0.0

    def on_download_progress(self, evrd, fraction):
        """React to the progress of a version download."""
        assert self.in_progress == 'installing'
        version = self._get_version(evrd)
        assert version['type'] == 'upgrade'
        self.progress = fraction / 2.0

    def on_download_done(self, evrd):
        """React to the end of a version download."""
        assert self.in_progress == 'installing'
        version = self._get_version(evrd)
        assert version['type'] == 'upgrade'
        self.progress = 0.5
        log.debug('downloaded %s-%s-%s.%s',
                  self.name,
                  evrd['version'],
                  evrd['release'],
                  self.arch)

    def on_install_start(self, evrd):
        """React to the start of installation of a version."""
        assert self.in_progress == 'installing'
        version = self._get_version(evrd)
        assert version['type'] == 'upgrade'
        self.in_progress = 'installing'
        self.progress = 0.5

    def on_install_progress(self, evrd, fraction):
        """React to the progress of installation of a version."""
        assert self.in_progress == 'installing'
        version = self._get_version(evrd)
        assert version['type'] == 'upgrade'
        self.progress = 0.5 + (fraction / 2.0)

    def on_install_done(self, evrd):
        """React to the end of installation of a version."""
        assert self.in_progress == 'installing'
        version = self._get_version(evrd)
        assert version['type'] == 'upgrade'

        self.in_progress = None
        self._set_latest_installed(version['rpm'])
        self._set_type(version, 'installed')
        log.debug('installed %s-%s-%s.%s',
                  self.name,
                  evrd['version'],
                  evrd['release'],
                  self.arch)

    def on_remove_start(self, evrd):
        """React to the start of a version removal."""
        assert self.in_progress is 'removing'
        version = self._get_version(evrd)
        assert version['type'] == 'installed'
        self.in_progress = 'removing'
        self.progress = 0.0

    def on_remove_progress(self, evrd, fraction):
        assert self.in_progress == 'removing'
        version = self._get_version(evrd)
        assert version['type'] == 'installed'
        self.progress = fraction

    def on_remove_done(self, evrd):
        """React to the end of removal of a version."""
        assert self.in_progress == 'removing'
        version = self._get_version(evrd)
        assert version['type'] == 'installed'
        self.in_progress = None
        if self.has_installs and version['rpm'] < self.latest_installed:
            new_type = 'downgrade'
        else:
            new_type = 'upgrade'
        self._set_type(version, new_type)
        log.debug('removed %s-%s-%s.%s',
                  self.name,
                  evrd['version'],
                  evrd['release'],
                  self.arch)

    def _get_version(self, key):
        if isinstance(key, RpmEVRD):
            evrd = key
        else:
            evrd = create_evrd(key)
        version = self._versions.get(evrd)
        if version is None:
            raise KeyError, 'not version of %s: %s' % (self.na, evrd)
        return version

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
        evrd = sorted(self._types[type])[-1]
        return self._versions[evrd]['rpm']

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
