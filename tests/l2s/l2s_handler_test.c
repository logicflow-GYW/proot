/*
 * Local test of the parts of link2symlink that don't need a running
 * tracee: the link count it records in the name of a faked hard link,
 * and the naming/reading of the chains it keeps on disk.
 *
 * The point of having it here is that it runs anywhere a C compiler and
 * libtalloc are available -- the sandbox and the CI runners alike -- while
 * PRoot itself can't run nested under another ptrace-based sandbox.  The
 * behaviour that needs a tracee (a failing rename(2), a rename(2) of a
 * name onto another name of the same file, getdents(2) types) is covered
 * by tests/l2s/test-*.sh, run by run_link2symlink_regression_test.sh on a
 * host where PRoot really can run.
 *
 * The production file is included, not linked against: its functions are
 * static, and -ffunction-sections plus --gc-sections drop the ones this
 * test never calls, so the linker doesn't need the rest of PRoot.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <limits.h>
#include <sys/stat.h>
#include <fcntl.h>

#include "extension/link2symlink/link2symlink.c"

static int failures;
static int checks;

static void report(int ok, const char *what, const char *detail)
{
	checks++;
	if (ok) {
		printf("ok   %s\n", what);
		return;
	}

	failures++;
	printf("FAIL %s%s%s\n", what, detail != NULL ? ": " : "", detail != NULL ? detail : "");
}

static void expect_count(const char *path, int expected)
{
	char what[PATH_MAX + 64];
	int got;

	snprintf(what, sizeof(what), "get_link_count(\"%s\")", path);
	got = get_link_count(path);

	if (got != expected) {
		char detail[64];
		snprintf(detail, sizeof(detail), "expected %d, got %d", expected, got);
		report(0, what, detail);
		return;
	}

	report(1, what, NULL);

	/* is_l2s_file() has to agree with the count it is derived from. */
	snprintf(what, sizeof(what), "is_l2s_file(\"%s\")", path);
	report(is_l2s_file(path) == (expected >= 0), what, NULL);
}

static void expect_name(const char *final, int link_count, const char *expected)
{
	char what[PATH_MAX + 64];
	char new_final[PATH_MAX];
	int status;

	snprintf(what, sizeof(what), "name_link_count(\"%s\", %d)", final, link_count);
	status = name_link_count(final, link_count, new_final);
	if (status < 0) {
		char detail[64];
		snprintf(detail, sizeof(detail), "returned %d", status);
		report(0, what, detail);
		return;
	}

	if (strcmp(new_final, expected) != 0) {
		char detail[PATH_MAX * 2 + 32];
		snprintf(detail, sizeof(detail), "expected \"%s\", got \"%s\"", expected, new_final);
		report(0, what, detail);
		return;
	}

	report(1, what, NULL);
}

static void expect_status(int got, int expected, const char *what)
{
	char detail[64];

	if (got == expected) {
		report(1, what, NULL);
		return;
	}

	snprintf(detail, sizeof(detail), "expected %d, got %d", expected, got);
	report(0, what, detail);
}

static void unlink_path(const char *path)
{
	if (unlink(path) < 0 && errno != ENOENT)
		perror(path);
}

static void create_file(const char *path)
{
	int fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0600);

	if (fd < 0) {
		perror(path);
		exit(2);
	}
	close(fd);
}

