from http.server import BaseHTTPRequestHandler
from io import BytesIO
import hashlib
import base64
import socket
import struct
import errno
import codecs
from collections import deque
from select import select
import time

def _check_unicode(val):
    return isinstance(val, str)

class HTTPRequest(BaseHTTPRequestHandler):
    def __init__(self, request_text):
        self.rfile = BytesIO(request_text)
        self.raw_requestline = self.rfile.readline()
        self.error_code = self.error_message = None
        self.parse_request()

class WebSocket(object):

    STREAM = 0x0
    TEXT = 0x1
    BINARY = 0x2

    CLOSE = 0x8
    PING = 0x9
    PONG = 0xA

    HEADERB1 = 1
    HEADERB2 = 3
    LENGTHSHORT = 4
    LENGTHLONG = 5
    MASK = 6
    PAYLOAD = 7

    _VALID_STATUS_CODES = [1000, 1001, 1002, 1003, 1007, 1008, 1009, 1010, 1011, 3000, 3999, 4000, 4999]

    HANDSHAKE_STR = (
       "HTTP/1.1 101 Switching Protocols\r\n"
       "Upgrade: WebSocket\r\n"
       "Connection: Upgrade\r\n"
       "Sec-WebSocket-Accept: %(acceptstr)s\r\n\r\n"
    )

    FAILED_HANDSHAKE_STR = (
       "HTTP/1.1 426 Upgrade Required\r\n"
       "Upgrade: WebSocket\r\n"
       "Connection: Upgrade\r\n"
       "Sec-WebSocket-Version: 13\r\n"
       "Content-Type: text/plain\r\n\r\n"
       "This service requires use of the WebSocket protocol\r\n"
    )

    GUID_STR = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'

    def __init__(self, server, sock, address):
    
        self.server = server
        self.client = sock
        self.address = address

        self.handshaked = False
        self.headerbuffer = bytearray()
        self.headertoread = 2048

        self.fin = 0
        self.data = bytearray()
        self.opcode = 0
        self.hasmask = 0
        self.maskarray = None
        self.length = 0
        self.lengtharray = None
        self.index = 0
        self.request = None
        self.usingssl = False
        self.last_seen = 0

        self.frag_start = False
        self.frag_type = WebSocket.BINARY
        self.frag_buffer = None
        self.frag_decoder = codecs.getincrementaldecoder('utf-8')(errors='strict')
        self.closed = False
        self.sendq = deque()

        self.state = WebSocket.HEADERB1

        # restrict the size of header and payload for performance reasons
        self.maxheader = 65536 # 64k
        self.maxpayload = 409600 # 400k

    def handleMessage(self):
        """
            Called when websocket frame is received.
            To access the frame data call self.data.

            If the frame is Text then self.data is a unicode object.
            If the frame is Binary then self.data is a bytearray object.
        """
        pass

    def handleConnected(self):
        """
            Called when a websocket client connects to the server.
        """
        pass

    def handleClose(self):
        """
            Called when a websocket server gets a Close frame from a client.
        """
        pass

    def _handlePacket(self):
        if self.opcode == WebSocket.CLOSE:
            pass
        elif self.opcode == WebSocket.STREAM:
            pass
        elif self.opcode == WebSocket.TEXT:
            pass
        elif self.opcode == WebSocket.BINARY:
            pass
        elif self.opcode == WebSocket.PONG or self.opcode == WebSocket.PING:
            if len(self.data) > 125:
                raise Exception('control frame length can not be > 125')
        else:
            # unknown or reserved opcode so just close
            raise Exception('unknown opcode')

        if self.opcode == WebSocket.CLOSE:
            status = 1000
            reason = u''
            length = len(self.data)

            if length == 0:
                pass
            elif length >= 2:
                status = struct.unpack_from('!H', self.data[:2])[0]
                reason = self.data[2:]

                if status not in WebSocket._VALID_STATUS_CODES:
                    status = 1002

                if len(reason) > 0:
                    try:
                        reason = reason.decode('utf8', errors='strict')
                    except:
                        status = 1002
            else:
                status = 1002

            self.close(status, reason)
            return

        elif self.fin == 0:
            if self.opcode != WebSocket.STREAM:
                if self.opcode == WebSocket.PING or self.opcode == WebSocket.PONG:
                    raise Exception('control messages can not be fragmented')

                self.frag_type = self.opcode
                self.frag_start = True
                self.frag_decoder.reset()

                if self.frag_type == WebSocket.TEXT:
                    self.frag_buffer = []
                    utf_str = self.frag_decoder.decode(self.data, final = False)
                    if utf_str:
                        self.frag_buffer.append(utf_str)
                else:
                    self.frag_buffer = bytearray()
                    self.frag_buffer.extend(self.data)

            else:
                if self.frag_start is False:
                    raise Exception('fragmentation protocol error')

                if self.frag_type == WebSocket.TEXT:
                    utf_str = self.frag_decoder.decode(self.data, final = False)
                    if utf_str:
                        self.frag_buffer.append(utf_str)
                else:
                    self.frag_buffer.extend(self.data)

        else:
            self.last_seen = int(time.time())
            if self.opcode == WebSocket.STREAM:
                if self.frag_start is False:
                    raise Exception('fragmentation protocol error')

                if self.frag_type == WebSocket.TEXT:
                    utf_str = self.frag_decoder.decode(self.data, final = True)
                    self.frag_buffer.append(utf_str)
                    self.data = u''.join(self.frag_buffer)
                else:
                    self.frag_buffer.extend(self.data)
                    self.data = self.frag_buffer

                self.handleMessage()

                self.frag_decoder.reset()
                self.frag_type = WebSocket.BINARY
                self.frag_start = False
                self.frag_buffer = None

            elif self.opcode == WebSocket.PING:
                self._sendMessage(False, WebSocket.PONG, self.data)

            elif self.opcode == WebSocket.PONG:
                pass

            else:
                if self.frag_start is True:
                    raise Exception('fragmentation protocol error')

                if self.opcode == WebSocket.TEXT:
                    try:
                        self.data = self.data.decode('utf8', errors='strict')
                    except Exception as exp:
                        raise Exception('invalid utf-8 payload') from exp

                self.handleMessage()


    def _handleData(self):
        # do the HTTP header and handshake
        if self.handshaked is False:

            data = self.client.recv(self.headertoread)
            if not data:
                raise Exception('remote socket closed')

            else:
                # accumulate
                self.headerbuffer.extend(data)

                if len(self.headerbuffer) >= self.maxheader:
                    raise Exception('header exceeded allowable size')

                # indicates end of HTTP header
                if b'\r\n\r\n' in self.headerbuffer:
                    self.request = HTTPRequest(self.headerbuffer)

                    # handshake rfc 6455
                    try:
                        key = self.request.headers['Sec-WebSocket-Key']
                        k = key.encode('ascii') + WebSocket.GUID_STR.encode('ascii')
                        k_s = base64.b64encode(hashlib.sha1(k).digest()).decode('ascii')
                        hStr = WebSocket.HANDSHAKE_STR % {'acceptstr': k_s}
                        self.sendq.append((WebSocket.BINARY, hStr.encode('ascii')))
                        self.handshaked = True
                        self.last_seen = int(time.time())
                        self.handleConnected()
                    except Exception as e:
                        hStr = WebSocket.FAILED_HANDSHAKE_STR
                        self._sendBuffer(hStr.encode('ascii'), True)
                        self.client.close()
                        raise Exception('handshake failed: %s' % str(e))

        # else do normal data
        else:
            data = self.client.recv(16384)
            if not data:
                raise Exception("remote socket closed")

            for d in data:
                self._parseMessage(d)

    def close(self, status = 1000, reason = u''):
        """
           Send Close frame to the client. The underlying socket is only closed
           when the client acknowledges the Close frame.

           status is the closing identifier.
           reason is the reason for the close.
         """
        try:
            if self.closed is False:
                close_msg = bytearray()
                close_msg.extend(struct.pack("!H", status))
                if _check_unicode(reason):
                    close_msg.extend(reason.encode('utf-8'))
                else:
                    close_msg.extend(reason)

                self._sendMessage(False, WebSocket.CLOSE, close_msg)

        finally:
            self.closed = True


    def _sendBuffer(self, buff, send_all = False):
        size = len(buff)
        tosend = size
        already_sent = 0

        while tosend > 0:
            try:
            # i should be able to send a bytearray
                sent = self.client.send(buff[already_sent:])
                if sent == 0:
                    raise RuntimeError('socket connection broken')

                already_sent += sent
                tosend -= sent

            except socket.error as e:
                # if we have full buffers then wait for them to drain and try again
                if e.errno in [errno.EAGAIN, errno.EWOULDBLOCK]:
                    if send_all:
                        continue
                    return buff[already_sent:]
                else:
                    raise e

        return None

    def sendFragmentStart(self, data):
        """
            Send the start of a data fragment stream to a websocket client.
            Subsequent data should be sent using sendFragment().
            A fragment stream is completed when sendFragmentEnd() is called.

            If data is a unicode object then the frame is sent as Text.
            If the data is a bytearray object then the frame is sent as Binary.
        """
        opcode = WebSocket.BINARY
        if _check_unicode(data):
            opcode = WebSocket.TEXT
        self._sendMessage(True, opcode, data)

    def sendFragment(self, data):
        """
            see sendFragmentStart()

            If data is a unicode object then the frame is sent as Text.
            If the data is a bytearray object then the frame is sent as Binary.
        """
        self._sendMessage(True, WebSocket.STREAM, data)

    def sendFragmentEnd(self, data):
        """
            see sendFragmentEnd()

            If data is a unicode object then the frame is sent as Text.
            If the data is a bytearray object then the frame is sent as Binary.
        """
        self._sendMessage(False, WebSocket.STREAM, data)

    def sendMessage(self, data):
        """
            Send websocket data frame to the client.

            If data is a unicode object then the frame is sent as Text.
            If the data is a bytearray object then the frame is sent as Binary.
        """
        opcode = WebSocket.BINARY
        if _check_unicode(data):
            opcode = WebSocket.TEXT
        self._sendMessage(False, opcode, data)


    def _sendMessage(self, fin, opcode, data):

        payload = bytearray()

        b1 = 0
        b2 = 0
        if fin is False:
            b1 |= 0x80
        b1 |= opcode

        if _check_unicode(data):
            data = data.encode('utf-8')

        length = len(data)
        payload.append(b1)

        if length <= 125:
            b2 |= length
            payload.append(b2)

        elif length >= 126 and length <= 65535:
            b2 |= 126
            payload.append(b2)
            payload.extend(struct.pack("!H", length))

        else:
            b2 |= 127
            payload.append(b2)
            payload.extend(struct.pack("!Q", length))

        if length > 0:
            payload.extend(data)

        self.sendq.append((opcode, payload))


    def _parseMessage(self, byte):
        # read in the header
        if self.state == WebSocket.HEADERB1:

            self.fin = byte & 0x80
            self.opcode = byte & 0x0F
            self.state = WebSocket.HEADERB2

            self.index = 0
            self.length = 0
            self.lengtharray = bytearray()
            self.data = bytearray()

            rsv = byte & 0x70
            if rsv != 0:
                raise Exception('RSV bit must be 0')

        elif self.state == WebSocket.HEADERB2:
            mask = byte & 0x80
            length = byte & 0x7F

            if self.opcode == WebSocket.PING and length > 125:
                raise Exception('ping packet is too large')

            if mask == 128:
                self.hasmask = True
            else:
                self.hasmask = False

            if length <= 125:
                self.length = length

                # if we have a mask we must read it
                if self.hasmask is True:
                    self.maskarray = bytearray()
                    self.state = WebSocket.MASK
                else:
                    # if there is no mask and no payload we are done
                    if self.length <= 0:
                        try:
                            self._handlePacket()
                        finally:
                            self.state = WebSocket.HEADERB1
                            self.data = bytearray()

                    # we have no mask and some payload
                    else:
                        #self.index = 0
                        self.data = bytearray()
                        self.state = WebSocket.PAYLOAD

            elif length == 126:
                self.lengtharray = bytearray()
                self.state = WebSocket.LENGTHSHORT

            elif length == 127:
                self.lengtharray = bytearray()
                self.state = WebSocket.LENGTHLONG


        elif self.state == WebSocket.LENGTHSHORT:
            self.lengtharray.append(byte)

            if len(self.lengtharray) > 2:
                raise Exception('short length exceeded allowable size')

            if len(self.lengtharray) == 2:
                self.length = struct.unpack_from('!H', self.lengtharray)[0]

                if self.hasmask is True:
                    self.maskarray = bytearray()
                    self.state = WebSocket.MASK
                else:
                    # if there is no mask and no payload we are done
                    if self.length <= 0:
                        try:
                            self._handlePacket()
                        finally:
                            self.state = WebSocket.HEADERB1
                            self.data = bytearray()

                    # we have no mask and some payload
                    else:
                        #self.index = 0
                        self.data = bytearray()
                        self.state = WebSocket.PAYLOAD

        elif self.state == WebSocket.LENGTHLONG:

            self.lengtharray.append(byte)

            if len(self.lengtharray) > 8:
                raise Exception('long length exceeded allowable size')

            if len(self.lengtharray) == 8:
                self.length = struct.unpack_from('!Q', self.lengtharray)[0]

                if self.hasmask is True:
                    self.maskarray = bytearray()
                    self.state = WebSocket.MASK
                else:
                    # if there is no mask and no payload we are done
                    if self.length <= 0:
                        try:
                            self._handlePacket()
                        finally:
                            self.state = WebSocket.HEADERB1
                            self.data = bytearray()

                    # we have no mask and some payload
                    else:
                        #self.index = 0
                        self.data = bytearray()
                        self.state = WebSocket.PAYLOAD

        # WebSocket.MASK STATE
        elif self.state == WebSocket.MASK:
            self.maskarray.append(byte)

            if len(self.maskarray) > 4:
                raise Exception('mask exceeded allowable size')

            if len(self.maskarray) == 4:
                # if there is no mask and no payload we are done
                if self.length <= 0:
                    try:
                        self._handlePacket()
                    finally:
                        self.state = WebSocket.HEADERB1
                        self.data = bytearray()

                # we have no mask and some payload
                else:
                    #self.index = 0
                    self.data = bytearray()
                    self.state = WebSocket.PAYLOAD

        # WebSocket.PAYLOAD STATE
        elif self.state == WebSocket.PAYLOAD:
            if self.hasmask is True:
                self.data.append( byte ^ self.maskarray[self.index % 4] )
            else:
                self.data.append( byte )

            # if length exceeds allowable size then we except and remove the connection
            if len(self.data) >= self.maxpayload:
                raise Exception('payload exceeded allowable size')

            # check if we have processed length bytes; if so we are done
            if (self.index+1) == self.length:
                try:
                    self._handlePacket()
                finally:
                    #self.index = 0
                    self.state = WebSocket.HEADERB1
                    self.data = bytearray()
            else:
                self.index += 1


