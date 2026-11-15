if [ -z "$(which mcookie)" ] || [ -z "$(which ln)" ] || [ -z "$(which readlink)" ] || \
   [ ! -x "${ROOTFS}/bin/small_readlink" ]; then
    exit 125;
fi

# readlink(2) reports at most the size of the buffer it is given, without
# any hint that the name it had was longer.  The host path of a backing
# file is much longer than the name the tracee opened, so a small buffer
# truncates it -- and the kernel then reports nothing the link2symlink
# extension recognizes, because it matches the whole host path.  The
# extension used to leave the answer as the kernel gave it: busybox's
# readlink(1), which reads a short chunk, then printed the backing file
# "/tmp/<dir>/.l2s.<name>0001".  It must not leak, whatever the buffer
# size; the large-buffer answer is the control.
#
# The buffer is sized so the host path is cut *inside* the backing file
# name ("<dir>/.l2s.<name><NNNN>.0002"): with a buffer of the directory's
# length plus six bytes, the answer ends right after "/.l2s.".  A fixed
# 32-byte buffer would be cut inside the temporary directory prefix on
# runners whose TMPDIR is long, and the test would pass without the fix.

DIR=${TMPDIR:-/tmp}/$(mcookie).l2s
mkdir "${DIR}"
echo content > "${DIR}/original"

${PROOT} -l ln "${DIR}/original" "${DIR}/link"

# "/.l2s." is six bytes: keep the directory part and the prefix itself.
BUFSIZE=$(( ${#DIR} + 6 ))
[ "${BUFSIZE}" -ge 2 ] || BUFSIZE=2

OUT=$(${PROOT} -l -b /proc ${ROOTFS}/bin/small_readlink "${DIR}/link" "${BUFSIZE}" 2>&1)
STATUS=$?

rm -rf "${DIR}"

printf '%s\n' "$OUT"

if [ "${STATUS}" -ne 0 ]; then
    echo "the probe failed (exit ${STATUS})"
    exit 1
fi

SMALL=$(printf '%s\n' "$OUT" | sed -n 's/^small=//p')
LARGE=$(printf '%s\n' "$OUT" | sed -n 's/^large=//p')

# The large-buffer answer is what the tracee opened: the control.
if [ "${LARGE}" != "${DIR}/link" ]; then
    echo "a large buffer doesn't report the name that was opened (got '${LARGE}')"
    exit 1
fi

# Neither answer may name the backing file or the host path of the app.
case "${SMALL}${LARGE}" in
    *"/.l2s."*) echo "a backing file name leaked"; exit 1 ;;
esac
case "${SMALL}${LARGE}" in
    *"/data/"*) echo "the host path of the app leaked"; exit 1 ;;
esac

# The small-buffer answer is a prefix of the name that was opened.
if [ -z "${SMALL}" ]; then
    echo "a small buffer reports nothing"
    exit 1
fi
case "${DIR}/link" in
    "${SMALL}"*) ;;
    *) echo "a small buffer doesn't report a prefix of the name that was opened (got '${SMALL}')"; exit 1 ;;
esac

exit 0
