if [ -z "$(which mcookie)" ] || [ -z "$(which ln)" ] || [ -z "$(which cp)" ] || \
   [ -z "$(which find)" ] || [ -z "$(which git)" ] || [ -z "$(which rm)" ]; then
    exit 125;
fi

# The files this extension keeps next to the names it faked are its own
# bookkeeping: no tracee ever named them, and a tool that lists a directory
# must not see them.  When it did, two user-visible things broke (both
# measured on the Android build, 2026-11-15):
#
#   * `rm -rf <dir>` deleted the counted file first, the count was renamed
#     as the names went, the tool's listing went stale and rmdir(2) failed
#     with "Directory not empty", leaving the file behind;
#   * `git clone --local` / `cp -al` hard-linked whatever a directory held,
#     including an intermediate, and the link(2) of an internal name failed
#     with EINVAL -- fatal to git, which only falls back to copying on
#     EPERM/EXDEV.
#
# So: hidden from listings, recursive removal clean, and the two tools that
# hard-link whole directories keep working.

DIR=${TMPDIR:-/tmp}/$(mcookie).l2s.internal
mkdir "${DIR}" || exit 1

fail=0
note() { echo "  - $*"; fail=1; }

# 1. A directory of faked hard links: no internal name in ls(1) nor find(1),
#    and the names themselves still show up as the files they are.
mkdir -p "${DIR}/listed"
printf KEEP > "${DIR}/listed/a"
ln "${DIR}/listed/a" "${DIR}/listed/b"
ln "${DIR}/listed/a" "${DIR}/listed/c"
LISTING=$(${PROOT} -l sh -c 'cd "$1/listed" || exit 1; ls -a; echo ===; find . -type f | sort' sh "${DIR}" 2>&1)
case "$LISTING" in
    *".l2s."*) note "an internal name is visible: $LISTING" ;;
esac
printf '%s\n' "$LISTING" | grep -q '^a$' || note "listing lost the name a"
printf '%s\n' "$LISTING" | grep -q '^\./b$' || note "find -type f lost the name b"
printf '%s\n' "$LISTING" | grep -q '^\./c$' || note "find -type f lost the name c"

# 2. Recursive removal of such a directory succeeds and leaves nothing.
OUT=$(${PROOT} -l sh -c 'cd "$1" && rm -rf listed && echo REMOVED' sh "${DIR}" 2>&1)
case "$OUT" in
    *REMOVED*) ;;
    *) note "rm -rf failed: $OUT" ;;
esac
[ -e "${DIR}/listed" ] && note "rm -rf left the directory behind"
LEFTOVER=$(find "${DIR}" -name '.l2s.*' | wc -l)
[ "$LEFTOVER" -eq 0 ] || note "rm -rf left $LEFTOVER internal files behind"

# 3. cp -al of a directory that holds faked hard links.
mkdir -p "${DIR}/cp-src"
printf CONTENT > "${DIR}/cp-src/x"
ln "${DIR}/cp-src/x" "${DIR}/cp-src/y"
OUT=$(${PROOT} -l sh -c 'cd "$1" && cp -al cp-src cp-dst && cat cp-dst/x cp-dst/y' sh "${DIR}" 2>&1)
case "$OUT" in
    *CONTENTCONTENT*) ;;
    *) note "cp -al failed: $OUT" ;;
esac

# 4. git clone --local, the tool that surfaced the EINVAL.
OUT=$(${PROOT} -l sh -c '
cd "$1" || exit 1
mkdir repo && cd repo || exit 1
git init -q .
printf a > a && git add a || exit 1
git -c user.email=a@b -c user.name=t commit -qm init || exit 1
cd .. || exit 1
git clone -q --local repo copy || exit 1
cd copy && cat a
' sh "${DIR}" 2>&1)
case "$OUT" in
    *a*) ;;
    *) note "git clone --local failed: $OUT" ;;
esac

# 5. Linking a faked hard link still works, and so does removing the last name.
mkdir -p "${DIR}/still"
printf X > "${DIR}/still/one"
ln "${DIR}/still/one" "${DIR}/still/two"
OUT=$(${PROOT} -l sh -c 'cd "$1/still" && ln two three && cat three && rm one two three && echo CLEAN' sh "${DIR}" 2>&1)
case "$OUT" in
    *XCLEAN*) ;;
    *) note "link/remove through a faked hard link broke: $OUT" ;;
esac
LEFTOVER=$(find "${DIR}/still" -name '.l2s.*' | wc -l)
[ "$LEFTOVER" -eq 0 ] || note "removing the last name left $LEFTOVER internal files"

rm -rf "${DIR}"

if [ "$fail" -ne 0 ]; then
    exit 1
fi

exit 0
