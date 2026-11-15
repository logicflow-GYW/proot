/* Report what readlink(2) answers for /proc/self/fd/<fd> through a
 * deliberately small buffer, then through a large one.
 *
 * readlink(2) reports at most the size of the buffer it is given and
 * gives no hint that the name was longer.  busybox's readlink(1) reads
 * short chunks, so on Android -- where the guest tool of a terminal is
 * busybox -- the kernel truncates the host path of the backing file the
 * link2symlink extension hides.  The extension used to give up
 * rewriting the name in that case: the tracee saw the backing file.
 * A large buffer is the control: it always answered the name the tracee
 * opened.
 *
 * The small buffer defaults to 32 bytes; a second argument overrides it,
 * so a test can cut the host path right inside the backing file name
 * whatever the length of its temporary directory.
 */
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(int argc, char **argv)
{
	char proc[64];
	char small[4096];
	char large[4096];
	size_t small_size_wanted = 32;
	ssize_t small_size;
	ssize_t large_size;
	int fd;

	if (argc < 2)
		return 2;

	if (argc > 2) {
		char *end = NULL;
		long value = strtol(argv[2], &end, 10);

		/* < sizeof(small): a buffer of 4096 would make small[4096]='\0'
		 * write past the array. */
		if (end == argv[2] || *end != '\0' || value < 2 || value >= (long) sizeof(small))
			return 2;
		small_size_wanted = (size_t) value;
	}

	fd = open(argv[1], O_RDONLY);
	if (fd < 0)
		return 1;

	snprintf(proc, sizeof(proc), "/proc/self/fd/%d", fd);

	small_size = readlink(proc, small, small_size_wanted);
	if (small_size < 0)
		return 1;
	small[small_size] = '\0';

	large_size = readlink(proc, large, sizeof(large) - 1);
	if (large_size < 0)
		return 1;
	large[large_size] = '\0';

	printf("small=%s\n", small);
	printf("large=%s\n", large);

	close(fd);
	return 0;
}
