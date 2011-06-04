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

use constant {
    STATE_SOLVING => 'state-resolving',
    STATE_DOWNLOADING => 'state-downloading',
    STATE_INSTALLING => 'state-installing',
    STATE_SEARCHING => 'state-searching',
};

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
	    task_exception($@);
	};
    }
}

#
# Backend responses
#

sub task_response {
    my ($tag, @args) = @_;

    my %value_converters = (
	'bool' => sub {
	    return $_[0] ? 'True' : 'False';
	},
	'str' => sub {
	    $_[0] =~ s|'|\\'|g;
	    return "'" . $_[0] . "'";
	},
	'int' => sub {
	    $_[0] =~ /\d+/ or die "$_[0] is not a number\n";
	    return $_[0];
	},
    );

    my $args_str = '';
    while (my $type = shift @args) {
	$args_str .= $value_converters{$type}->(shift @args) . ', ';
    }

    printf("\n%s\t%s\t(%s)\n", '%MDVPKG', $tag, $args_str);
}

sub task_signal {
    my ($signal_name, @args) = @_;
    task_response('SIGNAL', str => $signal_name, @args);
}

sub task_state_changed {
    my ($state) = @_;
    task_signal('StateChanged', str => $state);
}

sub task_exception {
    my ($message) = @_;
    task_response('EXCEPTION', str => $message);
}

sub task_error {
    my ($code, $message) = @_;
    task_response('ERROR', str => $code, str => $message);
}

sub task_done {
    task_response('DONE');
}

#
# Task Handlers
#

sub on_task__install_packages {
    my ($urpm, @names) = @_;

    @names or die "Missing package names to install\n";

    ## Search packages by name, getting their id ...

    task_state_changed(STATE_SEARCHING);
    my %packages;
    urpm::select::search_packages(
	$urpm, 
	\%packages,
	\@names, 
    );

    ## Lock urpmi and rpm databases ...

    # Here we lock urpmi & rpm databases
    # In third argument we can specified if the script must wait until urpmi or rpm
    # databases are locked
    my $lock = urpm::lock::urpmi_db($urpm, undef, wait => 0);
    my $rpm_lock = urpm::lock::rpm_db($urpm, 'exclusive');

    ## Resolve dependencies, get $state object ...

    task_state_changed(STATE_SOLVING);
    my $state = {};
    my $restart;
    $restart = urpm::select::resolve_dependencies(
	           $urpm,
	           $state,
	           \%packages,
	           auto_select => 0,
	       );

    # 4. Start urpm loop to download, remove and install packages ...

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
		die "Removable media found: $_[0]\n";
	    },
	    trans_log => sub {
		my ($mode, $urlfile, $percent, $total, $eta, $speed) = @_;

		my (undef, $file) = split(/: /, $urlfile);

		if ($mode eq 'start') {
		    $downloading or task_state_changed(STATE_DOWNLOADING);
		    $downloading ||= 1;
		    task_signal('DownloadStart', str => $file);
		}
		elsif ($mode eq 'progress') {
		    task_signal('Download',
				str => $file,
				str => $percent,
				str => $total,
				str => $eta,
				str => $speed);
		}
		elsif ($mode eq 'end') {
		    task_signal('DownloadDone', str => $file);
		}
		elsif ($mode eq 'error') {
		    # Error message is 3rd argument, saved in $percent
		    task_signal('DownloadError',
				str => $file,
				str => $percent);
		}
		else {
		    die "trans_log callback with unknown mode: $mode\n";
		}
	    },
	    bad_signature => sub {
		    print 'pk_print_error(PK_ERROR_ENUM_GPG_FAILURE, "Bad or missing GPG signatures");', "\n";
		    undef $lock;
		    undef $rpm_lock;
		    die;
	    },
	    trans_error_summary => sub {
		die "Not implemented callback: trans_error_summary\n";
	    },
	    inst => sub {
		my ($urpm, $type, $id, $subtype, $amount, $total) = @_;
		my $pkg = $urpm->{depslist}[$id];
		if ($subtype eq 'progress') {
		    task_signal('Install',
				str => scalar($pkg->fullname),
				str => $amount,
				str => $total);
		}
		elsif ($subtype eq 'start') {
		    task_signal('InstallStart',
				str => scalar($pkg->fullname),
				str => $total);
		}
	    },
	    trans => sub {
		my ($urpm, $type, $id, $subtype, $amount, $total) = @_;
		if ($subtype eq 'progress') {
		    task_signal('Preparing',
				str => $amount,
				str => $total);
		}
		elsif ($subtype eq 'stop') {
		    task_signal('PreparingDone')
		}
		elsif ($subtype eq 'start') {
		    task_signal('PreparingStart', str => $total);
		}
	    },
	    ask_yes_or_no => sub {
		# Return 1 = Return Yes
		return 1;
	    },
	    need_restart => sub {
		my ($need_restart_formatted) = @_;
		print "$_\n" foreach values %$need_restart_formatted;
	    },
	    completed => sub {
		undef $lock;
		undef $rpm_lock;
		task_response('DONE');
	    },
	    post_download => sub {
		$downloading = 0;
		task_state_changed(STATE_INSTALLING);
	    },
	    message => sub {
		my ($_title, $msg) = @_; # graphical title
		print $_title, $msg, "\n";
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
