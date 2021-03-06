#
#    ICRAR - International Centre for Radio Astronomy Research
#    (c) UWA - The University of Western Australia, 2017
#    Copyright by UWA (in the framework of the ICRAR)
#    All rights reserved
#
#    This library is free software; you can redistribute it and/or
#    modify it under the terms of the GNU Lesser General Public
#    License as published by the Free Software Foundation; either
#    version 2.1 of the License, or (at your option) any later version.
#
#    This library is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#    Lesser General Public License for more details.
#
#    You should have received a copy of the GNU Lesser General Public
#    License along with this library; if not, write to the Free Software
#    Foundation, Inc., 59 Temple Place, Suite 330, Boston,
#    MA 02111-1307  USA
#

import ctypes
import functools
import logging

import six

from ..ddap_protocol import AppDROPStates
from ..drop import AppDROP, BarrierAppDROP
from ..exceptions import InvalidDropException


logger = logging.getLogger(__name__)

_read_cb_type = ctypes.CFUNCTYPE(ctypes.c_size_t,
                                 ctypes.POINTER(ctypes.c_char),
                                 ctypes.c_size_t)

_write_cb_type = ctypes.CFUNCTYPE(ctypes.c_size_t,
                                  ctypes.POINTER(ctypes.c_char),
                                  ctypes.c_size_t)

_app_running_cb_type = ctypes.CFUNCTYPE(None)

_app_done_cb_type = ctypes.CFUNCTYPE(None, ctypes.c_int)

class CDlgInput(ctypes.Structure):
    _fields_ = [('uid', ctypes.c_char_p),
                ('oid', ctypes.c_char_p),
                ('name', ctypes.c_char_p),
                ('status', ctypes.c_int),
                ('read', _read_cb_type)]

class CDlgStreamingInput(ctypes.Structure):
    _fields_ = [('uid', ctypes.c_char_p),
                ('oid', ctypes.c_char_p),
                ('name', ctypes.c_char_p)]

class CDlgOutput(ctypes.Structure):
    _fields_ = [('uid', ctypes.c_char_p),
                ('oid', ctypes.c_char_p),
                ('name', ctypes.c_char_p),
                ('write', _write_cb_type)]

class CDlgApp(ctypes.Structure):
    _fields_ = [('appname', ctypes.c_char_p),
                ('uid', ctypes.c_char_p),
                ('oid', ctypes.c_char_p),
                ('inputs', ctypes.POINTER(CDlgInput)),
                ('n_inputs', ctypes.c_uint),
                ('streaming_inputs', ctypes.POINTER(CDlgStreamingInput)),
                ('n_streaming_inputs', ctypes.c_uint),
                ('outputs', ctypes.POINTER(CDlgOutput)),
                ('n_outputs', ctypes.c_uint),
                ('running', _app_running_cb_type),
                ('done', _app_done_cb_type),
                ('data', ctypes.c_void_p),]

