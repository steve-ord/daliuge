#
#    ICRAR - International Centre for Radio Astronomy Research
#    (c) UWA - The University of Western Australia, 2015
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
import logging
import threading
import time

from dfms import doutils
from dfms.data_object import AppDataObject, AbstractDataObject
from dfms.ddap_protocol import ExecutionMode, DOStates
import luigi
import importlib

"""
Module containing the code that integrates our DataObjects with Luigi.
"""

logger = logging.getLogger(__name__)

class RunDataObjectTask(luigi.Task):
    """
    A Luigi Task that, for a given DataObject, either simply monitors it or
    actually executes it.

    Which of the two actions is performed depends on the nature of the
    DataObject and on the execution mode set in the DataObject's upstream
    objects: only AppDataObject DataObjects can be triggered automatically by
    their upstream objects. Since AppDataObject DataObjects only reference one
    upstream object (their producer) we need only to check the producer's
    execution mode, and if it's set to ExecutionMode.EXTERNAL then this task
    needs to manually execute the AppDataObject DataObject. In any other case this
    task simply waits until the DataObject's status has moved to COMPLETED.

    The complete() test for both cases is still the same, regardless of who is
    driving the execution: the DO must be COMPLETED and must exist.
    """

    data_obj  = luigi.Parameter()
    sessionId = luigi.Parameter()

    def __init__(self, *args, **kwargs):
        super(RunDataObjectTask, self).__init__(*args, **kwargs)

        do = self.data_obj
        self.execDO  = False
        if isinstance(do, AppDataObject):
            for inputDO in do.inputs:
                if inputDO.executionMode == ExecutionMode.EXTERNAL:
                    self.execDO = True

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("%s will execute or monitor DataObject %s/%s?: %s" % (self.__class__, do.oid, do.uid, ("execute" if self.execDO else "monitor")))

        if not self.execDO:
            self._evt = threading.Event()
            def setEvtOnCompleted(e):
                if e.status == DOStates.COMPLETED:
                    self._evt.set()
            do.subscribe(setEvtOnCompleted, 'status')

    def complete(self):
        return self.data_obj.isCompleted() and self.data_obj.exists()

    def run(self):
        if self.execDO:
            for inputDO in self.data_obj.inputs:
                self.data_obj.dataObjectCompleted(inputDO.uid)
        else:
            timeout = None
            expirationDate = self.data_obj.expirationDate
            if expirationDate != -1:
                now = time.time()
                timeout = expirationDate - now
            self._evt.wait(timeout)

    def requires(self):
        """
        The list of RunDataObjectTask that are required by this one.
        We use self.__class__ to create the new dependencies so this method
        doesn't need to be rewritten by all subclasses
        """
        re = []
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("Checking requirements for RunDataObjectTask %s/%s" %(self.data_obj.oid, self.data_obj.uid))

        # The requires() method will be called not only when creating the
        # initial tree of tasks, but also at runtime. For a given graph in a
        # DOM that has been connected with to other graph running in a different
        # DOM, it will mean that at runtime more upstream objects will be found
        # for those nodes connected to an external graph. We shouldn't schedule
        # those objects though, since they are scheduled by their own DOM.
        # We simply filter then the upObjs here to return only those that are
        # actually an instance of AbstractDataObject, thus removing any Pyro
        # Proxy instances from the list
        upObjs = doutils.getUpstreamObjects(self.data_obj)
        upObjs = filter(lambda do: isinstance(do, AbstractDataObject), upObjs)

        for req in upObjs:
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Added requirement %s/%s" %(req.oid, req.uid))
            re.append(RunDataObjectTask(req, self.sessionId))
        return re

class FinishGraphExecution(luigi.Task):
    """
    A Luigi Task that creates a DataObject graph and waits until it has finished
    its execution fully. The DataObject graph is created by feeding this Task
    with a property pgCreator parameter, which is the name of a function with
    no arguments that returns the top-level nodes of the graph.

    For a number of testing graphs please see the graphsRepository module.
    """
    sessionId = luigi.Parameter(default=time.time())
    pgCreator = luigi.Parameter(default='testGraphDODriven')

    def __init__(self, *args, **kwargs):
        super(FinishGraphExecution, self).__init__(*args, **kwargs)
        self._req    = None

        if isinstance(self.pgCreator, basestring):
            parts = self.pgCreator.split('.')
            module = importlib.import_module('.'.join(parts[:-1]))
            pgCreatorFn = getattr(module, parts[-1])
            roots = pgCreatorFn()
        else:
            roots = self.pgCreator

        self._roots = doutils.listify(roots)
        self._leaves = doutils.getLeafNodes(self._roots)
        self._completed = False

    def requires(self):
        if self._req is None:
            self._req = []
            for dob in self._leaves:
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug("Adding leaf DO as requirement to FinishGraphExecution: %s/%s" % (dob.oid, dob.uid))
                self._req.append(RunDataObjectTask(dob, self.sessionId))
        return self._req

    def run(self):
        self._completed = True

    def complete(self):
        return self._completed

    @property
    def leaves(self):
        return self._leaves[:]

    @property
    def roots(self):
        return self._roots[:]