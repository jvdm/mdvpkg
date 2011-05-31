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
##            Eugeni Dodonov <eugeni@mandriva.com>
##            
##
"""Package repository manipulation."""


import re
import collections
import subprocess
import gzip
import mdvpkg.rpmutils
import logging


log = logging.getLogger('mdvpkgd.repo')


class Media:
    """Represents a URPMI media."""

    _hdlist_path_tpl = '/var/lib/urpmi/%s/synthesis.hdlist.cz'

    def __init__(self, name, update, ignore, key='', compressed=True):
        self.name = name
        self.ignore = ignore
        self.update = update
        self.key = key
        if compressed:
            self._open = gzip.open
        else:
            self._open = open
        self._hdlist_path = self._hdlist_path_tpl % name
        # name-version-release[disttagdistepoch].arch regexp:

        # FIXME This is a ugly hack.  Some packages comes with
        #       disttag/distepoch in their file names, separated by
        #       '-'.  And synthesis provides NVRA information in the
        #       rpm file name.  So we check if <release> starts with
        #       'm' for 'mdv' (our currently disttag).

        self._nvra_re = re.compile('^(?P<name>.+)-'
                                       '(?P<version>[^-]+)-'
                                       '(?P<release>[^m].*)\.'
                                       '(?P<arch>.+)$')
        self._cap_re = re.compile('^(?P<name>[^[]+)'
                                      '(?:\[\*])*(?:\[(?P<cond>[<>=]*)'
                                      ' *(?P<ver>.*)])?')

    def list(self):
        """Open the hdlist file and yields package data in it."""
        log.debug('reading packages in media: %s', self.name)
        with self._open(self._hdlist_path, 'r') as hdlist:
            pkg = {}
            for line in hdlist:
                fields = line.rstrip('\n').split('@')[1:]
                tag = fields[0]
                if tag == 'info':
                    (pkg['name'],
                     pkg['version'],
                     pkg['release'],
                     pkg['arch']) = self.parse_rpm_name(fields[1])
                    for (i, field) in enumerate(('epoch', 'size', 'group')):
                        pkg[field] = fields[2 + i]
                    yield pkg
                    pkg = {}
                elif tag == 'summary':
                    pkg['summary'] = fields[1]
                elif tag in ('requires', 'provides', 'conflict',
                                   'obsoletes'):
                    pkg[tag] = self._parse_capability_list(fields[1:])

    def parse_rpm_name(self, name):
        """Returns (name, version, release, arch) tuple from a rpm
        package name.  Handle both names with and without
        {release}-{disttag}{distepoch}.
        """
        match = self._nvra_re.match(name)
        if not match:
            raise ValueError, 'Malformed RPM name: %s' % name

        release = match.group('release')
        if release.find('-') != -1:
            release = release.split('-')[0]

        return (match.group('name'),
                match.group('version'),
                release,
                match.group('arch'))

    def _parse_capability_list(self, cap_str_list):
        """Parse a list of capabilities specification string.

        Return a list of dictionaries for each capability.
        """
        cap_list = []
        for cap_str in cap_str_list:
            m = self._cap_re.match(cap_str)
            if m is None:
                continue    # ignore malformed names
            cap_list.append({ 'name': m.group('name'),
                              'condition': m.group('cond'),
                              'version': m.group('ver') })
        return tuple(cap_list)


class RpmPackage(object):
    """Represents a RPM package."""

    def __init__(self, name, version, release, arch, epoch,
                     size, group, summary, requires=[], provides=[],
                     conflict=[], obsoletes=[]):
        self.name = name
        self.version = version
        self.release = release
        self.arch = arch
        self.epoch = epoch
        self.size = size
        self.group = group
        self.summary = summary
        self.requires = requires
        self.provides = provides
        self.conflict = conflict
        self.obsoletes = obsoletes

    @property
    def vr(self):
        """Package Version-Release: identifies a specific package
        version.
        """
        return (self.version, self.release)

    @property
    def nvra(self):
        """Package Name-Version-Release-Arch: identifies uniquely the
        package.
        """
        return (self.name, self.version, self.release, self.arch)

    def __eq__(self, other):
        return self.nvra == other.nvra

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
        if self.name != other.name:
            raise ValueError('Name mismatch %s != %s'
                             % (self.name, other.name))
        if self.epoch > other.epoch:
            return 1
        elif self.epoch < other.epoch:
            return -1
        cmp = mdvpkg.rpmutils.rpmvercmp(self.version, other.version)
        if cmp == 0:
            cmp = mdvpkg.rpmutils.rpmvercmp(self.release, other.release)
        return cmp

    def __str__(self):
        return '%s-%s-%s.%s' % self.nvra

    def __repr__(self):
        return '%s(%s:%s)' % (self.__class__.__name__,
                              self,
                              self.epoch)


class UrpmiPackage(object):
    """A proxy to directly access package data in the urpmi package
    cache.
    """

    def __init__(self, urpmi, name):
        self._urpmi = urpmi
        self.name = name

    @property
    def status(self):
        """Package status: 'new' or 'current'."""
        if self._urpmi._cache[self.name]['new']:
            return 'new'
        else:
            return 'current'

    @property
    def upgrades(self):
        """List of upgrade versions, empty if no upgrades are
        available.
        """
        return self._installed_updates('upgrade')

    @property
    def downgrades(self):
        """List of downgrade versions, empty if no upgrades are
        available.
        """
        return self._installed_updates('downgrade')

    @property
    def versions(self):
        """List of package versions by status."""
        return self._urpmi._cache[self.name][self.status].values()

    def _installed_updates(self, update_type):
        return self._urpmi._cache[self.name][update_type].values()


