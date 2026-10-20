# Fix the header parser

`tinyparse.parse_header` drops the last field when a header line ends with a
semicolon. Make it keep every field, and keep the existing behaviour for lines
without one.
