def read_header(text):
    lines = text.splitlines()
    header = {}
    for line in lines:
        key, _, value = line.partition(":")
        header[key.strip()] = value.strip()
    return header
