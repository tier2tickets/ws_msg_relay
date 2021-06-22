This is a Websocket Message Relay server/service/protocol for building applications on top of.
websocket_server.py is mostly based on the work of https://github.com/dpallot/simple-websocket-server
ws_msg_relay.py provides a system service which allows for client-client messaging, server-server messaging and client-server messaging both one-to-one and one-to-many
This could be the foundation for any of a number of applications, including real-time chat, or real-time RPC
We use it for both chat *and* RPC in our remote support tools at tier2technologies


setting up apache:

sudo nano /etc/apache2/sites-available/xxx.conf:
```
                <Location /ws_msg_relay>
                        Order allow,deny
                        Allow from all
                        ProxyPass ws://127.0.0.42:61839
                        ProxyPassReverse ws://127.0.0.42:61839
                </Location>
```
sudo a2enmod proxy_http
sudo a2enmod proxy_wstunnel
sudo systemctl restart apache2




setting up the service:
(this repo should be in /var/www/ws_msg_relay)

sudo cp ws_msg_relay.service /etc/systemd/system/
sudo systemctl enable ws_msg_relay
sudo systemctl restart ws_msg_relay
sudo systemctl status ws_msg_relay
sudo journalctl -u ws_msg_relay


using the relay:

upon connecting, the host will provide your randomly generated connection id:
```{"from": "host", "to": "JWG4K5YLfrsbfocmfyLIQA", "connection_id": "JWG4K5YLfrsbfocmfyLIQA", "message_type": "connection_id"}```

you can also request the connection id again at any time:
```{"to": "host", "command": "connection_id"}```

you can list other peers and get their metadata like so:
```{"to": "host", "command": "list_peers", "meta_filter": "*"}```
the meta_filter uses python fnmatch to return a list of peers which match that metadata search; like so:
```{"from": "host", "to": "zB8vVasSxP_SddNG4hrJjw", "peer_list": [{"connection_id": "h4VuFMipSHZ5ls4C7lrjEQ", "peer_meta": "2c01"}], "message_type": "peer_list", "meta_filter": "*"}```
You set your metadata at connection time by setting the `x-peer-meta` http header upon connection. The header can be up to 64KB

to send a message to a peer, just set the "to" address to their connection id:
```{"to": "h4VuFMipSHZ5ls4C7lrjEQ", "msg": "hi"}```
They will then receive a message like so:
```{"to": "h4VuFMipSHZ5ls4C7lrjEQ", "msg": "hi", "from": "zB8vVasSxP_SddNG4hrJjw", "message_type": "peer_communication"}```
in that example I used a "msg" key with a string value, but any key(s)/data can be sent so long as the message is less than 400KB and valid JSON

You can send a message to everyone in the group by sending to "group" like so:
```{"to": "group", "meta_filter": "*", "msg": "hi"}```
the 'meta_filter' will determine who receives the message

You cannot send messages to peers in a different group. Set the group by setting the `x-peer-group` header at connection time.

A random high-entropy shared-secret group ID can be used to facilitate secure private communication among peers within the same group.
So long as Apache is configured for TLS only, then the communications cannot be eavesdropped upon.
There are no built-in safeguards against peer impersonation within the same group though, so the application layer must protect against such attacks.
One simple way to do so would be for the peer to sign it's metadata with an ECDSA signature (and append it to the metadata).
The application would cache and serve public keys, to allow peers to confirm the identity of other peers




