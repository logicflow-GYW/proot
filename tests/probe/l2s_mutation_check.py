#!/usr/bin/env python3
"""Negative controls for probe/l2s_wiring_check.py.

A static check that never fails proves nothing: the wiring probe is only
worth its exit status if it goes red on the code it is meant to catch.  This
script builds a scratch copy of the tree, breaks one thing at a time, and
requires the probe to fail *on the expected criterion* -- not merely to
fail, which any syntax problem would do as well.

It also replays the baseline commit: the probe has to fail on the code
before the fix, otherwise it never proved the fix was needed.

Usage: python3 probe/l2s_mutation_check.py [--baseline <rev>]
                                            [--root <tree>] [-v]

Exit status: 0 when every mutation was caught as expected, 1 otherwise.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

FILES = [
    "src/extension/link2symlink/link2symlink.c",
    "src/extension/extension.h",
    "src/path/canon.c",
    "src/syscall/exit.c",
    "src/execve/enter.c",
]

L2S = FILES[0]

# (name, file, text to replace, replacement, criterion the probe must report)
MUTATIONS = [
    (
        "A1: count links down in the enter stage again",
        L2S,
        """		case PR_unlink:
			/* If path points a file that is an symlink to a file that begins""",
        """		case PR_unlink:
			status = decrement_intermediate_count(tracee, "x", "y");
			/* If path points a file that is an symlink to a file that begins""",
        "A1",
    ),
    (
        "A1: forget the removed name whatever the syscall returned",
        L2S,
        """	if ((int) peek_reg(tracee, CURRENT, SYSARG_RESULT) == 0
	    && (my_readlink(config->removed_link, value) < 0""",
        """	if ((int) peek_reg(tracee, CURRENT, SYSARG_RESULT) == 0
	    && (value[0] == '\\0'""",
        "A1",
    ),
    (
        "A1: don't count rename(2) down at the exit stage",
        L2S,
        """	case PR_unlinkat:                  //int unlinkat(int dirfd, const char *pathname, int flags);
		forget_removed_link(extension);""",
        """	case PR_unlinkat:                  //int unlinkat(int dirfd, const char *pathname, int flags);""",
        "A1",
    ),
    (
        "A2: prepare_rename() ignores RENAME_NOREPLACE/RENAME_EXCHANGE",
        L2S,
        """	if ((flags & (RENAME_NOREPLACE | RENAME_EXCHANGE)) != 0)
		return 0;""",
        """	if ((flags & (RENAME_NOREPLACE | RENAME_EXCHANGE)) == 0xffff)
		return 0;""",
        "A2",
    ),
    (
        "A2: prepare_rename() no longer pokes the new path",
        L2S,
        """	poke_reg(tracee, new_sysarg, peek_reg(tracee, CURRENT, old_sysarg));""",
        """	(void) old_sysarg;""",
        "A2",
    ),
    (
        "A3: getdents(2) hands the kernel result back untouched",
        L2S,
        """		compacted[offset + type_offset] = status;""",
        """		(void) status;""",
        "A3",
    ),
    (
        "A3: getdents(2) also rewrites the entry offset",
        L2S,
        """		compacted[offset + type_offset] = status;""",
        """		compacted[offset + type_offset] = status;
		compacted[offset + 8] = 0;  /* d_off */""",
        "A3",
    ),
    (
        "A3: the backing files are handed to the tracee again",
        L2S,
        """		if (hide && strncmp(name, PREFIX, strlen(PREFIX)) == 0)
			continue;""",
        """		if (hide && strncmp(name, PREFIX, strlen(PREFIX)) == 0)
			;""",
        "A3",
    ),
    (
        "A3: getdents(2) is no longer filtered at the exit stage",
        L2S,
        """			{ PR_getdents,		FILTER_SYSEXIT },""",
        """			{ PR_getdents,		FILTER_SYSEXIT | 0x10 },""",
        "A3",
    ),
    (
        "A4: statx(2) changes the buffer without flagging it updated",
        L2S,
        """	state->updated_stats = true;""",
        """	/* state->updated_stats = true; */""",
        "A4",
    ),
    (
        "A10: /proc/<pid>/fd/<fd> is no longer renamed after the faked link",
        L2S,
        """	strcpy(state->host_path, link);
	state->substituted = true;""",
        """	(void) link;""",
        "A10",
    ),
    (
        "A10: exit.c stops emitting READLINK_PROC_FD",
        "src/syscall/exit.c",
        """			status = notify_extensions(tracee, READLINK_PROC_FD, (intptr_t) &proc_fd, 0);""",
        """			status = 0;""",
        "A10",
    ),
    (
        "A10: a truncated answer is dropped before recall_fd()",
        L2S,
        """	if (!is_l2s_file(state->host_path) && !state->truncated)""",
        """	if (!is_l2s_file(state->host_path))""",
        "A10",
    ),
    (
        "A10: a truncated answer is only matched exactly",
        L2S,
        """			if (!truncated || rank != 0)
				continue;""",
        """			continue;""",
        "A10",
    ),
    (
        "A10: exit.c reports a substituted path as untouched",
        "src/syscall/exit.c",
        """			if (!proc_fd.substituted)
				goto end;""",
        """			goto end;""",
        "A10",
    ),
    (
        "A10: canon.c stops reporting the links it follows",
        "src/path/canon.c",
        """			status = notify_extensions(tracee, SYMLINK_DEREFERENCED,""",
        """			status = 0; (void)""",
        "A10",
    ),
    (
        "A10: execve/enter.c stops reporting /proc/<pid>/exe",
        "src/execve/enter.c",
        """	status = notify_extensions(tracee, EXECVE_PROC_EXE, (intptr_t) &proc_exe, 0);""",
        """	status = 0;""",
        "A10",
    ),
    (
        "fork: the which() guard is dropped with the old which() in place",
        L2S,
        """	if (tracee->exe == NULL)
		return;""",
        """""",
        "fork",
    ),
    (
        "fork: openat2(2) is no longer mapped although this fork translates it",
        L2S,
        """	case PR_creat:
	case PR_open:
	case PR_openat:
	case PR_openat2:
		return true;""",
        """	case PR_creat:
	case PR_open:
	case PR_openat:
		return true;""",
        "fork",
    ),
    (
        "A8: get_link_count() reads the last four characters again",
        L2S,
        """	length = strlen(dot + 1);
	if (length < 4 || length > 9)
		return -1;""",
        """	return atoi(dot + 1);""",
        "A8",
    ),
]


def copy_tree(source, destination):
    for name in FILES:
        target = os.path.join(destination, name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(os.path.join(source, name), target)


def seed_baseline(destination, source, revision):
    for name in FILES:
        try:
            content = subprocess.check_output(
                ["git", "-C", source, "show", f"{revision}:{name}"], stderr=subprocess.DEVNULL
            )
        except subprocess.CalledProcessError:
            return False
        target = os.path.join(destination, name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(content)
    return True


def run_probe(root, verbose=False):
    command = [sys.executable, os.path.join(HERE, "l2s_wiring_check.py"), "--root", root]
    if verbose:
        command.append("-v")
    result = subprocess.run(command, capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", help="tree to mutate (default: the one holding this script)")
    parser.add_argument("--baseline", default="e2155e6d62b523254e36c523dc659e277ae0e620",
                        help="commit the pre-fix code is taken from (default: the pin this branch forked at)")
    parser.add_argument("--skip-baseline", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    root = args.root or os.path.abspath(os.path.join(HERE, "..", ".."))

    checks = 0
    failures = []

    # 1. The tree as it is has to pass. --------------------------------
    status, output = run_probe(root, args.verbose)
    checks += 1
    if status != 0:
        failures.append(("the fixed tree", output))
        print("FAIL the fixed tree has to pass the wiring check")
    else:
        print("ok   the fixed tree passes the wiring check")

    work = tempfile.mkdtemp(prefix="l2s-mutations.")

    # 2. The baseline has to fail, or the check proves nothing. --------
    if not args.skip_baseline:
        checks += 1
        baseline = os.path.join(work, "baseline")
        copy_tree(root, baseline)
        if not seed_baseline(baseline, root, args.baseline):
            print(f"ok   baseline {args.baseline[:8]} skipped (commit not available)")
        else:
            status, output = run_probe(baseline)
            if status == 0:
                failures.append((f"baseline {args.baseline[:8]}", output))
                print(f"FAIL the baseline {args.baseline[:8]} passes the wiring check")
            else:
                print(f"ok   the baseline {args.baseline[:8]} fails the wiring check")

    # 3. One broken thing at a time. -----------------------------------
    for index, (name, relative, old, new, criterion) in enumerate(MUTATIONS):
        checks += 1
        tree = os.path.join(work, f"mutation-{index:02d}")
        copy_tree(root, tree)
        path = os.path.join(tree, relative)
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()

        if old not in text:
            failures.append((name, "the text to mutate is not in the tree any more"))
            print(f"FAIL {name}: the text to mutate is not in the tree any more")
            continue

        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, new, 1))

        status, output = run_probe(tree)
        if status == 0:
            failures.append((name, output))
            print(f"FAIL {name}: the wiring check stayed green")
        elif f"[{criterion}]" not in output:
            failures.append((name, output))
            print(f"FAIL {name}: red, but not on {criterion}")
        else:
            print(f"ok   {name} -> red on {criterion}")
        shutil.rmtree(tree, ignore_errors=True)

    shutil.rmtree(work, ignore_errors=True)

    print(f"\n{checks} controls, {len(failures)} unexpected results")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
