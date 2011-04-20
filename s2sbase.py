""" A jaboard XMPP face server as a standalone server via s2s - base code. 
"""
# pylint: disable-msg=W0703
# :W0703: *Catch "Exception"*

from twisted.application import service, strports
from twisted.internet import protocol
from twisted.internet import reactor
from twisted.words.xish import domish  # For creating messages to send.
from wokkel import component, server, xmppim
import sys

import simplejson
import traceback  # Debug on exceptions.


cfg = {
  'DOMAIN': 'testard.hell.orts.ru',
  'LOG_TRAFFIC': True,
  'S2S_PORT': 'tcp:5299:interface=0.0.0.0',
  'SECRET': 'xzcasdaxca',
}


## stdout log
server.log.startLogging(sys.stdout, setStdout=False)


def relayhandler(data):
    sys.stdout.write("%r\n" % data)


class AvailabilityPresenceX(xmppim.AvailabilityPresence):
    """ Slightly extended/fixed class that saves <x> element from the
    presence stanze.  """
    childParsers = {
      ## Other parsers should be 'accumulated' from AvailabilityPresence.
      ('vcard-temp:x:update', 'x'): '_childParser_photo',
    }
    
    photo = None

    def _childParser_photo(self, element):
        """ Adds the 'photo' data if such element exists. """
        for child in element.elements():  # usually only one.
            if child.name == 'photo':
                if child.children:
                    self.photo = child.children[0]
                else:
                    self.photo = ""

class PresenceHandler(xmppim.PresenceProtocol):
    """ Presence XMPP subprotocol - accept+relay handler.

    This handler blindly accepts incoming presence subscription requests,
    confirms unsubscription requests and responds to presence probes.

    Note that this handler does not remember any contacts, so it will not
    send presence when starting.  """

    def __init__(self, relayhandler, *ar, **kwar):
        """ @param relayhandler: a function that relays the provided dict
        data to the next layer.  """
        xmppim.PresenceProtocol.__init__(self, *ar, **kwar)
        self.statustext = u"Greetings"
        self.subscribedto = {}
        self.relayhandler = relayhandler
        # semi-hack to support vCard processing.:
        self.presenceTypeParserMap['available'] = AvailabilityPresenceX
        # ? Ask django-xmppface to ask self to send presences?

    def subscribedReceived(self, presence):
        """ Subscription approval confirmation was received. """
        server.log.msg(" ------- D: A: subscribedReceived.")
        self.relayhandler({'auth': 'subscribed',
           'src': presence.sender.full(), 'dst': presence.recipient.full()})

    def unsubscribedReceived(self, presence):
        """ Unsubscription confirmation was received. """
        server.log.msg(" ------- D: A: unsubscribedReceived.")
        self.relayhandler({'auth': 'unsubscribed',
           'src': presence.sender.full(), 'dst': presence.recipient.full()})

    def subscribeReceived(self, presence):
        """ Subscription request was received.
        Always grant permission to see our presence. """
        server.log.msg(" ------- D: A: subscribeReceived.")
        self.subscribed(recipient=presence.sender,
                        sender=presence.recipient)
        self.available(recipient=presence.sender,
                       status=self.statustext,
                       sender=presence.recipient)
        # Ask for subscription in turn.
        # ? Need some extracheckings for that?
        server.log.msg(" ------- D: A: Requesting mutual subscription... ")
        self.subscribe(recipient=presence.sender,
                       sender=presence.recipient)
        self.relayhandler({'auth': 'subscribe',
           'src': presence.sender.full(), 'dst': presence.recipient.full()})

    def unsubscribeReceived(self, presence):
        """ Unsubscription request was received.
        Always confirm unsubscription requests. """
        server.log.msg(" ------- D: A: unsubscribeReceived.")
        self.unsubscribed(recipient=presence.sender,
                          sender=presence.recipient)
        self.relayhandler({'auth': 'unsubscribe',
           'src': presence.sender.full(), 'dst': presence.recipient.full()})

    def availableReceived(self, presence):
        """ Available presence was received. """
        server.log.msg(" ------- D: A: availableReceived. show: %r" % (
          presence.show,))
        show = presence.show or "online"
        self.relayhandler({'stat': show,
           'photo': getattr(presence, 'photo', None),
           'src': presence.sender.full(), 'dst': presence.recipient.full()})

    def unavailableReceived(self, presence):
        """ Unavailable presence was received. """
        server.log.msg(" ------- D: A: unavailableReceived.")
        self.relayhandler({'stat': 'unavail',
           'src': presence.sender.full(), 'dst': presence.recipient.full()})

    def probeReceived(self, presence):
        """ A presence probe was received.
        Always send available presence to whoever is asking. """
        server.log.msg(" ------- D: A: probeReceived.")
        self.available(recipient=presence.sender,
                       status=self.statustext,
                       sender=presence.recipient)


