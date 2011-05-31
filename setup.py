from distutils.core import setup, Extension
from subprocess import Popen, PIPE

opts = {'-I': [], '-l': []}

pkg_config = 'pkg-config --cflags --libs rpm'

for arg in Popen(pkg_config,
                 stdout=PIPE,
                 shell=True).communicate()[0].split():
    opt = opts.get(arg[:2])
    if opt is not None:
        opt.append(arg[2:])

rpmutils = Extension('_rpmutils',
                     libraries=opts['-l'],
                     include_dirs=opts['-I'],
                     sources=['_rpmutilsmodule.c'])

setup (ext_modules = [rpmutils])
