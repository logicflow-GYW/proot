if [ -z "$(which mcookie)" ] || [ -z "$(which ln)" ] || [ -z "$(which stat)" ] || [ -z "$(which cat)" ] || [ -z "$(which ls)" ]; then
    exit 125;
fi

# A directory the tracee can't write to any more, as Git leaves
# .git/objects/pack (0555) -- openminis/openminis#375: removing a faked hard
# link from it reported "Operation not permitted".  The kernel removes the
# name because fake_id0 grants the write permission for the duration of that
# syscall; link2symlink then has to update its own bookkeeping -- rename the
# counted file, unlink the intermediate -- in that same directory, as the
# real uid, now that the permission is gone again.
#
# Root ignores the mode, so this arm can only run as a normal user.

if [ "$(id -u)" -eq 0 ]; then
    exit 125;
fi

DIR=${TMPDIR:-/tmp}/$(mcookie).l2s.0555
mkdir "${DIR}"

set +e
RESULT=$(${PROOT} -l -0 sh -c '
cd "$1" || exit 1
mkdir pack
echo packed > pack/blob
ln pack/blob pack/link
chmod 555 pack
rm pack/link; echo "rm $? $(cat pack/blob) $(stat -c %h pack/blob)"
rm pack/blob; echo "last $? $(ls -A pack | grep -c "^\\.l2s\\.")"
chmod 755 pack
' sh "${DIR}" 2>&1)
STATUS=$?
set -e

rm -rf "${DIR}"

EXPECTED='rm 0 packed 1
last 0 0'

if [ "${STATUS}" -ne 0 ] || [ "${RESULT}" != "${EXPECTED}" ]; then
    echo "${RESULT}"
    exit 1
fi

exit 0