class MessageHandler(xmppim.MessageProtocol):
    """ Message XMPP subprotocol - relay handler. """

    def __init__(self, relayhandler, *ar, **kwar):
        # ? XXX: Make some base class with this __init__? Possible?
        xmppim.MessageProtocol.__init__(self, *ar, **kwar)
        self.relayhandler = relayhandler

    def onMessage(self, message):
        """ A message stanza was received. """
        server.log.msg(" ------- D: A: onMessage.")

        # Ignore error messages
        if message.getAttribute('type') == 'error':
            server.log.msg(" -------------- D: W: error-type message.")
            return

        ## Note: possible types are 'chat' and no type (i.e. plain non-chat
        ## message).
        # ? But are there other types besides those and 'error' (and
        # 'headline?
        try:
            msgtype = message.getAttribute('type')
            if msgtype == 'headline':
                # ? Something interesting in those?
                # (see note2.txt for example)
                return  # ...or just skip them.
            if not (msgtype == 'chat'):
                # For now - log them.
                server.log.msg(" ------- !! D: message of type %r." % msgtype)
            try:
                body = message.body.children[0]
            except Exception, e:
                server.log.err("onMessage: error retreiving body.")
                server.log.err("onMessage:  ... message probaby was %r." % (
                  message.toXml(),))
                raise e

            # Dump all the interesting parts of data to the processing.
            self.relayhandler(
              {'src': message.getAttribute('from'),
               'dst': message.getAttribute('to'),
               'body': message.body.children[0],
               'id': message.getAttribute('id'),
               'type': message.getAttribute('type'),
              })
        except Exception:
            # Something went pretty.wrong.
            server.log.err(" -------------- E: onMessage: exception.")
            traceback.print_exc()


## vCard support stuff.
try:
    # pylint: disable-msg=E0611,F0401
    # :E0611: *No name %r in module %r*  - d'uh.
    from twisted.words.protocols.xmlstream import XMPPHandler
except ImportError:
    from wokkel.subprotocols import XMPPHandler

VCARD_RESPONSE = "/iq[@type='result']/vCard[@xmlns='vcard-temp']"


class VCardHandler(XMPPHandler):
    """ Subprotocol handler that processes received vCard iq responses. """

    def __init__(self, relayhandler, *ar, **kwar):
        XMPPHandler.__init__(self, *ar, **kwar)
        self.relayhandler = relayhandler

    def connectionInitialized(self):
        """ Called when the XML stream has been initialized.
        Sets up an observer for incoming stuff. """
        self.xmlstream.addObserver(VCARD_RESPONSE, self.onVcard)

    # pylint: disable-msg=R0201
    # *Method could be a function* - should be overridable.
    def onVcard(self, iq):
        """ iq response with vCard data received """
        def _jparse(elem):
            """ Parses the provided XML element, returning recursed dict
            with its data (ignoring *some* text nodes).  """
            if not elem.firstChildElement():  # nowhere to recurse to.
                return unicode(elem)  # perhaps there's some string, then.
            outd = {}
            for child in elem.elements():  # * ignoring other text nodes,
                # * ...and overwriting same-name siblings, apparently.
                outd[child.name] = _jparse(child)
            return outd

        server.log.msg("onVcard. iq: %r" % iq)
        server.log.msg(" ... %r" % iq.children)
        el_vcard = iq.firstChildElement()
        if el_vcard:
            self.relayhandler(
              {'src': iq.getAttribute('from'),
               'dst': iq.getAttribute('to'),
               'vcard': _jparse(el_vcard)})


