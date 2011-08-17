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
use urpm::install qw();

use File::Basename qw(fileparse);

use FindBin;
use lib "$FindBin::Bin/";

use mdvpkg;


$| = 1;

binmode STDOUT, ':encoding(utf8)';
binmode STDIN, ':encoding(utf8)';

our $read_tasks = 1;
$SIG{TERM} = sub {
    $read_tasks = 0;
};

our $rpmdb_lock;
our $urpmdb_lock;


MAIN: {
    # Initializing urpmi ...
    my $urpm = urpm->new_parse_cmdline;
    urpm::media::configure($urpm);

    while ($read_tasks and defined(my $task_string = <>)) {
	chomp($task_string);
	my ($name, @args) = split(/\t/, $task_string);
	eval {
	    my $task_func = "on_task__$name";
	    defined $main::{$task_func} 
	        or die "Unknown task name: '$name'\n";
	    $main::{$task_func}->($urpm, @args);
	    return 1;
	}
	or do {
	    chomp($@);
	    response('exception', $@);
	    _unlock() if (defined $rpmdb_lock);
	};
    }
}


#
# Backend responses
#

sub response {
    printf("<mdvpkg> %s\n", join("\t", @_));
}

my ($progress_count, $progress_total);

sub init_progress {
    $progress_count = 0;
    $progress_total = $_[0]
}

sub progress {
    $progress_count += $_[0];
    response('callback', 'task_progress',
	     $progress_count, $progress_total)
}

sub _lock {
    my $urpm = shift @_;
    $urpmdb_lock = urpm::lock::urpmi_db($urpm, undef, wait => 0);
    $rpmdb_lock = urpm::lock::rpm_db($urpm, 'exclusive');
}

sub _unlock {
    $urpmdb_lock = undef;
    $rpmdb_lock = undef;
}

##
# _add_pkg
#     Add fullname and nvra entries to a pkg_map hash.
# :Parameters:
#     `$pkg_map` : hash_ref
#         The pkg_map hash to add entries
#     `$pkg` : URPM::Package
#         The pkg to grab fullname and nvra
#
sub _add_pkg {
    my $pkg_map = shift;
    my $pkg = shift;
    $pkg_map->{$pkg->fullname} = $pkg;
    $pkg_map->{join('-',
		    $pkg->name,
		    $pkg->version,
		    $pkg->release) . '.' . $pkg->arch} = $pkg;
}

#
# Task Handlers
#

