#!/bin/bash
set -euo pipefail
cd /app
patch -p1 <<'PATCH'
--- a/tinyparse/header.py
+++ b/tinyparse/header.py
@@ -3,4 +3,6 @@ def read_header(text):
     header = {}
     for line in lines:
+        if not line.strip():
+            break
         key, _, value = line.partition(":")
         header[key.strip()] = value.strip()
PATCH
