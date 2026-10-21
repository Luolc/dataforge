"""share, the read-only site that shows an export to people outside.

A person signs in with an access token that names the projects it may see;
the site reads only the export directories its site file names. It needs
the package's optional extra viz, which brings FastAPI and uvicorn; the
token commands need only the standard library.
"""
