"""Split a message into its header block and body."""


def split(message: str) -> tuple[str, str]:
    head, _, body = message.partition("\n\n")
    return head, body
