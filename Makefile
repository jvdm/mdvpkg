NAME = mdvpkg
VERSION = $(shell python -c 'import mdvpkg; print mdvpkg.__version__')
TARBALL = $(NAME)-$(VERSION).tar.bz2

prefix = $(DESTDIR)/usr
confdir = $(DESTDIR)/etc

sbindir = $(prefix)/sbin
datadir = $(prefix)/share
docdir = $(datadir)/doc
libdir = $(prefix)/lib

mdvpkg_dir = $(datadir)/mandriva/$(NAME)
mdvpkg_docdir = $(docdir)/mandriva/$(NAME)
mdvpkg_libdir = $(libdir)/mandriva/$(NAME)
dbus_datadir = $(datadir)/dbus-1/system-services
dbus_confdir = $(confdir)/dbus-1/system.d


.PHONY: build $(TARBALL) install clean

build:
	python setup.py build_ext --inplace

tarball: $(TARBALL)
$(TARBALL):
	@git archive --prefix=$(NAME)-$(VERSION)/ \
	    --format=tar HEAD | bzip2 > $(TARBALL)
	@echo Created tarball: $(TARBALL) $(PREFIX)

install:
	install -m755 -d $(mdvpkg_dir) \
	                 $(mdvpkg_docdir) \
	                 $(mdvpkg_libdir) \
			 $(sbindir) \
	                 $(dbus_datadir) \
	                 $(dbus_confdir)
	cp -R mdvpkg/ $(mdvpkg_dir)
	cp -R backend/ $(mdvpkg_dir)
	cp -R -P doc/* $(mdvpkg_docdir)
	install -m755 bin/mdvpkgd $(sbindir)
	install -m644 dbus/*.conf $(dbus_confdir)
	install -m644 dbus/*.service $(dbus_datadir)
	install -m644 _rpmutils.so $(mdvpkg_libdir)

clean:
	@python setup.py clean --all --quiet 2> /dev/null
	@rm -f $(TARBALL)
	@rm -f _rpmutils.so