class DynlibAppBase(object):

    def initialize(self, **kwargs):
        super(DynlibAppBase, self).initialize(**kwargs)

        if 'lib' not in kwargs:
            raise InvalidDropException(self, "library not specified")

        # Try with a simple name, or as full path
        from ctypes.util import find_library
        libname = kwargs.pop('lib')
        lib = find_library(libname) or libname

        self.lib = ctypes.cdll.LoadLibrary(lib)
        expected_functions = ('init', 'run')
        for fname in expected_functions:
            if hasattr(self.lib, fname):
                continue
            raise InvalidDropException(self, "%s doesn't have function %s" % (lib, fname))

        # Create the initial contents of the C dlg_app_info structure
        # We pass no inputs because we don't know them (and don't need them)
        # at this point yet.
        # The running and done callbacks are also NULLs
        self._c_app = CDlgApp(None,six.b(self.uid), six.b(self.oid),
                                   None, 0, None, 0, None, 0,
                                   ctypes.cast(None, _app_running_cb_type),
                                   ctypes.cast(None, _app_done_cb_type),
                                   None)

        # Collect the rest of the parameters to pass them down to the library
        # We need to keep them in a local variable so when we expose them to
        # the app later on via pointers we still have their contents
        local_params = [(six.b(str(k)), six.b(str(v))) for k, v in kwargs.items()]
        logger.debug("Extra parameters passed to application: %r", local_params)

        # Wrap in ctypes
        str_ptr_type = ctypes.POINTER(ctypes.c_char_p)
        two_str_type = (ctypes.c_char_p * 2)
        app_params = [two_str_type(k, v) for k, v in local_params]
        app_params.append(None)
        params = (str_ptr_type * len(app_params))(*app_params)

        # Let the shared library initialize this app
        if self.lib.init(ctypes.pointer(self._c_app), params):
            raise InvalidDropException(self, "%s app failed during initialization" % (lib,))

        self._c_outputs = []

    def addOutput(self, outputDrop, back=True):
        super(DynlibAppBase, self).addOutput(outputDrop, back)

        # Update the list of outputs on our app structure
        output = self._to_c_output(outputDrop)
        app = self._c_app
        if not app.n_outputs:
            outputs = (CDlgOutput * 1)(output)
        else:
            outputs = (CDlgOutput * (app.n_outputs + 1))(*self._c_outputs)
            outputs[-1] = output
        self._c_outputs = outputs
        app.outputs = outputs
        app.n_outputs += 1

    def _to_c_output(self, o):
        w = _write_cb_type(functools.partial(self._write_to_output, o.write))
        return CDlgOutput(six.b(o.uid), six.b(o.oid), six.b(o.name), w)

    def _write_to_output(self, output_write, buf, n):
        return output_write(buf[:n])


class DynlibStreamApp(DynlibAppBase, AppDROP):

    def initialize(self, **kwargs):
        super(DynlibStreamApp, self).initialize(**kwargs)

        # Set up callbacks for the library to signal they the application
        # is running, and that it has ended
        def _running():
            self.execStatus = AppDROPStates.RUNNING
        def _done(status):
            self.execStatus = status
            self._notifyAppIsFinished()

        self._c_app.running = _app_running_cb_type(_running)
        self._c_app.done = _app_done_cb_type(_done)

    def dataWritten(self, uid, data):
        app_p = ctypes.pointer(self._c_app)
        self.lib.data_written(app_p, six.b(uid), data, len(data))

    def dropCompleted(self, uid, drop_state):
        app_p = ctypes.pointer(self._c_app)
        self.lib.drop_completed(app_p, six.b(uid), drop_state)

    def addInput(self, inputDrop, back=True):
        super(DynlibStreamApp, self).addInput(inputDrop, back)
        self._c_app.n_inputs += 1

    def addStreamingInput(self, streamingInputDrop, back=True):
        super(DynlibStreamApp, self).addStreamingInput(streamingInputDrop, back)
        self._c_app.n_streaming_inputs += 1


class DynlibApp(DynlibAppBase, BarrierAppDROP):

    def run(self):

        # read / write callbacks
        def _read(input_read, desc, buf, n):
            x = input_read(desc, n)
            ctypes.memmove(buf, x, len(x))
            return len(x)

        # Update our C structure to include inputs, which we open for reading
        inputs = []
        opened_info = []
        for i in self.inputs:
            desc = i.open()
            opened_info.append((i, desc))
            r = _read_cb_type(functools.partial(_read, i.read, desc))
            inputs.append(CDlgInput(six.b(i.uid), six.b(i.oid), six.b(i.name), i.status, r))
        self._c_app.inputs = (CDlgInput * len(inputs))(*inputs)
        self._c_app.n_inputs = len(inputs)

        try:
            self.lib.run(ctypes.pointer(self._c_app))
        finally:
            for x, desc  in opened_info:
                x.close(desc);
