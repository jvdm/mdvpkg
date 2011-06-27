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

    # Search package by name ...
    my %packages;
    my $ret = urpm::select::search_packages(
	$urpm,
	\%packages,
	\@names,
	fuzzy => 0,
	no_substring => 1,
    );
    if (not $ret) {
	print "%MDVPKG ERROR error-not-found\n";
	exit 1;
    }

    # Resolve dependencies ...
    my %state;
    my $restart;
    $restart = urpm::select::resolve_dependencies(
	           $urpm,
	           \%state,
	           \%packages,
	           auto_select => 0,
	       );

    # Check %state and emit return data ...
    while (my ($id, $info) = each %{ $state{selected} }) {
	my $pkg = $urpm->{depslist}[$id];
	my $action;
	if (defined $info->{requested}) {
	    $action = 'action-install';
	}
	else {
	    $action = 'action-auto-install';
	}
	printf("%%MDVPKG SELECTED %s %s@%s\n",
	       $action,
	       $pkg->name,
	       $pkg->arch);
    }

    exit 0;
}
