"""Test stand-in for GitHub's release pages (inside.sh starts it).

Serves the directory given as the first argument on 127.0.0.1 at the port
given as the second. A request for <path>/releases/latest is answered as
GitHub answers it, with a redirect to <path>/releases/tag/<tag>, the tag
being the content of the file at that path; everything else is a plain file.
"""

import functools
import http.server
import pathlib
import sys

root = pathlib.Path(sys.argv[1])


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.endswith("/releases/latest"):
            latest = root / self.path.lstrip("/")
            if not latest.is_file():
                self.send_error(404)
                return
            tag = latest.read_text().strip()
            self.send_response(302)
            self.send_header("Location", self.path.removesuffix("latest") + "tag/" + tag)
            self.end_headers()
            return
        super().do_GET()


handler = functools.partial(Handler, directory=str(root))
http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[2])), handler).serve_forever()
