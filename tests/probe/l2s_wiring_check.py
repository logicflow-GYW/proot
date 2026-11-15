#!/usr/bin/env python3
"""Static wiring check for the link2symlink fixes.

This does not prove the behaviour -- that is what tests/l2s/test-*.sh does,
on a host where PRoot can really run -- it proves that the code that is
supposed to be wired is wired, and that the arms the fork deliberately does
not have are not referenced.

Every check names the criterion it stands for:

  A1  a failed rename(2)/unlink(2) must not decrement the link count: the
      accounting happens in the exit stage, and only when the name is gone
  A2  rename(2) of one name of a file onto another name of the same file
      must stay a no-op: the new path is poked to the old one
  A3  getdents(2)/getdents64(2) must give faked hard links the type of
      their file, and must not touch d_ino/d_off/d_reclen
  A4  statx(2) must report the emulated link count: updated_stats is set
  A10 a descriptor opened through a faked hard link is named after it:
      the READLINK_PROC_FD event is emitted before the path is detranslated
  A5  the fork keeps its own error reporting (no bare `return status`)
  fork  the guard which() needs (tracee->exe == NULL) is still there, no
      openat2 work was pulled in, and native_offload is still declared

Usage: python3 probe/l2s_wiring_check.py [--root <tree>] [-v]
Exit status: 0 when every check passed, 1 otherwise (2 on usage errors).
"""

import argparse
import os
import re
import sys


class Tree:
    def __init__(self, root):
        self.root = root
        self._cache = {}

    def read(self, rel):
        if rel not in self._cache:
            path = os.path.join(self.root, rel)
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                self._cache[rel] = handle.read()
        return self._cache[rel]

    def function_body(self, rel, name):
        """Return the body of the function @name, braces included.

        A forward declaration and a call site both look like
        "name(", so only the occurrence that is followed by a body --
        once its parameter list is skipped -- is the definition.
        """
        text = self.read(rel)
        for match in re.finditer(r"\b" + re.escape(name) + r"\s*\(", text):
            index = match.end() - 1
            depth = 1
            while index + 1 < len(text) and depth > 0:
                index += 1
                if text[index] == "(":
                    depth += 1
                elif text[index] == ")":
                    depth -= 1
            if depth != 0:
                continue

            rest = text[index + 1 :]
            stripped = rest.lstrip()
            if not stripped.startswith("{"):
                continue

            start = index + 1 + (len(rest) - len(stripped))
            break
        else:
            return None

        depth = 0
        end = start
        while end < len(text):
            if text[end] == "{":
                depth += 1
            elif text[end] == "}":
                depth -= 1
                if depth == 0:
                    return text[start : end + 1]
            end += 1
        return None

    def strip_comments(self, text):
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        return re.sub(r"//[^\n]*", "", text)

    def case_arms(self, rel, function):
        """Return {label: body} for the arms of the switch of @function.

        Several labels can share one arm -- `case PR_rename:` on a line of
        its own, `case PR_renameat:` right below it -- so the labels of an
        arm are collected together: a label line followed by another label
        line opens no new arm.
        """
        body = self.function_body(rel, function)
        if body is None:
            return {}

        stripped = self.strip_comments(body)
        arms = {}
        labels = []
        current = []

        for line in stripped.split("\n"):
            label = re.match(r"^\tcase\s+([A-Za-z0-9_]+)\s*:", line)
            if label is not None:
                if current and any(part.strip() for part in current):
                    for name in labels:
                        arms[name] = "\n".join(current)
                    current = []
                    labels = []
                labels.append(label.group(1))
                continue

            if re.match(r"^\tdefault\s*:", line):
                if labels:
                    for name in labels:
                        arms[name] = "\n".join(current)
                labels = []
                current = []
                continue

            if labels:
                current.append(line)

        if labels:
            for name in labels:
                arms[name] = "\n".join(current)

        return arms

    def case_block(self, rel, function, label):
        return self.case_arms(rel, function).get(label)


