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

use FindBin;
use lib "$FindBin::Bin/";

use mdvpkg;


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


    my ($restart, $state, $to_remove);
    eval {
	($restart, $state, $to_remove)
	    = mdvpkg::create_state($urpm, $installs, $removes);
    }
    or do {
	response_error($@->{error}, @{ $@->{names} });
    }

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
	response_action($action, $pkg->name, $pkg->arch);
    }

    foreach (@{ $state->{orphans_to_remove} }) {
	response_action('action-auto-remove', $_->name, $_->arch, );
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
	response_action('action-remove', $name, $arch);
    }

    # TODO There is no conflict checking !!

    exit 0;
}

sub response_action {
    my ($action, $name, $arch) = @_;
	printf("%%MDVPKG SELECTED %s %s@%s\n",
	       $action,
	       $name,
	       $arch);

}

sub response_error {
    my ($name, @args) = @_;
	printf("%%MDVPKG ERROR %s%s\n",
	       $name,
	       @args ? ' ' . join("\t", @args) : '');

}
