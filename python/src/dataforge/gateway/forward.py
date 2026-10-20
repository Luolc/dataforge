"""Forward 127.0.0.1:8787 in a container to the model proxy's Unix socket.

Runs inside an agent's container, whose client reaches the model only at
`http://127.0.0.1:8787`; every connection to that port is copied byte for byte
to the socket the host's proxy listens on, which is `/run/dataforge/model.sock`
in the directory mounted there.
It needs nothing but Python 3.8 or later and imports nothing from dataforge, so
that it can be mounted into any image as one file.
"""

import argparse
import asyncio
import typing

HOST = "127.0.0.1"
PORT = 8787
SOCKET = "/run/dataforge/model.sock"


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
  try:
    while True:
      data = await reader.read(65536)
      if not data:
        break
      writer.write(data)
      await writer.drain()
  except OSError:
    pass
  finally:
    writer.close()


async def serve(socket: str = SOCKET, port: int = PORT):
  """Forward each connection to the port until cancelled.

  Args:
    socket: The proxy's socket.
    port: The port on HOST to listen on.
  """

  async def connect(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
      up_reader, up_writer = await asyncio.open_unix_connection(socket)
    except OSError:
      writer.close()
      return
    _ = await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))

  server = await asyncio.start_server(connect, HOST, port)
  async with server:
    await server.serve_forever()


def main():
  """Serve until killed."""
  parser = argparse.ArgumentParser(description=__doc__)
  _ = parser.add_argument("--socket", default=SOCKET)
  asyncio.run(serve(typing.cast(str, parser.parse_args().socket)))


if __name__ == "__main__":
  main()