def ProcessData(l_msgHandler, dataline):
    """ Called from OutqueueHandler.dataReceived for an actual combined data
    ready for processing (which is determined by newlines in the stream,
    actually).  """
    try:
        x = simplejson.loads(dataline)
        server.log.msg(" ------- D: Got JSON line of length %d:" % (
          len(dataline)))
        # server.log.msg(" --    %r        --\n  " % x)
        if 'class' in x:
            response = domish.Element((None, x['class']))
            # Values are supposed to be always present here:
            response['to'] = x['dst']
            response['from'] = x['src']
            for extranode in ('type', 'xml:lang'):
                if extranode in x:
                    response[extranode] = x[extranode]
            if 'content' in x:
                # We're provided with raw content already.
                # It is expected to be valid XML.
                # (if not - remote server will probably drop the s2s
                # connection)
                ## ? Process it with BeautifulSoup? :)
                response.addRawXml(x['content'])
            if x['class'] == 'message':
                # Create and send a response.
                response['type'] = response.getAttribute('type') \
                  or 'chat'
            # Everything else should be in place already for most types.
            # XXX: This throws some weird errors but works.
            l_msgHandler.send(response)
        else:  # not 'class'
            pass  # Nothing else implemented yet.
    except ValueError:
        server.log.err(" -------------- E: Failed processing: "
         "%r" % dataline)


class OutqueueHandler(protocol.Protocol):
    """ Relay for sending messages through the outqueue. """

    datatemp = ""  # for holding partially reveived data
    dataend = "\n"  # End character for splitting datastream.

    def __init__(self, l_msgHandler):
        # protocol.Protocol.__init__(self)
        self.msgHandler = l_msgHandler
    
    def __call__(self):
        """ Hack-method that returns self, for passing an
        already-instantiated class to twisted.internet.protocol.Factory.  """
        return self

    def connectionMade(self):
        server.log.msg(" D: OutqueueHandler: connection received.\n")

    def dataReceived(self, data):
        # Process received data for messages to send.
        server.log.msg(u" ------- D: Received data (%r) of len %d with %d "
          "newlines." % (type(data), len(data), data.count("\n")))
        if self.dataend in data:  # final chunk.
            # Unlike splitlines(), this returns an empty string at the
            # end if data ends with newline.
            datas = data.split(self.dataend)
            ProcessData(self.msgHandler, self.datatemp + datas[0])
            for datachunk in datas[1:-1]:
                # suddenly, more complete messages?
                ProcessData(self.msgHandler, datachunk)
            # Data after the last newline, usually empty.
            self.datatemp = datas[-1]
        else:  # partial data.
            self.datatemp += data


application = service.Application("xtestcon s2s server")

router = component.Router()

serverService = server.ServerService(router, domain=cfg['DOMAIN'],
  secret=cfg['SECRET'])
serverService.logTraffic = cfg['LOG_TRAFFIC']

s2sFactory = server.XMPPS2SServerFactory(serverService)
s2sFactory.logTraffic = cfg['LOG_TRAFFIC']
s2sService = strports.service(cfg['S2S_PORT'], s2sFactory)
s2sService.setServiceParent(application)

internalRouterComponent = component.InternalComponent(router,
  cfg['DOMAIN'])
internalRouterComponent.logTraffic = cfg['LOG_TRAFFIC']
internalRouterComponent.setServiceParent(application)

presenceHandler = PresenceHandler(relayhandler)
presenceHandler.setHandlerParent(internalRouterComponent)

vcardHandler = VCardHandler(relayhandler)
vcardHandler.setHandlerParent(internalRouterComponent)

msgHandler = MessageHandler(relayhandler)
msgHandler.setHandlerParent(internalRouterComponent)

# outqueue.
outqueueHandler = OutqueueHandler(msgHandler)
# OutqueueHandler is hacked so it will just return self when called 'for
# instantiation' there.
# (Similarly hacky way would be to give
#   lambda: OutqueueHandler(msgHandler)
# as protocol. Less hacky way would probably be a class generator)
outFactory = protocol.Factory()
outFactory.protocol = outqueueHandler  # an instance, not class!
outService = strports.service('unix:%s:mode=770' %
  'testsocket', outFactory)
outService.setServiceParent(application)

def xt_set_it():
    from twisted.application.app import startApplication
    from twisted.internet import reactor
    startApplication(application, False)
    # reactor.run()
    return reactor