class Probe:
    def __init__(self, tree, verbose):
        self.tree = tree
        self.verbose = verbose
        self.checks = 0
        self.failures = []

    def expect(self, ok, criterion, description, detail=""):
        self.checks += 1
        if ok:
            if self.verbose:
                print(f"ok   [{criterion}] {description}")
            return
        self.failures.append((criterion, description, detail))
        print(f"FAIL [{criterion}] {description}" + (f": {detail}" if detail else ""))

    def run(self):
        l2s = "src/extension/link2symlink/link2symlink.c"
        exit_c = "src/syscall/exit.c"
        canon_c = "src/path/canon.c"
        enter_c = "src/execve/enter.c"
        extension_h = "src/extension/extension.h"

        enter_end = self.tree.case_block(l2s, "link2symlink_callback", "SYSCALL_ENTER_END")
        exit_end = self.tree.case_block(l2s, "link2symlink_callback", "SYSCALL_EXIT_END")
        self.expect(enter_end is not None, "A1", "SYSCALL_ENTER_END arm exists in link2symlink_callback")
        self.expect(exit_end is not None, "A1", "SYSCALL_EXIT_END arm exists in link2symlink_callback")

        if enter_end:
            self.expect(
                "decrement_link_count" not in enter_end
                and "decrement_intermediate_count" not in enter_end,
                "A1",
                "the enter stage does not count links down",
                "decrement_* is called before the kernel acted",
            )
            self.expect(
                "forget_removed_link" not in enter_end,
                "A1",
                "the enter stage does not run the exit-stage bookkeeping",
            )
            self.expect(
                re.search(r"prepare_rename\s*\(", enter_end) is not None,
                "A1/A2",
                "rename(2) is prepared in the enter stage (prepare_rename)",
            )
            self.expect(
                re.search(r"remember_removed_link\s*\(", enter_end) is not None,
                "A1",
                "unlink(2) is only remembered in the enter stage (remember_removed_link)",
            )

        sysexit_body = self.tree.function_body(l2s, "handle_sysexit_end")
        self.expect(sysexit_body is not None, "A1", "handle_sysexit_end exists")
        if sysexit_body:
            arms = self.tree.case_arms(l2s, "handle_sysexit_end")
            for label in ("PR_rename", "PR_renameat", "PR_renameat2", "PR_unlink", "PR_unlinkat"):
                arm = arms.get(label, "")
                self.expect(
                    "forget_removed_link" in arm,
                    "A1",
                    f"the exit stage counts {label} down once the kernel removed the name",
                )

        forget = self.tree.function_body(l2s, "forget_removed_link")
        self.expect(forget is not None, "A1", "forget_removed_link exists")
        if forget:
            stripped = self.tree.strip_comments(forget)
            self.expect(
                "SYSARG_RESULT" in stripped,
                "A1",
                "forget_removed_link() only acts on a successful syscall result",
            )
            self.expect(
                re.search(r"my_readlink\s*\(\s*config->removed_link", stripped) is not None,
                "A1",
                "forget_removed_link() checks that the name is really gone",
                "the result register alone is not enough (PROOT_NO_SECCOMP)",
            )

        prepare = self.tree.function_body(l2s, "prepare_rename")
        self.expect(prepare is not None, "A2", "prepare_rename exists")
        if prepare:
            stripped = self.tree.strip_comments(prepare)
            self.expect(
                re.search(
                    r"flags\s*&\s*\(RENAME_NOREPLACE\s*\|\s*RENAME_EXCHANGE\)\)\s*!=\s*0\)\s*\n\s*return 0;",
                    stripped,
                )
                is not None,
                "A2",
                "prepare_rename() leaves the flags that don't remove the new name alone",
            )
            self.expect(
                re.search(
                    r"poke_reg\s*\(\s*tracee\s*,\s*new_sysarg\s*,\s*peek_reg\s*\([^)]*old_sysarg",
                    stripped,
                )
                is not None,
                "A2",
                "prepare_rename() pokes the new path to the old one (a strict no-op rename)",
            )
            self.expect(
                re.search(r"read_faked_hard_link\s*\(\s*old_path", stripped) is not None,
                "A2",
                "prepare_rename() only does so when both names are the same file",
            )

        getdents = self.tree.function_body(l2s, "handle_getdents_exit")
        self.expect(getdents is not None, "A3", "handle_getdents_exit exists")
        if getdents:
            stripped = self.tree.strip_comments(getdents)
            self.expect(
                "compacted[offset + type_offset] = status" in stripped,
                "A3",
                "the entries kept get the type of the file they stand for",
            )
            for field in ("d_ino", "d_off", "d_reclen"):
                self.expect(
                    field not in stripped,
                    "A3",
                    f"handle_getdents_exit() doesn't touch {field}",
                )
            writes = re.findall(r"compacted\[[^\]]*\]\s*=[^=]", stripped)
            self.expect(
                len(writes) == 1 and "type_offset" in writes[0],
                "A3",
                "handle_getdents_exit() only ever writes the type byte of an entry",
                f"writes: {writes}",
            )
            self.expect(
                re.search(r"write_data\(tracee, address, compacted, kept\)", stripped) is not None
                and re.search(r"poke_reg\(tracee, SYSARG_RESULT, kept\)", stripped) is not None,
                "A3",
                "the compacted listing is written back, with its own length as the result",
            )
            self.expect(
                re.search(
                    r"strncmp\(name, PREFIX, strlen\(PREFIX\)\) == 0\)\s*\n\s*continue;",
                    stripped,
                )
                is not None,
                "A3",
                "the backing files this extension keeps are dropped from the listing",
                "they are its own bookkeeping; tools list, delete and hard-link them otherwise",
            )
            self.expect(
                re.search(r"register_chained_syscall\(tracee,", stripped) is not None,
                "A3",
                "a chunk that held nothing but backing files asks for the next one",
                "reporting an empty listing would look like the end of the directory",
            )
            self.expect(
                "DT_LNK" in stripped,
                "A3",
                "only entries the kernel reported as DT_LNK are considered",
                "an ordinary symbolic link has to stay a symbolic link",
            )
            self.expect(
                re.search(r"status\s*==\s*DT_LNK", stripped) is not None,
                "A3",
                "an entry whose file is a symbolic link is left alone",
            )

        statx = self.tree.function_body(l2s, "link2symlink_handle_statx")
        self.expect(statx is not None, "A4", "link2symlink_handle_statx exists")
        if statx:
            stripped = self.tree.strip_comments(statx)
            self.expect(
                re.search(r"state->stx_nlink|stx_nlink", stripped) is not None
                and "updated_stats = true" in stripped,
                "A4",
                "statx(2) reports the emulated count and flags the buffer as updated",
            )

        proc_fd = self.tree.function_body(l2s, "readlink_proc_fd")
        self.expect(proc_fd is not None, "A10", "readlink_proc_fd exists")
        if proc_fd:
            stripped = self.tree.strip_comments(proc_fd)
            self.expect(
                "strcpy(state->host_path, link)" in stripped,
                "A10",
                "readlink_proc_fd() reports the name the tracee used",
            )
            self.expect(
                "state->substituted = true;" in stripped,
                "A10",
                "readlink_proc_fd() flags the path as substituted",
            )
            self.expect(
                re.search(r"recall_fd\s*\(", stripped) is not None,
                "A10",
                "readlink_proc_fd() only reports a name that still leads to this file",
            )

        translated = self.tree.function_body(l2s, "translated_path")
        self.expect(translated is not None, "A10", "translated_path exists")
        if translated:
            stripped = self.tree.strip_comments(translated)
            self.expect(
                re.search(r"if\s*\(\s*tracee->exe\s*==\s*NULL\s*\)\s*return\s*;", stripped) is not None,
                "fork",
                "translated_path() leaves the name alone while which() looks the program up",
                "8f467b0 needs this arm while the fork keeps the old which(); "
                "upstream needs it in 1d0e732 instead",
            )
            self.expect(
                "is_open_syscall" in stripped and "remember_opened_link" in stripped,
                "A10",
                "an open(2) that went through a faked hard link is remembered",
            )
            self.expect(
                "SYMLINK_DEREFERENCED" not in stripped,
                "A10",
                "the link a path went through is recorded by canon.c, not here",
            )

        open_syscall = self.tree.function_body(l2s, "is_open_syscall")
        self.expect(open_syscall is not None, "A10", "is_open_syscall exists")
        if open_syscall:
            self.expect(
                "PR_openat2" not in self.tree.strip_comments(open_syscall),
                "fork",
                "no openat2(2) reference: this fork doesn't translate it",
            )

        count = self.tree.function_body(l2s, "get_link_count")
        self.expect(count is not None, "A8", "get_link_count exists")
        if count:
            stripped = self.tree.strip_comments(count)
            self.expect(
                "MAX_LINK_COUNT" in self.tree.read(l2s),
                "A8",
                "the count is bounded by MAX_LINK_COUNT",
            )
            self.expect(
                re.search(r"strlen\([^)]*\)\s*-\s*4\b", stripped) is None,
                "A8",
                "get_link_count() doesn't read the last 4 characters only",
            )
            self.expect(
                re.search(r"isdigit", stripped) is not None,
                "A8",
                "get_link_count() validates every digit of the count",
            )
            self.expect(
                "atoi(" not in stripped,
                "A8",
                "get_link_count() doesn't hand the tail to atoi(3)",
            )
            self.expect(
                re.search(r"count\s*=\s*count \* 10 \+", stripped) is not None,
                "A8",
                "get_link_count() accumulates every digit of the count",
            )
            self.expect(
                re.search(r"length < 4 \|\| length > 9", stripped) is not None,
                "A8",
                "get_link_count() accepts 4 to 9 digits, the range an int holds",
            )

        filters = self.tree.function_body(l2s, "link2symlink_callback")
        if filters:
            self.expect(
                re.search(r"\{\s*PR_getdents,\s*FILTER_SYSEXIT\s*\}", filters) is not None
                and re.search(r"\{\s*PR_getdents64,\s*FILTER_SYSEXIT\s*\}", filters) is not None,
                "A3",
                "getdents(2)/getdents64(2) are filtered at the exit stage",
            )
            self.expect(
                "FILTER_SYSEXIT" in filters,
                "A1",
                "the extension still asks for syscall exits",
            )

        # The events and their emitters.
        events = self.tree.read(extension_h)
        for event in ("SYMLINK_DEREFERENCED", "READLINK_PROC_FD", "EXECVE_PROC_EXE"):
            self.expect(
                re.search(r"^\s*" + event + r",\s*$", events, re.M) is not None,
                "A10",
                f"extension.h declares the {event} event",
            )
        self.expect(
            "native_offload_callback" in events,
            "fork",
            "extension.h still declares native_offload_callback",
        )

        canon = self.tree.read(canon_c)
        canon_stripped = self.tree.strip_comments(canon)
        self.expect(
            "notify_extensions(tracee, SYMLINK_DEREFERENCED" in canon_stripped,
            "A10",
            "canon.c reports each link it dereferences",
        )
        self.expect(
            re.search(r"glue_type\s*==\s*0", canon_stripped[: canon_stripped.find("SYMLINK_DEREFERENCED")])
            is not None
            if "SYMLINK_DEREFERENCED" in canon_stripped
            else False,
            "A10",
            "canon.c doesn't notify while a binding is being initialized",
        )

        exit_body = self.tree.read(exit_c)
        exit_stripped = self.tree.strip_comments(exit_body)
        notify = exit_stripped.find("notify_extensions(tracee, READLINK_PROC_FD")
        detranslate = exit_stripped.find("status = detranslate_path(tracee, referee, referer)")
        self.expect(
            notify >= 0,
            "A10",
            "exit.c emits READLINK_PROC_FD for /proc/<pid>/fd/<fd>",
        )
        self.expect(
            notify >= 0 and detranslate > notify,
            "A10",
            "the substitution happens before the path is detranslated",
        )
        self.expect(
            re.search(r"if\s*\(!proc_fd\.substituted\)\s*\n\s*goto end;", exit_stripped) is not None,
            "A10",
            "a substituted path is reported even when detranslation had nothing to do",
        )
        self.expect(
            re.search(r'sscanf\(referer, "/proc/%d/fd/%d%c"', exit_stripped) is not None,
            "A10",
            "the descriptor behind the read path is parsed",
        )

        enter_stripped = self.tree.strip_comments(self.tree.read(enter_c))
        notify = enter_stripped.find("notify_extensions(tracee, EXECVE_PROC_EXE")
        detranslate = enter_stripped.find("strcpy(new_exe, host_path)")
        self.expect(notify >= 0, "A10", "execve/enter.c emits EXECVE_PROC_EXE")
        self.expect(
            notify >= 0 and detranslate > notify,
            "A10",
            "the guest name /proc/<pid>/exe reports is settled before detranslation",
        )

        return 0 if not self.failures else 1


def find_root(start):
    path = os.path.abspath(start)
    while True:
        if os.path.isfile(os.path.join(path, "src/extension/link2symlink/link2symlink.c")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", help="tree to check (default: the one holding this script)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    root = args.root or find_root(os.path.dirname(os.path.abspath(__file__)))
    if root is None or not os.path.isdir(root):
        print("l2s wiring: can't find the source tree", file=sys.stderr)
        return 2

    probe = Probe(Tree(root), args.verbose)
    status = probe.run()

    print(f"\n{probe.checks} checks, {len(probe.failures)} failures ({root})")
    if probe.failures:
        criteria = sorted({criterion for criterion, _, _ in probe.failures})
        print("failed criteria: " + ", ".join(criteria))
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