class SimpleWebSocketServer(object):
    def __init__(self, host, port, websocketclass, selectInterval = 0.1):
        self.websocketclass = websocketclass

        if (host == ''):
            host = None

        if host is None:
            fam = socket.AF_INET6
        else:
            fam = 0

        hostInfo = socket.getaddrinfo(host, port, fam, socket.SOCK_STREAM, socket.IPPROTO_TCP, socket.AI_PASSIVE)
        self.serversocket = socket.socket(hostInfo[0][0], hostInfo[0][1], hostInfo[0][2])
        self.serversocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.serversocket.bind(hostInfo[0][4])
        self.serversocket.listen(5)
        self.selectInterval = selectInterval
        self.connections = {}
        self.listeners = [self.serversocket]

    def _decorateSocket(self, sock):
        return sock

    def _constructWebSocket(self, sock, address):
        return self.websocketclass(self, sock, address)

    def close(self):
        self.serversocket.close()

        for desc, conn in self.connections.items():
            conn.close()
            self._handleClose(conn)

    def _handleClose(self, client):
        client.client.close()
        # only call handleClose when we have a successful websocket connection
        if client.handshaked:
            try:
                client.handleClose()
            except:
                pass

    def serveonce(self):
        writers = []
        for fileno in self.listeners:
            if fileno == self.serversocket:
                continue
            client = self.connections[fileno]
            if client.sendq:
                writers.append(fileno)

        rList, wList, xList = select(self.listeners, writers, self.listeners, self.selectInterval)

        for ready in wList:
            client = self.connections[ready]
            try:
                while client.sendq:
                    opcode, payload = client.sendq.popleft()
                    remaining = client._sendBuffer(payload)
                    if remaining is not None:
                        client.sendq.appendleft((opcode, remaining))
                        break
                    else:
                        if opcode == WebSocket.CLOSE:
                            raise Exception('received client close')

            except Exception as n:
                self._handleClose(client)
                del self.connections[ready]
                self.listeners.remove(ready)

        for ready in rList:
            if ready == self.serversocket:
                sock = None
                try:
                    sock, address = self.serversocket.accept()
                    newsock = self._decorateSocket(sock)
                    newsock.setblocking(0)
                    fileno = newsock.fileno()
                    self.connections[fileno] = self._constructWebSocket(newsock, address)
                    self.listeners.append(fileno)
                except Exception as n:
                    if sock is not None:
                        sock.close()
            else:
                if ready not in self.connections:
                    continue
                client = self.connections[ready]
                try:
                    client._handleData()
                except Exception as n:
                    self._handleClose(client)
                    del self.connections[ready]
                    self.listeners.remove(ready)

        for failed in xList:
            if failed == self.serversocket:
                self.close()
                raise Exception('server socket failed')
            else:
                if failed not in self.connections:
                    continue
                client = self.connections[failed]
                self._handleClose(client)
                del self.connections[failed]
                self.listeners.remove(failed)

    def serveforever(self):
        while True:
            self.serveonce()