int main(void)
{
	char dir[] = "/tmp/l2s-handler-test.XXXXXX";
	char path[PATH_MAX];
	char scratch[PATH_MAX];
	struct stat statl;

	if (mkdtemp(dir) == NULL) {
		perror("mkdtemp");
		return 2;
	}

	/* 1. Names this extension writes: "<PREFIX><name><NNNN>.<count>".
	 * The count is written with 4 digits at least, 5 from 10000 links
	 * on -- reading it back as the last 4 characters, as this
	 * extension used to, made the 10000th link look like a broken
	 * chain holding a single link. */
	expect_count("/root/.l2s.a0001.0002", 2);
	expect_count("/root/.l2s.a0001.0003", 3);
	expect_count("/root/.l2s.a0001.9999", 9999);
	expect_count("/root/.l2s.a0001.10000", 10000);
	expect_count("/root/.l2s.a0001.123456", 123456);
	expect_count("/root/.l2s.a0001.999999999", 999999999);

	/* A count that doesn't fit an int, a shorter one, a missing one. */
	expect_count("/root/.l2s.a0001.1000000000", -1);
	expect_count("/root/.l2s.a0001.000", -1);
	expect_count("/root/.l2s.a0001.", -1);

	/* 2. Names that are not faked hard links at all. */
	expect_count("a", -1);
	expect_count("/root/a", -1);
	expect_count("/root/.l2s.a", -1);
	expect_count("/root/.l2s.", -1);
	expect_count("/root/.l2s.a0001", -1);          /* an intermediate */
	expect_count("/root/x.l2s.a0001.0002", -1);    /* not at the start */
	expect_count("/root/.l2s.9999", -1);

	/* 3. A chain an older version left in place: the intermediate is
	 * "<PREFIX><name><NNNN>" and the file "<PREFIX><name><NNNN>.<count>"
	 * next to it.  Reinstalling the rootfs is not something an upgrade
	 * can ask for, so these names have to keep working. */
	expect_count("/root/.l2s.report0001.0002", 2);
	expect_count("/root/.l2s.report0007.0005", 5);
	if (snprintf(path, sizeof(path), "%s/.l2s.legacy0001.0002", dir) >= PATH_MAX) {
		return 2;
	}
	if (snprintf(scratch, sizeof(scratch), "%s/.l2s.legacy0001", dir) >= PATH_MAX) {
		return 2;
	}
	if (symlink(path, scratch) < 0) {
		perror("symlink");
		return 2;
	}
	/* The intermediate of a legacy chain points to the counted name. */
	expect_status(read_intermediate(scratch, path), 0, "read_intermediate(legacy intermediate)");
	expect_status(strcmp(path, "") == 0 ? -1 : 0, 0, "legacy intermediate keeps its count");

	/* 4. Renaming a count across the 4-digit boundary rewrites the whole
	 * count, rather than its last four characters. */
	expect_name("/root/.l2s.a0001.9999", 10000, "/root/.l2s.a0001.10000");
	expect_name("/root/.l2s.a0001.10000", 9999, "/root/.l2s.a0001.9999");
	expect_name("/root/.l2s.a0001.0002", 3, "/root/.l2s.a0001.0003");
	expect_name("/root/sub/dir/.l2s.report0007.0005", 6, "/root/sub/dir/.l2s.report0007.0006");

	/* 5. Reading the chains on disk.  A chain this extension made: the
	 * name is a symbolic link to the intermediate, which points to the
	 * counted file. */
	snprintf(path, sizeof(path), "%s/.l2s.real0001.0002", dir);
	create_file(path);
	snprintf(scratch, sizeof(scratch), "%s/.l2s.real0001", dir);
	if (symlink(path, scratch) < 0) {
		perror("symlink");
		return 2;
	}
	{
		char name[PATH_MAX];
		char intermediate[PATH_MAX];
		char final[PATH_MAX];

		snprintf(name, sizeof(name), "%s/real", dir);
		if (symlink(scratch, name) < 0) {
			perror("symlink");
			return 2;
		}

		expect_status(read_faked_hard_link(name, intermediate), 0, "read_faked_hard_link(faked)");
		expect_status(strcmp(intermediate, scratch) == 0 ? 0 : -1, 0,
				"read_faked_hard_link() reports the intermediate");
		expect_status(read_intermediate(intermediate, final), 0, "read_intermediate(faked)");
		expect_status(strcmp(final, path) == 0 ? 0 : -1, 0,
				"read_intermediate() reports the counted file");
		expect_status(find_intermediate(path, intermediate), 0, "find_intermediate(counted file)");
		expect_status(strcmp(intermediate, scratch) == 0 ? 0 : -1, 0,
				"find_intermediate() finds the intermediate next to it");

		/* An ordinary symbolic link is not a faked hard link. */
		snprintf(name, sizeof(name), "%s/ordinary", dir);
		if (symlink("somewhere", name) < 0) {
			perror("symlink");
			return 2;
		}
		expect_status(read_faked_hard_link(name, intermediate), -EINVAL,
				"read_faked_hard_link(ordinary symlink)");

		/* A chain that doesn't end at a counted name was not made by
		 * this extension: nothing may be renamed or deleted on its
		 * behalf, hence -ENOENT rather than a count parsed out of
		 * whatever name it ends at. */
		snprintf(name, sizeof(name), "%s/.l2s.foreign0001", dir);
		snprintf(scratch, sizeof(scratch), "%s/plain", dir);
		create_file(scratch);
		if (symlink(scratch, name) < 0) {
			perror("symlink");
			return 2;
		}
		expect_status(read_intermediate(name, final), -ENOENT,
				"read_intermediate(chain not made by us)");

		/* A broken chain: the intermediate exists, the file it points
		 * to doesn't.  Reading the intermediate still answers its
		 * content -- readlink(2) doesn't care about the target -- and
		 * the count is still parsed out of it; what tells it apart is
		 * find_intermediate(), which stats both ends. */
		snprintf(name, sizeof(name), "%s/.l2s.gone0001", dir);
		snprintf(scratch, sizeof(scratch), "%s/.l2s.gone0001.0002", dir);
		if (symlink(scratch, name) < 0) {
			perror("symlink");
			return 2;
		}
		expect_status(read_intermediate(name, final), 0,
				"read_intermediate(broken chain) still reads the count");
		expect_status(find_intermediate(scratch, final), -ENOENT,
				"find_intermediate(broken chain) refuses to name an intermediate");
	}

	/* 6. PROOT_L2S_DIR names the entries relative to a directory
	 * descriptor, and keeps working when it is unset (the default, and
	 * the only configuration this app uses). */
	expect_status(l2s_lstat(path, &statl), 0, "l2s_lstat(counted file)");
	expect_status(S_ISREG(statl.st_mode) ? 0 : -1, 0, "l2s_lstat() reports a regular file");

	/* Cleanup: the test writes nothing outside of @dir, so unlinking the
	 * names it created and the directory itself is enough -- no shell. */
	{
		static const char *const names[] = {
			"real", "ordinary", "plain",
			".l2s.real0001", ".l2s.real0001.0002",
			".l2s.foreign0001", ".l2s.gone0001",
			".l2s.legacy0001", ".l2s.legacy0001.0002",
		};
		size_t i;

		for (i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
			snprintf(path, sizeof(path), "%s/%s", dir, names[i]);
			unlink_path(path);
		}
		if (rmdir(dir) < 0 && errno != ENOENT)
			perror(dir);
	}

	printf("\n%d checks, %d failures\n", checks, failures);
	return failures == 0 ? 0 : 1;
}
