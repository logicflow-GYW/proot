if [ -z "$(which mcookie)" ] || [ -z "$(which ln)" ] || [ -z "$(which stat)" ] || [ -z "$(which cat)" ] || [ -z "$(which ls)" ]; then
    exit 125;
fi

# A hard link faked by a version that came before this fix is already on
# disk, in the layout that version left: the file is "<PREFIX><name><NNNN>
# .<count>", the intermediate "<PREFIX><name><NNNN>" points to it, and the
# names of the file are symbolic links to the intermediate.  An upgrade
# can't ask for the rootfs to be reinstalled, so those chains have to keep
# working: reading through one, seeing its count, adding a name to it,
# removing names from it, and deleting the data with the last one.
#
# This is the arm the handler test can't provide: it drives the real
# binary through the real syscalls on a chain it did not create itself.

DIR=${TMPDIR:-/tmp}/$(mcookie).l2s.legacy
mkdir "${DIR}"

echo data > "${DIR}/.l2s.a0001.0002"
ln -s "${DIR}/.l2s.a0001.0002" "${DIR}/.l2s.a0001"
ln -s "${DIR}/.l2s.a0001" "${DIR}/a"
ln -s "${DIR}/.l2s.a0001" "${DIR}/b"

set +e
RESULT=$(${PROOT} -l sh -c '
cd "$1" || exit 1
echo "read $(cat a) $(cat b)"
echo "count $(stat -c %h a)"
ln a c; echo "link $? $(stat -c %h a) $(cat c)"
rm b; echo "unlink $? $(stat -c %h a) $(cat a)"
rm a c; echo "last $? $(ls -A . | grep -c "^\\.l2s\\.")"
' sh "${DIR}" 2>&1)
STATUS=$?
set -e

rm -rf "${DIR}"

EXPECTED='read data data
count 2
link 0 3 data
unlink 0 2 data
last 0 0'

if [ "${STATUS}" -ne 0 ] || [ "${RESULT}" != "${EXPECTED}" ]; then
    echo "${RESULT}"
    exit 1
fi

exit 0