class URPMI(object):
    """Represents a urpmi database.
    
    Packages are stored in a 'package cache', keyed by name.  For
    example, to access the cache and get the media for
    'foobar-ver-rel' package you would write:

    >>> self._cache['foobar']['current'][('ver', 'rel')]['media']

    The cache is a python dict (where keys are the package names).
    Each package version available for each name is then grouped by
    status:

    - new: Package versions are new (the package name is not
      installed) and do not upgrade/downgrade an installed version.

    - current: Package versions are installed.

    - upgrade: Package versions are upgrades to the highest version
      installed.

    - downgrade: Package versions are downgrades to the highest
      version installed.

    Package versions are tuples (version, release) as returned by
    RpmPackage.vr property.

    So each cache entry (cache dictionary values) is a python dict
    with status as keys and a dictionary of versions as values (status
    with no packages have an empty dictionary).

    Package versions residing in 'upgrade' or 'downgrade' group
    implies the existance of a version in 'current'.  So each package
    name will necessarily have versions in 'new' or 'current'
    (exclusively).

    Each package version is stored in a 'package version description',
    a python dict with the following keys:

    - rpm: A RpmPackage instance of the package.

    - installtime: Installation time (present only if package version
      is in 'current').

    - media: Media where package was found, or '' (empyt string) if
      not found in any media.

    See 'self._get_or_create_cache_entry()' and
    'self._create_pkg_desc()' to see how those data structures are
    created.    
    """

    _urpmi_cfg = '/etc/urpmi/urpmi.cfg'

    def __init__(self):
        self._medias = None
        self._cache = {}
        self.groups = {}

    @property
    def medias(self):
        """Get the list of package medias in the repo.  Late
        initialization is used.
        """
        if not self._medias:
            self._load_medias()
        return self._medias

    @medias.deleter
    def medias(self):
        self._medias = None

    @property
    def packages(self):
        """A generator for each package in the cache instantiated as
        UrpmiPackage object.
        """
        if not self._cache:
            self.load_db()
        for name in self._cache.keys():
            yield UrpmiPackage(self, name)

    def load_db(self):
        """Parse all available medias and locally installed packages
        and populates the package cache.
        """
        self._cache = {}
        self._load_installed()
        for media in [m for m in self.medias.values() if not m.ignore]:
            for pkg_data in media.list():
                pkg = RpmPackage(**pkg_data)
                self._load_pkg_from_media(pkg, media.name)
                self._add_group(pkg.group)

    def _add_group(self, group):
        if group not in self.groups:
            self.groups[group] = 1
        else:
            self.groups[group] += 1

    def _load_installed(self):
        """Populate installed cache with package in local rpm db."""
        rpm = subprocess.Popen("rpm -qa --qf '%{NAME}@%{VERSION}@%{RELEASE}"
                                   "@%{ARCH}@%|EPOCH?{%{EPOCH}}:{0}|"
                                   "@%{SIZE}@%{GROUP}@%{SUMMARY}"
                                   "@%{INSTALLTIME}\n'",
                               stdout=subprocess.PIPE,
                               stdin=None,
                               shell=True)
        for line in rpm.stdout:
            fields = line.split('@')
            # Initialize without INSTALLTIME:
            pkg = RpmPackage(*fields[:-1])
            installtime = int(fields[-1])

            entry = self._get_or_create_cache_entry(pkg.name)
            version = pkg.vr

            assert version not in entry['current'], \
                'installed pkg with same version: %s' % pkg
            desc = self._create_pkg_desc(pkg,
                                         installtime=installtime)
            entry['current'][version] = desc
            self._add_group(pkg.group)
        rpm.wait()

    def _load_pkg_from_media(self, pkg, media_name):
        version = pkg.vr
        entry = self._get_or_create_cache_entry(pkg.name)
        if entry['current']:
            current = entry['current']
            if version in current:
                current[version]['media'] = media_name
            else:
                desc = self._create_pkg_desc(pkg, media_name)
                installed_pkgs = [v['rpm'] for v in current.values()]
                recent_pkg = sorted(installed_pkgs)[-1]
                if pkg > recent_pkg:
                    entry['upgrade'][version] = desc
                else:
                    entry['downgrade'][version] = desc
        else:
            entry['new'][version] = self._create_pkg_desc(pkg, media_name)

    def _get_or_create_cache_entry(self, name):
        if name not in self._cache:
            entry = {'upgrade': {},
                     'downgrade': {},
                     'current': {},
                     'new': {}}
            self._cache[name] = entry
        else:
            entry = self._cache[name]
        return entry

    def _create_pkg_desc(self, pkg, media='', installtime=None):
        desc = {'rpm': pkg, 'media': media}
        if installtime:
            desc['installtime'] = installtime
        return desc

    def _load_medias(self):
        """Locate all configured medias."""
        self._medias = {}
        media_r = re.compile('^(.*) {([\s\S]*?)\s*}', re.MULTILINE)
        ignore_r = re.compile('.*(ignore).*')
        update_r = re.compile('.*(update).*')
        key_r = re.compile('.*key-ids:\s* (.*).*')
        url_r = re.compile('(.*) (.*://.*|/.*$)')
        with open(self._urpmi_cfg, 'r') as fd:
            data = fd.read()
            res = media_r.findall(data)
            for media, values in res:
                res2 = url_r.findall(media)
                if res2:
                    # found a media with url, fixing
                    name, url = res2[0]
                    media = name
                media = media.replace('\\', '')
                media = media.strip()
                key = ''
                ignore=False
                update=False
                keys = key_r.findall(values)
                if keys:
                    key = keys[0]
                if ignore_r.search(values):
                    ignore=True
                if update_r.search(values):
                    update=True
                self._medias[media] = Media(media, update, ignore, key)
