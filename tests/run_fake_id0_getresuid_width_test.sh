#!/bin/sh
# Build and run the fake_id0 getres[ug]id ID-width unit test.
#
# The test #include-s the production fake_id0.c, so it exercises the
# real handle_getresuid_exit_end() / handle_getresgid_exit_end().
#
# The production code has two shapes:
#
#   - pre-fix  (a9e68cb): the handlers take (tracee, config) and
#     POKE_MEM_ID always writes 16 bits.
#   - post-fix (c1cd48d): the handlers take (tracee, config, sysnum)
#     and the width depends on the syscall and on the ABI.
#
# C cannot overload on function arity, so the test needs to know
# which shape it is compiled against.  Rather than trusting a manual
# switch, the switch is derived here from the *content* of the
# production source, so it cannot drift from the code under test.
#
# Usage: run.sh [path/to/proot/src]

set -e

# Note: this script is POSIX sh (no bash-isms): the exit status of the
# test is captured through a temporary file, not through PIPESTATUS.
HERE=$(cd "$(dirname "$0")" && pwd)
SRC=${1:-$(cd "$HERE/../src" && pwd)}
OUT=${OUT:-/tmp/s05a_getresuid_width_test}
LOG=${LOG:-/tmp/s05a_getresuid_width_test.log}

FAKE_ID0="$SRC/extension/fake_id0/fake_id0.c"

if [ ! -f "$FAKE_ID0" ]; then
	echo "ERROR: $FAKE_ID0 not found" >&2
	exit 2
fi

# Detect the shape of the production code.
if grep -q 'is_16bit_id_syscall' "$FAKE_ID0"; then
	DEFINE="-DS05A_FIXED"
	SHAPE="post-fix (sysnum-aware)"
else
	DEFINE=""
	SHAPE="pre-fix (always 16-bit)"
fi

echo "=== fake_id0 getres[ug]id ID width test ==="
echo "source : $FAKE_ID0"
echo "shape  : $SHAPE"
echo

# -ffunction-sections/-fdata-sections + --gc-sections let the linker
# drop the parts of fake_id0.c the test does not reach, so only the
# symbols used by the test have to be resolvable.
# shellcheck disable=SC2086
${CC:-gcc} \
	-D_FILE_OFFSET_BITS=64 -D_GNU_SOURCE \
	$DEFINE \
	-I"$SRC" \
	-ffunction-sections -fdata-sections \
	-Wall \
	-o "$OUT" \
	"$HERE/fake_id0_getresuid_width_test.c" \
	-Wl,--gc-sections \
	-ltalloc

set +e
"$OUT" > "$LOG" 2>&1
rc=$?
set -e
cat "$LOG"

if [ "$rc" -eq 0 ]; then
	echo "RESULT: PASS"
else
	echo "RESULT: FAIL (exit $rc)"
fi

exit "$rc"
