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
    $urpm->{debug} = sub { print "[debug] @_\n" };
    $urpm->{debug_URPM} = sub { print "[debug_URPM] @_\n" };
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
	    = mdvpkg::create_state($urpm,
				   $installs,
				   $removes,
				   ignore_base => 1);
    }
    or do {
	response_error($@->{error}, @{ $@->{names} });
	exit 0;
    };

    my $pkg_map = mdvpkg::create_pkg_map($urpm, $state);

    # Check %state and emit return data ...
    CHECK_UNSELECTED: {
	my @unselected_names
	    = grep {
		!exists($state->{rejected}{$_}{removed})
		&& $state->{rejected}{$_}{backtrack}
		|| $state->{rejected}{$_}{closure}
	     } keys %{ $state->{rejected} || [] };
	foreach my $fullname (@unselected_names) {
            my $pkg_arg = $pkg_map->{$fullname};

            my $backtrack = $state->{rejected}{$fullname}{backtrack};
            if (@{ $backtrack->{unsatisfied} || [] }) {
                response_reject(
                    'reject-install-unsatisfied',
                    $pkg_arg,
                    map {
                        /\D/ ? $_ : $urpm->{depslist}[$_]->fullname;
                    } @{ $backtrack->{unsatisfied} }
                );
            }
	    foreach my $type (qw(conflicts keep)) {
		if (@{ $backtrack->{$type} || [] }) {
		    response_reject(
			'reject-install-conflicts',
			$pkg_arg,
			map {
			    $pkg_map->{$_};
			} @{ $backtrack->{$type} }
		    );
		}
	    }

	    # TODO Don't known how to handle theses cases.  They're
	    #      here from urpmi, and don't provide responses ...

	    if ($backtrack->{promote} && !$backtrack->{keep}) {
		print "trying to promote", join(", ",
						@{$backtrack->{promote}});
	    }

            my $closure = $state->{rejected}{$fullname}{closure};
	    if (%{ $closure || {} }) {
		my ($from) = grep {
				$_ ne 'disttag' && $_ ne 'distepoch'
			    } keys %$closure;
		my ($whyk) = grep {
				$_ ne 'disttag' && $_ ne 'distepoch'
			     } keys %{ $closure->{$from} };
		if ($whyk eq 'avoid'
		    && $state->{rejected}{$from}) {
		    response_reject(
			'reject-install-rejected-dependency',
			$pkg_arg,
			$pkg_map->{$from}
		    );
		}
	    }
	}
    }

    CHECK_REMOVED: {
	my @removed_names
	    = grep {
		$state->{rejected}{$_}{removed}
		&& !exists($state->{rejected}{$_}{removed}{asked})
		&& !$state->{rejected}{$_}{obsoleted}
	} keys %{ $state->{rejected} || {} };
	if (@removed_names) {
	    my %depends = ();
	    foreach (@removed_names) {
		my ($removed)
		    = keys %{ $state->{rejected}{$_}{removed} };
		push @{ $depends{$removed} ||= [] }, $_;
		delete $state->{rejected}{$_};
	    }
	    foreach (keys %depends) {
		delete $state->{rejected}{$_};
		response_reject(
		    'reject-remove-depends',
		    $pkg_map->{$_},
		    map {
			$pkg_map->{$_};
		    } @{ $depends{$_} });
	    }
	}
    }

    urpm::select::find_removed_from_basesystem(
	$urpm,
	URPM::DB::open(),
	$state,
	sub {
	    shift;
	    # 1 is returned when there is no rejected package:
	    $_[0] != 1 or return;
	    foreach (@_) {
		response_reject('reject-remove-basesystem',
			       $pkg_map->{$_});
		delete $state->{rejected}{$_};
	    }
	}
    );


    # Emit actions ...
    while (my ($id, $info) = each %{ $state->{selected} }) {
	my $pkg = $urpm->{depslist}[$id];
	my $action;
	if (defined $info->{requested}) {
	    $action = 'action-install';
	}
	else {
	    $action = 'action-auto-install';
	}
	response_action($action, $pkg);
    }


    foreach (grep {
	         $state->{rejected}{$_}{removed}
		 && !$state->{rejected}{$_}{obsoleted}
		 && exists($state->{rejected}{$_}{removed}{asked})
	     } keys %{$state->{rejected} || {}})
    {
	my $pkg = mdvpkg::pkg_from_fullname(
                      $urpm,
	              $_,
	              $state->{rejected}{$_}{disttag},
                      $state->{rejected}{$_}{distepoch}
	          );
	response_action('action-remove', $pkg);
    }

    # TODO There is no conflict checking !!

    exit 0;
}

sub response_reject {
    my ($reason, $pkg_arg, @args) = @_;
    my $args = join("\t",
		    'REJECTED',
		    $reason,
		    $pkg_arg,
		    @args);
    printf("%%MDVPKG %s\n", $args);
}

sub response_action {
    my ($action, $pkg) = @_;
    printf("%%MDVPKG SELECTED\t%s\t%s\n",
	   $action,
	   mdvpkg::create_pkg_arg($pkg));
}

sub response_error {
    my ($name, @args) = @_;
	printf("%%MDVPKG ERROR\t%s%s\n",
	       $name,
	       @args ? ' ' . join("\t", @args) : '');

}
