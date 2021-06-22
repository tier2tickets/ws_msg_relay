import signal
import sys
from websocket_server import WebSocket, SimpleWebSocketServer
import traceback
import threading
import time
import json
import secrets
from fnmatch import fnmatch
from datetime import datetime

clients = []
clients_lock = threading.Lock()

def main():
    server = SimpleWebSocketServer('127.0.0.42', 61839, server_class)
    def close_sig_handler(signal, frame):
        try:
            with clients_lock:
                for client in clients:
                    client.close(status=1012, reason='Server is going down. Hope to be back soon')
        except:
            traceback.print_exc()
        server.close()
        sys.exit()
    signal.signal(signal.SIGINT, close_sig_handler)
    threading.Thread(target=clients_watchdog, daemon=True).start()
    print(f"service started at {datetime.now()}")
    server.serveforever()

def clients_watchdog():
    while True:
        try:
            with clients_lock:
                for client in clients:
                    if client.last_seen < int(time.time()) - 25:
                        client.close(status=1002, reason='No pong received after 2 pings')
                    else:
                        if client.last_seen < int(time.time()) - 15:
                            client._sendMessage(False, WebSocket.PING, 'hey')
        except:
            traceback.print_exc()
        finally:
            time.sleep(5)

class server_class(WebSocket):
    def handleMessage(self):
        try:
            with clients_lock:

                if self.opcode != WebSocket.TEXT:
                    self.close(status=1003, reason='Server Unsupported Data: Text Only')
                    return

                if self.data == 'ping':
                    self.sendMessage('pong') # this isn't a "real" ping/pong, but some clients (like chrome) don't expose access to the real ones, so this is for them to check if the server is still accessible
                    return

                try:
                    message = json.loads(self.data)
                except:
                    self.close(status=1008, reason='messages must be in JSON format')
                    return
                
                if not isinstance(message, dict) or not 'to' in message:
                    self.close(status=1008, reason='the message must be addressed to somewhere')
                    return

                message['from'] = self.connection_id


                if message['to'] == 'host':
                    if 'command' in message and message['command'] == 'connection_id':
                        self.sendMessage(json.dumps({
                            'from': 'host',
                            'to': self.connection_id,
                            'connection_id': self.connection_id,
                            'message_type': 'connection_id',
                        }))
                        return
                        
                    if 'command' in message and message['command'] == 'list_peers':

                        if 'meta_filter' in message:
                            list = []
                            for client in clients:
                                if client.peer_group == self.peer_group and client != self:
                                    if fnmatch(client.peer_meta, message['meta_filter']):
                                        list.append({'connection_id': client.connection_id, 'peer_meta': client.peer_meta})
                            self.sendMessage(json.dumps({
                                'from': 'host',
                                'to': self.connection_id,
                                'peer_list': list,
                                'message_type': 'peer_list',
                                'meta_filter': message['meta_filter']
                            }))
                        return

                if message['to'] == 'group':
                    for client in clients:
                        if client.peer_group == self.peer_group and client != self:
                            if 'meta_filter' in message and fnmatch(client.peer_meta, message['meta_filter']):
                                message['message_type'] = 'peer_communication'
                                client.sendMessage(json.dumps(message))
                    return

                for client in clients:
                    if client.connection_id == message['to'] and client.peer_group == self.peer_group:
                        message['message_type'] = 'peer_communication'
                        client.sendMessage(json.dumps(message))
        except:
            traceback.print_exc()
            self.close(status=1011, reason='Server Internal Error')



    def handleConnected(self):
        try:
            with clients_lock:
                clients.append(self)
                self.request.headers['x-peer-group'] = 'kk' # TODO: remove
                self.request.headers['X-peer-meta'] = secrets.token_hex(2) # TODO: remove

                self.peer_group = None
                self.peer_meta = None
                self.connection_id = secrets.token_urlsafe(16)

                if 'x-peer-group' not in [x.lower() for x in self.request.headers.keys()]:
                    self.close(status=1008, reason='group id is required')
                    return
                self.peer_group = self.request.headers['x-peer-group'].lower()

                if 'x-peer-meta' not in [x.lower() for x in self.request.headers.keys()]:
                    self.close(status=1008, reason='peer type is required')
                    return
                self.peer_meta = self.request.headers['x-peer-meta'].lower()

                for client in clients:
                    if client != self:
                        client.sendMessage(json.dumps({'from': 'host', 'to': client.connection_id, 'peer': {'connection_id': self.connection_id, 'peer_meta': self.peer_meta}, 'message_type': 'peer_connect'}))


                self.sendMessage(json.dumps({
                    'from': 'host',
                    'to': self.connection_id,
                    'connection_id': self.connection_id,
                    'message_type': 'connection_id',
                }))


        except:
            traceback.print_exc()
            self.close(status=1011, reason='Server Internal Error')

    def handleClose(self):
        try:
            with clients_lock:
                clients.remove(self)
                for client in clients:
                    client.sendMessage(json.dumps({'from': 'host', 'to': client.connection_id, 'peer': {'connection_id': self.connection_id, 'peer_meta': self.peer_meta}, 'message_type': 'peer_disconnect'}))
        except:
            traceback.print_exc()


if __name__ == "__main__":
    main()
