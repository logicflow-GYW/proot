if [ -z "$(which mcookie)" ] || [ -z "$(which ln)" ] || [ -z "$(which cp)" ] || \
   [ -z "$(which rm)" ] || [ -z "$(which mkdir)" ] || [ -z "$(which ls)" ]; then
    exit 125;
fi

# With PROOT_L2S_DIR configured, the backing files live outside every
# directory a tracee can name: a guest directory then holds only the
# names themselves -- symbolic links -- and removing it never trips over
# a backing file that another directory's name still references.
#
# Without it the chain owner (the file the first link(2) was made from)
# keeps its backing file next to its name.  Copying a tree of faked hard
# links in readdir order -- cp -a, cp -al, git clone --local -- can make
# a subdirectory's name the owner, and then `rm -rf` of the tree visits
# that subdirectory before the other names are gone: its backing file is
# still referenced, so rmdir(2) reports "Directory not empty" and the
# removal fails once, leaving the tree half removed.  A second pass
# finishes the job.
#
# This test pins the configured mode: one pass, no failure, the backing
# directory really holds the chain while it lives, and the guest
# directories never show it.

DIR=${TMPDIR:-/tmp}/$(mcookie).l2s
BACKING=${DIR}/backing
mkdir -p "${DIR}" "${BACKING}"

PROOT_L2S_DIR="${BACKING}" ${PROOT} -l sh -c '
set -e
# The chain owner is a name inside "sub": this is the layout cp -a
# produces when readdir hands the subdirectory first.
mkdir -p "$1/d/sub"
printf C > "$1/d/sub/f3"
ln "$1/d/sub/f3" "$1/d/f1"
ln "$1/d/f1" "$1/d/f2"

# The guest directory holds the names only.  (The dedicated directory
# itself is a host one, unreachable from the guest, so it must stay
# visible when listed by the host-side test below.)
ls -a "$1/d" | grep -q "\.l2s\." && { echo "a backing file is visible in a guest directory"; exit 1; }
ls -a "$1/d/sub" | grep -q "\.l2s\." && { echo "a backing file is visible in a guest subdirectory"; exit 1; }

# The configured mode was really in use: the backing directory holds the
# chain while it lives.  Checked before the removal, the chain is gone
# afterwards.
[ -n "$(ls -A "$2")" ] || { echo "PROOT_L2S_DIR stayed empty: the backing files were not kept there"; exit 1; }

rm -rf "$1/d"
' sh "${DIR}" "${BACKING}" 2>&1
STATUS=$?

if [ "${STATUS}" -ne 0 ]; then
    echo "rm -rf of a tree whose chain owner sits in a subdirectory failed (exit ${STATUS})"
    rm -rf "${DIR}" 2>/dev/null
    exit 1
fi

# One pass must leave nothing behind.
if [ -e "${DIR}/d" ]; then
    echo "rm -rf left the tree behind"
    rm -rf "${DIR}" 2>/dev/null
    exit 1
fi

# Removing the last name cleaned the backing directory: no orphan stays.
if [ -n "$(ls -A "${BACKING}")" ]; then
    echo "the backing directory kept orphans after the last name was removed: $(ls -A "${BACKING}")"
    rm -rf "${DIR}" 2>/dev/null
    exit 1
fi

rm -rf "${DIR}"
exit 0
