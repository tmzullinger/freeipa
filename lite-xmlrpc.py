#!/usr/bin/env python

import sys
import SimpleXMLRPCServer
import logging
import xmlrpclib
import re
import threading
import commands
from ipalib import api
from ipalib import config
from ipa_server import conn
from ipa_server.servercore import context
import ipalib.load_plugins
from ipalib.util import xmlrpc_unmarshal
import traceback

"""
In-tree XML-RPC server using SimpleXMLRPCServer.
"""

PORT=8888

class StoppableXMLRPCServer(SimpleXMLRPCServer.SimpleXMLRPCServer):
    """Override of TIME_WAIT"""
    allow_reuse_address = True

    def serve_forever(self):
        self.stop = False
        while not self.stop:
          self.handle_request()

class LoggingSimpleXMLRPCRequestHandler(SimpleXMLRPCServer.SimpleXMLRPCRequestHandler):
    """Overides the default SimpleXMLRPCRequestHander to support logging.
       Logs client IP and the XML request and response.
    """

    def parse(self, given):
        """Convert the incoming arguments into the format IPA expects"""
        args = []
        kw = {}
        for g in given:
            kw[g] = unicode(given[g])
        return (args, kw)

    def _dispatch(self, method, params):
        """Dispatches the XML-RPC method.

        Methods beginning with an '_' are considered private and will
        not be called.
        """

        # this is fine for our test server
        # uid = commands.getoutput('/usr/bin/id -u')
        uid = "500"
        krbccache = "FILE:/tmp/krb5cc_" + uid

        func = None
        try:
            try:
                # check to see if a matching function has been registered
                func = funcs[method]
            except KeyError:
                raise Exception('method "%s" is not supported' % method)
            (args, kw) = xmlrpc_unmarshal(*params)
            # FIXME: don't hardcode host and port
            context.conn = conn.IPAConn("localhost", 389, krbccache)
            logger.info("calling %s" % method)
            return func(*args, **kw)
        finally:
            # Clean up any per-request data and connections
#            for k in context.__dict__.keys():
#                del context.__dict__[k]
            pass

    def _marshaled_dispatch(self, data, dispatch_method = None):
        try:
            params, method = xmlrpclib.loads(data)

            # generate response
            if dispatch_method is not None:
                response = dispatch_method(method, params)
            else:
                response = self._dispatch(method, params)
            # wrap response in a singleton tuple
            response = (response,)
            response = xmlrpclib.dumps(response, methodresponse=1)
        except:
            # report exception back to client. This is needed to report
            # tracebacks found in server code.
            e_class, e = sys.exc_info()[:2]
            # FIXME, need to get this number from somewhere...
            faultCode = getattr(e_class,'faultCode',1)
            tb_str = ''.join(traceback.format_exception(*sys.exc_info()))
            faultString = tb_str
            response = xmlrpclib.dumps(xmlrpclib.Fault(faultCode, faultString))

        return response

    def do_POST(self):
        clientIP, port = self.client_address
	# Log client IP and Port
        logger.info('Client IP: %s - Port: %s' % (clientIP, port))
        try:
            # get arguments
            data = self.rfile.read(int(self.headers["content-length"]))

            # unmarshal the XML data
            params, method = xmlrpclib.loads(data)

            # Log client request
	    logger.info('Client request: \n%s\n' % data)

            response = self._marshaled_dispatch(
                    data, getattr(self, '_dispatch', None))

	    # Log server response
            logger.info('Server response: \n%s\n' % response)
	except Exception, e:
            # This should only happen if the module is buggy
            # internal error, report as HTTP server error
            print e
            self.send_response(500)
            self.end_headers()
        else:
            # got a valid XML-RPC response
            self.send_response(200)
            self.send_header("Content-type", "text/xml")
            self.send_header("Content-length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

            # shut down the connection
            self.wfile.flush()
            self.connection.shutdown(1)

# Set up our logger
logger = logging.getLogger('xmlrpcserver')
hdlr = logging.FileHandler('xmlrpcserver.log')
formatter = logging.Formatter("%(asctime)s  %(levelname)s  %(message)s")
hdlr.setFormatter(formatter)
logger.addHandler(hdlr)
logger.setLevel(logging.INFO)

# Set up the server
XMLRPCServer = StoppableXMLRPCServer(("",PORT), LoggingSimpleXMLRPCRequestHandler)

XMLRPCServer.register_introspection_functions()

api.finalize()

# Initialize our environment
config.set_default_env(api.env)
env_dict = config.read_config()
env_dict['server_context'] = True
api.env.update(env_dict)

# Get and register all the methods
for cmd in api.Command:
    logger.info("registering %s" % cmd)
    XMLRPCServer.register_function(api.Command[cmd], cmd)

funcs = XMLRPCServer.funcs

print "Listening on port %d" % PORT
try:
    XMLRPCServer.serve_forever()
except KeyboardInterrupt:
    XMLRPCServer.server_close()
    print "Server shutdown."
