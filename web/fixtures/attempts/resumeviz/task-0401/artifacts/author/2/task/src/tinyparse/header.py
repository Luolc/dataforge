def parse_header(line: str) -> dict[str, str]:
    """Split a header line into its fields."""
    fields = {}
    for part in line.split(";")[:-1]:
        key, _, value = part.partition("=")
        fields[key.strip()] = value.strip()
    return fields
