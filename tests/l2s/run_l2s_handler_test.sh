#!/bin/sh
# Build and run the link2symlink handler test, which drives the production
# code without a tracee: it needs nothing but a C compiler, libtalloc and
# a writable /tmp, so it runs in the Minis sandbox as well as on a CI
# runner.  Exits 0 when every check passed, 1 when one failed, 2 when the
# test couldn't be built.
#
# Usage: sh run_l2s_handler_test.sh [--keep]

set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
SRC=$(cd "$HERE/../.." && pwd)
OUT=${TMPDIR:-/tmp}/l2s-handler-test.$$

trap 'rm -f "$OUT"' EXIT INT TERM

CC=${CC:-cc}

# The test #includes the production file, so a wrong root would silently
# compile something else -- or nothing at all.
if [ ! -f "$SRC/src/extension/link2symlink/link2symlink.c" ] \
   || [ ! -f "$SRC/src/extension/extension.h" ]; then
	echo "l2s handler test: $SRC doesn't look like the PRoot source tree" >&2
	exit 2
fi
echo "l2s handler test: src=$SRC cc=$CC" >&2

if ! printf '#include <talloc.h>\nint main(void) { return 0; }\n' \
	| "$CC" -x c - -ltalloc -o "$OUT.probe" >/dev/null 2>&1; then
	echo "l2s handler test: needs a C compiler and libtalloc (libtalloc-dev)" >&2
	exit 2
fi
rm -f "$OUT.probe"

# -ffunction-sections/-fdata-sections + --gc-sections drop the functions of
# the production file this test never calls, so the linker doesn't need the
# rest of PRoot.
"$CC" -D_FILE_OFFSET_BITS=64 -D_GNU_SOURCE -I"$SRC" -I"$SRC/src" \
	-Wall -Wextra -O2 -ffunction-sections -fdata-sections \
	"$HERE/l2s_handler_test.c" -Wl,--gc-sections -ltalloc -o "$OUT" || exit 2

exec "$OUT"
