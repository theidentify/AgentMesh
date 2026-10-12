#!/bin/sh
# Fail if guide pages contain private-looking data. Run from docs/.
set -eu
pattern='/Users/[A-Za-z0-9_]|/home/[a-z][a-z0-9_-]*/|C:\\Users\\[A-Za-z]|(^|[^0-9.])(10|192\.168|172\.(1[6-9]|2[0-9]|3[01]))\.[0-9]+\.[0-9]+|password=|postgres(ql)?://[^ ]*:[^ ]*@'
if grep -RInE "$pattern" --include='*.md' --exclude-dir=node_modules --exclude-dir=superpowers . ; then
  echo "check-privacy: private-looking data found above" >&2
  exit 1
fi
echo "check-privacy: ok"
