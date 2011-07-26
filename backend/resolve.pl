#!/usr/bin/perl

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


use warnings;
use strict;

use URPM qw();
use urpm qw();
use urpm::media qw();
use urpm::args qw();
use urpm::select qw();
use urpm::main_loop qw();

$| = 1;

binmode STDOUT, ':encoding(utf8)';
binmode STDIN, ':encoding(utf8)';

MAIN: {
    my $task_string;
    if (not defined($task_string = <>)) {
	print "%MDVPKG ERROR Missing task string\n";
	exit 1;
    }
    chomp($task_string);
    my (@names) = split(/\t/, $task_string);

    # Initializing urpmi ...
    my $urpm = urpm->new_parse_cmdline;
    urpm::media::configure($urpm);

    # Parse args ...
    my $installs = [];
    my $removes = [];
    foreach (@names) {
	my $list = s/^r:(.*)/$1/ ? $removes : $installs;
	push @$list, $_;
    }

    my ($restart, $state, $to_remove, %pkg_map)
	= _get_state($urpm, $installs, $removes);

    # Check %state and emit return data ...
    while (my ($id, $info) = each %{ $state->{selected} }) {
	my $pkg = $urpm->{depslist}[$id];
	my $action;
	if (defined $info->{requested}) {
	    $action = 'action-install';
	}
	else {
	    $action = 'action-auto-install';
	}
	response_action($pkg->name, $pkg->arch, $action);
    }

    foreach (@{ $state->{orphans_to_remove} }) {
	response_action($_->name, $_->arch, 'action-auto-remove');
    }

    foreach (grep {
	         $state->{rejected}{$_}{removed}
		     && !$state->{rejected}{$_}{obsoleted};
	     } keys %{$state->{rejected} || {}})
    {
	my $disttag = $state->{rejected}{$_}{disttag} || '';
	my $distepoch = $state->{rejected}{$_}{distepoch} || '';
	s/-$disttag$distepoch// if ($disttag and $distepoch);
	my $name;
	my $arch;
	($name, undef, undef, $arch) = /^(.+)-([^-]+)-([^-].*)\.(.+)$/;
	response_action($name, $arch, 'action-remove');
    }

    # TODO There is no conflict checking !!

    exit 0;
}

sub response_action {
    my ($name, $arch, $action) = @_;
	printf("%%MDVPKG SELECTED %s %s@%s\n",
	       $action,
	       $name,
	       $arch);

}

sub response_error {
    my ($name, @args) = @_;
	printf("%%MDVPKG ERROR %s\n",
	       $name,
	       join("\t", @args));

}

######################################################################
# TODO Replicated code from urpmi_backend
######################################################################

# %options
#   - auto_select: passed to resolve dependencies
sub _get_state {
    my ($urpm, $installs, $removals, %options) = @_;

    my %state = ();
    my @to_remove = ();
    my %pkg_map = ();

    if (@{ $removals || [] }) {
        @to_remove = urpm::select::find_packages_to_remove(
			$urpm,
			\%state,
			$removals,
			callback_notfound => sub {
			    shift;
			    response_error('error-not-found', @_);
			    return;
			},
			callback_base => sub {
			    shift;
			    response_error('error-remove-base', @_);
			    return;
		    }) or do {
			return;
		    };
	my %remove_names = map { $_ => undef } @to_remove;
	foreach (@{ $urpm->{depslist} }) {
	    if (exists $remove_names{$_->fullname}) {
		delete $remove_names{$_->fullname};
		my $key = sprintf('%s-%s-%s.%s',
				  $_->name,
				  $_->version,
				  $_->release,
				  $_->arch);
		$pkg_map{$key} = $_;
	    }
	}

	urpm::orphans::compute_future_unrequested_orphans($urpm, \%state);
	push(@to_remove,
	     map {
		 scalar $_->fullname
	     } @{ $state{orphans_to_remove} });
	foreach (@{ $state{orphans_to_remove} }) {
	    my $key = sprintf('%s-%s-%s.%s',
			      $_->name,
			      $_->version,
			      $_->release,
			      $_->arch);
	    $pkg_map{$key} = $_;
	}

    }

    my %packages = ();
    my $restart;
    if (@{ $installs || [] }) {
	urpm::select::search_packages(
	    $urpm,
	    \%packages,
	    $installs,
	    fuzzy => 0,
	    no_substring => 1,
	) or do {
	    response('error', 'error-not-found', @{ $installs });
	    return;
	};
	$restart = urpm::select::resolve_dependencies(
		       $urpm,
		       \%state,
		       \%packages,
		       auto_select => $options{auto_select},
		   );
    }

    foreach (@{ $urpm->{depslist} }[keys %{ $state{selected} || {} }]) {
	$pkg_map{$_->fullname} = $_;
    }

    # TODO Check $state for conflicts and report that to caller as
    #      error responses.
    return $restart, \%state, \@to_remove, %pkg_map;
}