sub on_task__commit {
    my ($urpm, @args) = @_;

    # Parse args ...
    my $installs = [];
    my $removes = [];
    foreach (@args) {
	my $list = s/^r:(.*)/$1/ ? $removes : $installs;
	push @$list, $_;
    }

    _lock();

    my ($restart, $state, $to_remove);
    eval {
	($restart, $state, $to_remove)
	    = mdvpkg::create_state($urpm, $installs, $removes);
    }
    or do {
	response('error', $@->{error}, @{ $@->{names} });
	return;
    };

    # Populate pkg_map ...
    my %pkg_map = ();
    foreach my $id (keys %{ $state->{selected} || {} }) {
	my $pkg = $urpm->{depslist}[$id];
	_add_pkg(\%pkg_map, $pkg);
    }
    while (my ($fn, $rej) = each %{ $state->{rejected} || {} }) {
	foreach (@{ $urpm->{depslist} }) {
	    if ($_->fullname eq $fn) {
		_add_pkg(\%pkg_map, $_);
	    }
	}
    }

    init_progress(1
		  + keys(%{ $state->{selected} || {} }) * 2
	          + @{ $to_remove || [] });

    progress(1);

    # Remove packages ...
    my $remove_count = @{ $to_remove || [] };
    if (@{ $to_remove || [] }) {
        urpm::install::install(
            $urpm,
            $to_remove,
            {},
            {},
            callback_report_uninst => sub {
                my @return = split(/ /, $_[0]);
                my $pkg = $pkg_map{$return[-1]};
                response('callback', 'remove_start',
			 mdvpkg::create_pkg_arg($pkg),
                         100, $remove_count);
                response('callback', 'remove_progress',
                         mdvpkg::create_pkg_arg($pkg), 100, 100);
                response('callback', 'remove_end',
                         mdvpkg::create_pkg_arg($pkg));
                progress(1);
            }
        );
    }

    if (%{ $state->{selected} || {} }) {
	# Start urpm loop to download, remove and install packages ...
	my $exit_code;
	my %task_info = (set => undef,
			 progress => 0);

	$exit_code = urpm::main_loop::run(
	    $urpm,
	    $state,
	    undef,
	    undef,
	    undef,
	    {
		completed => sub {
		    _unlock();
		    response('done');
		    # reload package data:
		    urpm::media::configure($urpm);
		},
		pre_removable => undef,
		post_removable => undef,
		copy_removable => sub {
		    die "removable media found: $_[0]\n";
		},
		trans_log => sub {
		    my ($mode,
			$urlfile,
			$percent,
			$total,
			$eta,
			$speed) = @_;
		    my $pkg = $pkg_map{fileparse($urlfile, '.rpm')};
		    if ($mode eq 'start') {
			response('callback',
				 'download_start',
				 mdvpkg::create_pkg_arg($pkg));
		    }
		    elsif ($mode eq 'progress') {
			response('callback',
				 'download_progress',
				 mdvpkg::create_pkg_arg($pkg),
				 $percent, $total, $eta, $speed);
		    }
		    elsif ($mode eq 'end') {
			response('callback', 'download_end',
				 mdvpkg::create_pkg_arg($pkg));
			progress(1);
		    }
		    elsif ($mode eq 'error') {
			# error message is the 3rd argument, $percent:
			response('callback', 'download_error',
				 mdvpkg::create_pkg_arg($pkg), $percent);
		    }
		    else {
			die "trans_log callback with unknown mode: $mode\n";
		    }
		},
		trans => sub {
		    my ($urpm, $type, $id, $subtype, $amount, $total) = @_;
		    if ($subtype eq 'start') {
			response('callback', 'preparing', $total);
		    }
		},
		inst => sub {
		    my ($urpm, $type, $id, $subtype, $amount, $total) = @_;
		    my $pkg = $urpm->{depslist}[$id];
		    if ($subtype eq 'progress') {
			response('callback',
				 'install_progress',
				 mdvpkg::create_pkg_arg($pkg),
				 $amount,
				 $total);
			if ($amount ==  $total) {
			    response('callback',
				     'install_end',
				     mdvpkg::create_pkg_arg($pkg));
			    progress(1);
			}
		    }
		    elsif ($subtype eq 'start') {
			$task_info{progress} += 1;
			response('callback', 'install_start',
				 mdvpkg::create_pkg_arg($pkg),
				 $total,
				 $task_info{progress});
		    }
		},
		ask_yes_or_no => sub {
		    # response('callback', 'ask');
		    # return <> =~ /|Y|y|Yes|yes|true|True|/;
		    return 1;
		},
		message => sub {
		    my ($title, $message) = @_;
		    response('callback', 'message',
			     $title, $message);
		},
		post_extract => sub {
		    my ($set,
			$transaction_sources,
			$transaction_sources_install) = @_;
		    $task_info{set} = $set;
		    $task_info{progress} = 0;
		},
		pre_check_sig => undef,
		check_sig => undef,
		bad_signature => sub {
		    response('callback', 'bad_signature');
		    _unlock();
		    die "bad signature\n";
		},
		post_download => sub {
		    # TODO Look for a cancellation flag so further
		    #      installation won't go
		},
		need_restart => sub {
		    my ($need_restart_formatted) = @_;
		    print "$_\n" foreach values %$need_restart_formatted;
		    response('callback', 'need_restart');
		},
		trans_error_summary => sub {
		    die "not implemented callback: trans_error_summary\n";
		},
		success_summary => undef,
	    }
	);
    }
    else {
	_unlock();
	response('done');
    }
}

sub on_task__search_files {
    my ($urpm, @files) = @_;
    
    # For each medium, we browse the xml info file, while looking for
    # files which matched with the search term given in argument. We
    # store results in a hash ...

    my %results;
    my %args;
    foreach my $medium (urpm::media::non_ignored_media($urpm)) {
	my $xml_info_file = urpm::media::any_xml_info(
	                        $urpm,
	                        $medium,
	                        qw( files summary ),
	                        undef,
	                        \&search_files_sync_logger_callback
	                    );
	$xml_info_file or next;

	require urpm::xml_info;
	require urpm::xml_info_pkg;

	my $F = urpm::xml_info::open_lzma($xml_info_file);
	my $fn;
	local $_;
	my @files = ();
	while (<$F>) {
	    chomp;
	    if (/<files/) {
		($fn) = /fn="(.*)"/;
	    } 
	    elsif (/^$args{pattern}$/ or ($args{fuzzy} and /$args{pattern}/)) {
		my $xml_pkg = urpm::xml_info_pkg->new({ fn => $fn });
		if (not exists $results{$fn}) {
		    $results{$fn} = { pkg => $xml_pkg,
				      files => [] };
		}
		push @{ $results{$fn}{files} }, $_;
	    }
	}
    }

    foreach my $fn (keys %results) {
	my $xml_pkg = $results{$fn}{pkg};
	my $py_str = sprintf("{'name': %s, "
			         . "'version': %s, "
			         . "'release': %s, " 
			         . "'arch': %s, "
			         . "'files': [",
			     py_str($xml_pkg->name),
			     py_str($xml_pkg->version),
			     py_str($xml_pkg->release),
			     py_str($xml_pkg->arch));
	$py_str .= sprintf('%s, ', py_str($_)) for (@{ $results{$fn}{files} });
	$py_str .= ']}';
	task_signal('PackageFiles', $py_str)
    }

    return 'exit-success';
}
