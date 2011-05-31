/*
 * Copyright (C) 2010-2011 Mandriva S.A <http://www.mandriva.com>
 * All rights reserved
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU Lesser General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., or visit: http://www.gnu.org/.
 *
 *
 * Author(s): J. Victor Martins <jvdm@mandriva.com>
 */

/**
 * _rpmutils - python extension module to acces rpm util functions
 */


#include <Python.h>
#include <stdint.h>
#include <rpmevr.h>


static PyObject *
_rpmutils_rpmvercmp(PyObject *self, PyObject *args)
{
     const char *first;
     const char *second;
     int cmp;

     if (!PyArg_ParseTuple(args, "ss", &first, &second)) {
	  return NULL;
     }

     cmp = rpmvercmp(first, second);
     return Py_BuildValue("i", cmp);
}

/*
 * Module initialization ...
 */

static PyMethodDef Methods[] = {
    {"rpmvercmp",  _rpmutils_rpmvercmp, METH_VARARGS,
     "Compare two RPM version strings, exactly like rpmvercmp()."},
    {NULL, NULL, 0, NULL}        /* Sentinel */
};

PyMODINIT_FUNC
init_rpmutils(void)
{
    (void) Py_InitModule("_rpmutils", Methods);
}

