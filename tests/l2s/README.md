# link2symlink regression suite

Self-contained tests for the faked hard links of `--link2symlink`: real
behaviour, driven through a real PRoot binary, plus the checks that can run
without one.

Everything here lives in a subdirectory on purpose: the harness of
`tests/GNUmakefile` picks `tests/test-*.sh` up with `$(wildcard)`, which
doesn't recurse, so this suite doesn't change what `make check` does.

## Running it

On a glibc host where PRoot can actually run -- a GitHub ubuntu runner,
typically:

```sh
sudo apt-get install -y libtalloc-dev clang lld
sh tests/run_link2symlink_regression_test.sh
```

The script copies the tree to a scratch directory, builds PRoot there (never
in the working tree), checks that the built binary really can trace a
tracee, and runs every `test-*.sh` below with `PROOT` and `ROOTFS` set.  A
test that exits 125 is skipped and listed as such -- it is not counted as a
pass, and three of them carry the acceptance criteria of the batch this
suite was written for, so skipping one of those fails the run.

Why not in the Minis sandbox: PRoot drives its tracee with `ptrace(2)` and
can't run nested under another ptrace-based sandbox, and this fork doesn't
build with musl anyway (`src/syscall/rlimit.c` uses glibc's `struct
rlimit64` -- pre-existing, the baseline commit fails the same way).  The
script detects both and exits 125 instead of pretending.

## What runs without a tracee

```sh
sh tests/l2s/run_l2s_handler_test.sh             # 54 checks
python3 tests/l2s/run_l2s_handler_mutations.py   # negative controls
python3 tests/probe/l2s_wiring_check.py          # 61 checks
python3 tests/probe/l2s_mutation_check.py        # negative controls
```

* `l2s_handler_test.c` includes the production file and drives the parts
  that need no tracee: the link count recorded in the name of a faked hard
  link (4 to 9 digits), the naming that has to cross 9999 without losing a
  digit, and the reading of the chains left on disk.
* `probe/l2s_wiring_check.py` reads the sources and checks that the arms the
  fix is made of are wired -- accounting in the exit stage, the no-op
  rename, the `d_type` rewrite, `updated_stats`, the three events -- and
  that the fork doesn't reference what it doesn't have (`openat2`).
* Both mutation checkers require the corresponding check to go *red*, on the
  expected criterion, when one defect is put back.  They also replay the
  baseline commit, which has to fail: a static check that never fails proves
  nothing.

## The cases

| test | what it stands for |
|---|---|
| `test-9f16ee06.sh` | a failed rename(2) removes nothing; a name renamed onto another name of the same file stays a no-op; `RENAME_NOREPLACE`/`RENAME_EXCHANGE` |
| `test-4e91b60a.sh` | `statx(2)` reports the emulated link count (`updated_stats`) |
| `test-ad207330.sh` | `getdents(2)` gives faked hard links the type of their file, an ordinary symbolic link keeps `DT_LNK` |
| `test-l2s-legacy-chain.sh` | a chain left on disk by the previous version keeps working: read, count, link, unlink, delete |
| `test-l2s-readonly-unlink.sh` | removing a faked hard link from a 0555 directory (openminis#375), as a normal user |
| `test-l2s-hidden-internals.sh` | the backing files stay out of listings, `rm -rf` cleans up, and `cp -al` / `git clone --local` keep working |
| `test-7fa2c1d4.sh`, `test-b9d41e62.sh`, `test-d4187784.sh` | `/proc/<pid>/fd/<fd>` names the file the way the tracee opened it, `O_NOFOLLOW` included |
| `test-f3a8c2e1.sh`, `test-f3a8c2e2.sh` | `/proc/<pid>/exe` names the faked hard link, execveat included |
| `test-02686ecd.sh` | a path whose translation failed is not handed to the emulation |
| `test-4171f0b5.sh`, `test-bcf7c15f.sh` | 10000 links, and 1000 files of the same name |
| `test-60e0614d.sh` | the intermediate is reserved before the file is moved |
| `test-b1e55743.sh` | the errno of a failed emulated link(2) reaches the tracee |
| `test-bf7be26e.sh` | a broken chain is left to the kernel; a chain this extension didn't make is not deleted |
| `test-6e3a9d21.sh` | `ln -L` links the faked hard link, not the file it stands for |
| `test-f4182ce0.sh` | an ordinary symbolic link is linked as a symbolic link |

Most of them come from upstream `termux/proot` @ `52d65a4`, which is where
this behaviour was worked out; the two `test-l2s-*` cases were written for
this fork (the legacy on-disk layout, and openminis#375).
