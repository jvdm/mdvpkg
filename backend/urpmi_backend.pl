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

use File::Basename qw(fileparse);


$| = 1;

binmode STDOUT, ':encoding(utf8)';
binmode STDIN, ':encoding(utf8)';

our $read_tasks = 1;
$SIG{TERM} = sub {
    $read_tasks = 0;
};


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
	};
    }
}

#
# Backend responses
#

sub response {
    printf("<mdvpkg> %s\t\n", join("\t", @_));
}

#
# Task Handlers
#

sub on_task__install {
    my ($urpm, @names) = @_;

    @names or die "Missing package names to install\n";

    # Search packages by name, getting their id ...
    response('state', 'searching');
    my %packages;
    urpm::select::search_packages(
	$urpm, 
	\%packages,
	\@names, 
    );

    # Lock urpmi and rpm databases, in third argument we can specified
    # if the script must wait until urpmi or rpm databases are locked ...
    my $lock = urpm::lock::urpmi_db($urpm, undef, wait => 0);
    my $rpm_lock = urpm::lock::rpm_db($urpm, 'exclusive');

    # Resolve dependencies, get $state object ...
    response('state', 'resolving');
    my $state = {};
    my $restart;
    $restart = urpm::select::resolve_dependencies(
	           $urpm,
	           $state,
	           \%packages,
	           auto_select => 0,
	       );

    # Start urpm loop to download, remove and install packages ...
    my $exit_code;
    my $downloading = 0;
    $exit_code = urpm::main_loop::run(
	$urpm,
	$state,
	undef,
	undef, #\@ask_unselect,
	\%packages,
	{
	    copy_removable => sub {
		die "removable media found: $_[0]\n";
	    },
	    trans_log => sub {
		my ($mode, $urlfile, $percent, $total, $eta, $speed) = @_;
		my ($rpm_name) = fileparse($urlfile, '.rpm');

		if ($mode eq 'start') {
		    response('callback', 'download_start',
			     $rpm_name);
		}
		elsif ($mode eq 'progress') {
		    response('callback', 'download_progress',
			     $rpm_name, $percent, $total, $eta, $speed);
		}
		elsif ($mode eq 'end') {
		    response('callback', 'download_end',
			     $rpm_name);
		}
		elsif ($mode eq 'error') {
		    # error message is 3rd argument:
		    response('callback', 'download_error',
			     $rpm_name, $percent);
		}
		else {
		    die "trans_log callback with unknown mode: $mode\n";
		}
	    },
	    bad_signature => sub {
		response('callback', 'bad_signature');
		undef $lock;
		undef $rpm_lock;
		die "bad signature\n";
	    },
	    trans_error_summary => sub {
		die "not implemented callback: trans_error_summary\n";
	    },
	    inst => sub {
		my ($urpm, $type, $id, $subtype, $amount, $total) = @_;
		my $pkg = $urpm->{depslist}[$id];
		if ($subtype eq 'progress') {
		    response('callback', 'install_progress',
			     scalar $pkg->fullname, $amount, $total);
		}
		elsif ($subtype eq 'start') {
		    response('callback', 'install_start',
			     scalar $pkg->fullname, $total);
		}
	    },
	    trans => sub {
		my ($urpm, $type, $id, $subtype, $amount, $total) = @_;
		if ($subtype eq 'progress') {
		    response('callback', 'trans_progress',
			    $amount, $total);
		}
		elsif ($subtype eq 'stop') {
		    response('callback', 'trans_stop')
		}
		elsif ($subtype eq 'start') {
		    response('callback', 'trans_start',
			     $total);
		}
	    },
	    ask_yes_or_no => sub {
		response('callback', 'ask');
		return <> =~ /|Y|y|Yes|yes|true|True|/;
	    },
	    need_restart => sub {
		my ($need_restart_formatted) = @_;
		print "$_\n" foreach values %$need_restart_formatted;
		response('callback', 'nedd_restart');
	    },
	    completed => sub {
		undef $lock;
		undef $rpm_lock;
		response('done');
	    },
	    post_download => sub {
		response('callback', 'post_download');
	    },
	    message => sub {
		my ($title, $message) = @_;
		response('callback', 'message',
			 $title, $message);
	    }
	}
    );    
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
