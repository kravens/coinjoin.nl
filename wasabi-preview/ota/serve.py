# Serves the install page over HTTPS (iOS requires it) and the certificate over plain HTTP
# so the phone can trust it first. Binds localhost; `tailscale serve --tcp` forwards the tailnet ports.
import functools, http.server, ssl, sys, threading

OUT = sys.argv[1]

class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".cer": "application/x-x509-ca-cert", ".plist": "text/xml", ".ipa": "application/octet-stream"}

handler = functools.partial(Handler, directory=OUT)
plain = http.server.ThreadingHTTPServer(("127.0.0.1", 18080), handler)
secure = http.server.ThreadingHTTPServer(("127.0.0.1", 18443), handler)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain("ota/cert.pem", "ota/key.pem")
secure.socket = context.wrap_socket(secure.socket, server_side=True)
threading.Thread(target=plain.serve_forever, daemon=True).start()
secure.serve_forever()
