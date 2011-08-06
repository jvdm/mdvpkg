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

package mdvpkg;


use urpm::select qw();
use urpm::orphans qw();


##
# create_state
#     Get lists of package fullnames for installation and removal to
#     create a $state object and parameters for main_loop and
#     urpm::install::install.
#
# :Parameters:
#     `$urpm` : The urpm object
#     `$installs` : array_ref
#         List of fullnames to install
#     `$removes` : array_ref
#         List of fullnames to remove
#     `$options{auto_select}` : eval boolean
#         evaluated to True to ignore $installs and select all
#         upgrades
#
sub create_state {
    my ($urpm, $installs, $removals, %options) = @_;

    my %state = ();
    my @to_remove = ();

    if (@{ $removals || [] }) {
        @to_remove = urpm::select::find_packages_to_remove(
			$urpm,
			\%state,
			$removals,
			callback_notfound => sub {
			    shift;
			    die {error => 'error-not-found',
				 names => \@_};
			},
			callback_base => sub {
			    shift;
			    die {error => 'error-remove-base',
				 names => \@_};
		    }) or do {
			die {error => 'error-nothing-to-remove',
			     names => []}
		    };
	urpm::orphans::compute_future_unrequested_orphans(
	    $urpm,
	    \%state
	);
	push(@to_remove,
	     map {
		 scalar $_->fullname
	     } @{ $state{orphans_to_remove} });
    }

    my $restart;

    if (@{ $installs || [] } or $options{auto_select}) {
	my %packages = ();
	urpm::select::search_packages(
	    $urpm,
	    \%packages,
	    $installs,
	    fuzzy => 0,
	    no_substring => 1,
	) or do {
	    die {error => 'error-not-found',
		 names => $installs};
	};
	$restart = urpm::select::resolve_dependencies(
		       $urpm,
		       \%state,
		       \%packages,
		       auto_select => $options{auto_select},
		   );
    }

    return $restart, \%state, \@to_remove;
}

##
# pkg_from_fullname
#     Find the URPM::Package object in depslist from a fullname.
#
sub pkg_from_fullname {
    my $urpm = shift;
    my $fullname = shift;

    my ($disttag, $distepoch) = @_;
    $disttag = $disttag && "-$disttag";
    $disttag ||= '';
    $distepoch ||= '';
    my $nvra = $fullname;
    $nvra =~ s/$disttag$distepoch//;

    my ($name, undef, undef)
	= $nvra =~ /(.+)-([^-]+)-([^-]+)\./;
    my @result = grep {
	             $fullname eq $_->fullname
                 } $urpm->packages_providing($name);
    if (@result == 0) {
	die "could not find URPM::Package for $name";
    }
    return $result[0];
}

##
# get_evrd
#     Return a python dictionary string with the evrd of a package.
#
sub get_evrd {
    my ($pkg) = @_;
    my $evrd = sprintf(
	"{'epoch': %s," .
	" 'version': '%s'," .
	" 'release': '%s'",
	$pkg->epoch,
	$pkg->version,
	$pkg->release);
    if ($pkg->distepoch) {
	$evrd .= sprintf(", 'distepoch': '%s'}",
			 $pkg->distepoch);
    }
    else {
	$evrd .= '}'
    }
    return $evrd;
}

##
# get_na
#     Return a python tuple string with the na of a package.
#
sub get_na {
    my ($pkg) = shift;
    my $name = $pkg->name;
    my $arch = $pkg->arch;
    $name =~ s/'/\'/g;
    $arch =~ s/'/\'/g;
    return sprintf("('%s', '%s')", $name, $arch);
}


1;
