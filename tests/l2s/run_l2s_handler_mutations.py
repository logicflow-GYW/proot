#!/usr/bin/env python3
"""Negative controls for the handler test (tests/l2s/l2s_handler_test.c).

The handler test only matters if it goes red when the production code loses
the behaviour it checks.  Each control below puts one defect back into a
scratch copy of the extension and requires the test to fail:

  * the count read as the last four characters -- what this fork did before
    the fix, and what made the 10000th link look like a broken chain;
  * the count written over the last four characters only -- the other half
    of the same defect;
  * a chain that this extension did not make accepted as one.

Usage: python3 tests/l2s/run_l2s_handler_mutations.py [-v]
Exit status: 0 when every control was caught, 1 otherwise.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", ".."))
RELATIVE = "src/extension/link2symlink/link2symlink.c"

MUTATIONS = [
    (
        "read the link count as the last four characters",
        """	length = strlen(dot + 1);
	if (length < 4 || length > 9)
		return -1;

	count = 0;
	for (i = 1; i <= length; i++) {
		if (!isdigit((unsigned char) dot[i]))
			return -1;
		count = count * 10 + (dot[i] - '0');
	}

	return count;""",
        """	(void) i;
	(void) count;
	(void) length;
	(void) dot;
	/* What this fork did before the fix: the last four characters of
	 * the whole path, whatever its name holds. */
	return atoi(host_path + strlen(host_path) - 4);""",
    ),
    (
        "write the link count over the last four characters",
        """	length = strrchr(final, '.') + 1 - final;

	status = snprintf(new_final, PATH_MAX, "%.*s%04d", length, final, link_count);
	if (status < 0 || status >= PATH_MAX)
		return -ENAMETOOLONG;

	return 0;""",
        """	length = strrchr(final, '.') + 1 - final;

	status = snprintf(new_final, PATH_MAX, "%s", final);
	if (status < 0 || status >= PATH_MAX)
		return -ENAMETOOLONG;

	sprintf(new_final + strlen(final) - 4, "%04d", link_count);

	return 0;""",
    ),
    (
        "accept a chain this extension did not make",
        """	/* Not a chain this extension made: there is no count to read in
	 * this name, and nothing to rename or to delete on its behalf.  */
	if (!is_l2s_file(final))
		return -ENOENT;""",
        """	/* mutated: the counted name is no longer checked. */""",
    ),
]


def read_count_test_source():
    with open(os.path.join(SRC, RELATIVE), "r", encoding="utf-8") as handle:
        return handle.read()


def build_and_run(tree, verbose):
    binary = os.path.join(tree, "handler-test")
    compile_command = [
        os.environ.get("CC", "cc"),
        "-D_FILE_OFFSET_BITS=64", "-D_GNU_SOURCE",
        f"-I{tree}/src", f"-I{tree}", f"-I{SRC}", f"-I{SRC}/src",
        "-Wall", "-Wextra", "-O2",
        "-ffunction-sections", "-fdata-sections",
        os.path.join(SRC, "tests", "l2s", "l2s_handler_test.c"),
        "-Wl,--gc-sections", "-ltalloc", "-o", binary,
    ]
    built = subprocess.run(compile_command, capture_output=True, text=True)
    if built.returncode != 0:
        return None, built.stdout + built.stderr

    run = subprocess.run([binary], capture_output=True, text=True)
    if verbose:
        print(run.stdout)
    return run.returncode, run.stdout + run.stderr


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    text = read_count_test_source()
    work = tempfile.mkdtemp(prefix="l2s-handler-mutations.")
    failures = []
    checks = 0

    def control(name, mutated):
        nonlocal checks
        checks += 1
        tree = os.path.join(work, f"case-{checks:02d}")
        os.makedirs(os.path.join(tree, os.path.dirname(RELATIVE)), exist_ok=True)
        with open(os.path.join(tree, RELATIVE), "w", encoding="utf-8") as handle:
            handle.write(mutated)

        status, output = build_and_run(tree, args.verbose)
        if status is None:
            failures.append((name, "the mutated tree doesn't compile"))
            print(f"FAIL {name}: the mutated tree doesn't compile")
            print(output)
        elif status == 0:
            failures.append((name, "the handler test stayed green"))
            print(f"FAIL {name}: the handler test stayed green")
        else:
            failed = [line for line in output.splitlines() if line.startswith("FAIL ")]
            print(f"ok   {name} -> {len(failed)} failing checks")
            for line in failed[:3]:
                print(f"     {line}")
        shutil.rmtree(tree, ignore_errors=True)

    # 0. The code as it is has to pass.
    checks += 1
    tree = os.path.join(work, "clean")
    os.makedirs(os.path.join(tree, os.path.dirname(RELATIVE)), exist_ok=True)
    with open(os.path.join(tree, RELATIVE), "w", encoding="utf-8") as handle:
        handle.write(text)
    status, output = build_and_run(tree, args.verbose)
    if status != 0:
        failures.append(("the tree as it is", "the handler test doesn't pass"))
        print("FAIL the tree as it is: the handler test doesn't pass")
        print(output)
    else:
        print("ok   the tree as it is passes the handler test")
    shutil.rmtree(tree, ignore_errors=True)

    for name, old, new in MUTATIONS:
        if old not in text:
            checks += 1
            failures.append((name, "the text to mutate is not in the tree"))
            print(f"FAIL {name}: the text to mutate is not in the tree")
            continue
        control(name, text.replace(old, new, 1))

    shutil.rmtree(work, ignore_errors=True)

    print(f"\n{checks} controls, {len(failures)} unexpected results")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
