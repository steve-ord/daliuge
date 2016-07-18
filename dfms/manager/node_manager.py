#
#    ICRAR - International Centre for Radio Astronomy Research
#    (c) UWA - The University of Western Australia, 2014
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
"""
Module containing the NodeManager, which directly manages DROP instances, and
thus represents the bottom of the DROP management hierarchy.
"""

import importlib
import inspect
import logging
import os
import sys

from dfms import droputils
from dfms.exceptions import NoSessionException, SessionAlreadyExistsException
from dfms.lifecycle.dlm import DataLifecycleManager
from dfms.manager import repository
from dfms.manager.drop_manager import DROPManager
from dfms.manager.session import Session


logger = logging.getLogger(__name__)

def _functionAsTemplate(f):
    args, _, _, defaults = inspect.getargspec(f)

    # 'defaults' might be shorter than 'args' if some of the arguments
    # are not optional. In the general case anyway the optional
    # arguments go at the end of the method declaration, and therefore
    # a reverse iteration should yield the correct match between
    # arguments and their defaults
    defaults = list(defaults) if defaults else []
    defaults.reverse()
    argsList = []
    for i, arg in enumerate(reversed(args)):
        if i >= len(defaults):
            # mandatory argument
            argsList.append({'name':arg})
        else:
            # optional with default value
            argsList.append({'name':arg, 'default':defaults[i]})

    return {'name': inspect.getmodule(f).__name__ + "." + f.__name__, 'args': argsList}

class NodeManager(DROPManager):
    """
    A DROPManager that creates and holds references to DROPs.

    A NodeManager is the ultimate responsible of handling DROPs. It does so not
    directly, but via Sessions, which represent and encapsulate separate,
    independent DROP graph executions. All DROPs created by the
    different Sessions are also given to a common DataLifecycleManager, which
    takes care of expiring them when needed and replicating them.

    Since a NodeManager can handle more than one session, in principle only one
    NodeManager is needed for each computing node, thus its name.
    """

    def __init__(self, useDLM=True, dfmsPath=None, host=None, error_listener=None,
                 enable_luigi=False):
        self._dlm = DataLifecycleManager() if useDLM else None
        self._sessions = {}
        self._host = host

        # dfmsPath contains code added by the user with possible
        # DROP applications
        if dfmsPath:
            dfmsPath = os.path.expanduser(dfmsPath)
            if os.path.isdir(dfmsPath):
                logger.info("Adding %s to the system path", dfmsPath)
                sys.path.append(dfmsPath)

        # Error listener used by users to deal with errors coming from specific
        # Drops in whatever way they want
        if error_listener:
            if isinstance(error_listener, basestring):
                try:
                    parts   = error_listener.split('.')
                    module  = importlib.import_module('.'.join(parts[:-1]))
                except:
                    logger.exception('Creating the error listener')
                    raise
                error_listener = getattr(module, parts[-1])()
            if not hasattr(error_listener, 'on_error'):
                raise ValueError("error_listener doesn't contain an on_error method")
        self._error_listener = error_listener

        self._enable_luigi = enable_luigi

    def _check_session_id(self, session_id):
        if session_id not in self._sessions:
            raise NoSessionException(session_id)

    def createSession(self, sessionId):
        if sessionId in self._sessions:
            raise SessionAlreadyExistsException(sessionId)
        self._sessions[sessionId] = Session(sessionId, self._host, self._error_listener, self._enable_luigi)
        logger.info('Created session %s', sessionId)

    def getSessionStatus(self, sessionId):
        self._check_session_id(sessionId)
        return self._sessions[sessionId].status

    def quickDeploy(self, sessionId, graphSpec):
        self.createSession(sessionId)
        self.addGraphSpec(sessionId, graphSpec)
        return self.deploySession(sessionId)

    def linkGraphParts(self, sessionId, lhOID, rhOID, linkType):
        self._check_session_id(sessionId)
        self._sessions[sessionId].linkGraphParts(lhOID, rhOID, linkType)

    def addGraphSpec(self, sessionId, graphSpec):
        self._check_session_id(sessionId)
        self._sessions[sessionId].addGraphSpec(graphSpec)

    def getGraphStatus(self, sessionId):
        self._check_session_id(sessionId)
        return self._sessions[sessionId].getGraphStatus()

    def getGraph(self, sessionId):
        self._check_session_id(sessionId)
        return self._sessions[sessionId].getGraph()

    def deploySession(self, sessionId, completedDrops=[]):
        self._check_session_id(sessionId)
        session = self._sessions[sessionId]
        session.deploy(completedDrops=completedDrops)
        roots = session.roots

        logger.debug('Registering new Drops with the DLM and collecting their URIs')
        uris = {}
        for drop,_ in droputils.breadFirstTraverse(roots):
            uris[drop.uid] = drop.uri
            if self._dlm:
                self._dlm.addDrop(drop)

        return uris

    def destroySession(self, sessionId):
        self._check_session_id(sessionId)
        session = self._sessions.pop(sessionId)
        session.destroy()

    def getSessionIds(self):
        return list(self._sessions.keys())

    def getGraphSize(self, sessionId):
        self._check_session_id(sessionId)
        session = self._sessions[sessionId]
        return len(session._graph)

    def getTemplates(self):

        # TODO: we currently have a hardcoded list of functions, but we should
        #       load these repositories in a different way, like in this
        #       commented code
        #tplDir = os.path.expanduser("~/.dfms/templates")
        #if not os.path.isdir(tplDir):
        #    logger.warning('%s directory not found, no templates available' % (tplDir))
        #    return []
        #
        #templates = []
        #for fname in os.listdir(tplDir):
        #    if not  os.path.isfile(fname): continue
        #    if fname[-3:] != '.py': continue
        #
        #    with open(fname) as f:
        #        m = imp.load_module(fname[-3:], f, fname)
        #        functions = m.list_templates()
        #        for f in functions:
        #            templates.append(_functionAsTemplate(f))

        templates = []
        for f in repository.complex_graph, repository.pip_cont_img_pg, repository.archiving_app:
            templates.append(_functionAsTemplate(f))
        return templates

    def materializeTemplate(self, tpl, sessionId, **tplParams):

        self._check_session_id(sessionId)

        # tpl currently has the form <full.mod.path.functionName>
        parts = tpl.split('.')
        module = importlib.import_module('.'.join(parts[:-1]))
        tplFunction = getattr(module, parts[-1])

        # invoke the template function with the given parameters
        # and add the new graph spec to the session
        graphSpec = tplFunction(**tplParams)
        self.addGraphSpec(sessionId, graphSpec)

        logger.info('Added graph from template %s to session %s with params: %s', tpl, sessionId, tplParams)