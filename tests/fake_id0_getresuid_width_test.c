/* -*- c-set-style: "K&R"; c-basic-offset: 8 -*-
 *
 * This file is part of PRoot.
 *
 * Copyright (C) 2015 STMicroelectronics
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License as
 * published by the Free Software Foundation; either version 2 of the
 * License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
 * 02110-1301 USA.
 */

/*
 * Unit test for the fake_id0 getresuid/getresgid ID width.
 *
 * The bug (fixed upstream by c1cd48d): POKE_MEM_ID always wrote 16
 * bits, so a guest running under an Android secondary user / work
 * profile (uid = userId * 100000 + appId, i.e. above 65535) saw a
 * truncated EUID -- only the low half of the real ID was replaced by
 * the faked 0.
 *
 * The width now depends on the syscall and on the ABI of the tracee:
 *
 *   - legacy getresuid/getresgid of a 32-bit ABI  -> 16-bit IDs
 *   - legacy getresuid/getresgid of a 64-bit ABI  -> 32-bit IDs
 *   - getresuid32/getresgid32 (any ABI)           -> 32-bit IDs
 *
 * The test drives the *production* static functions
 * handle_getresuid_exit_end() / handle_getresgid_exit_end() of
 * fake_id0.c.  fake_id0.c is #include-d, so those functions are the
 * real ones, not copies.  Everything else (the ABI lookup, the
 * register access, the memory writes) is stubbed, so the test needs
 * neither a tracee nor ptrace.
 *
 * The test compiles against both the pre-fix and the post-fix
 * production code: the two differ in the signature of the handlers
 * (the post-fix ones take the syscall number), which is detected
 * with S05A_FIXED.  The *expectations* are the same in both cases,
 * so the pre-fix code makes this test fail -- that is the point.
 *
 * Build: see the comment at the bottom of this file.
 */

#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "tracee/tracee.h"
#include "tracee/reg.h"
#include "tracee/abi.h"
#include "tracee/mem.h"
#include "syscall/sysnum.h"

/* ------------------------------------------------------------------ *
 * State of the stubs
 * ------------------------------------------------------------------ */

/* The tracee ABI the stubbed get_abi() reports.  */
static Abi stub_abi = ABI_DEFAULT;

/* The fake IDs written into the tracee's memory.  */
#define STUB_RUID 1011627  /* 0xF6FAB -- above 65535.  */
#define STUB_EUID 1011628  /* 0xF6FAC */
#define STUB_SUID 1011629  /* 0xF6FAD */
#define STUB_RGID 1011630  /* 0xF6FAE */
#define STUB_EGID 1011631  /* 0xF6FAF */
#define STUB_SGID 1011632  /* 0xF6FB0 */

/* The syscall number the stubbed get_sysnum() reports.  */
static Sysnum stub_sysnum = PR_getresuid;

/* What the stubbed memory layer observed.  */
static unsigned int observed_writes;  /* Number of writes.  */
static unsigned int observed_width;   /* 16 or 32.  */
static uint32_t observed_values[8];   /* Values, in write order.  */

/* Addresses of the three out-parameters of getresuid/getresgid.  */
#define STUB_ADDR_1 0x1000
#define STUB_ADDR_2 0x2000
#define STUB_ADDR_3 0x3000

/* ------------------------------------------------------------------ *
 * Stubs
 *
 * They are declared before fake_id0.c is included and installed with
 * macros afterwards, so that the *call sites* inside fake_id0.c refer
 * to the stubs while the declarations in the headers stay untouched.
 * ------------------------------------------------------------------ */

static Abi stub_get_abi(const Tracee *tracee);
static word_t stub_peek_reg(const Tracee *tracee, RegVersion version, Reg reg);
static void stub_poke_uint16(Tracee *tracee, word_t address, uint16_t value);
static void stub_poke_uint32(Tracee *tracee, word_t address, uint32_t value);
static Sysnum stub_get_sysnum(const Tracee *tracee, RegVersion version);

#define get_abi     stub_get_abi
#define peek_reg    stub_peek_reg
#define poke_uint16 stub_poke_uint16
#define poke_uint32 stub_poke_uint32
#define get_sysnum  stub_get_sysnum

/* The production source, pulled in as-is.  */
#include "extension/fake_id0/fake_id0.c"

#undef get_abi
#undef peek_reg
#undef poke_uint16
#undef poke_uint32
#undef get_sysnum

/*
 * The post-fix handlers take the syscall number, the pre-fix ones do
 * not.  C has no compile-time overloading, so the arity is detected
 * at runtime through a function-pointer assignment: assigning a
 * three-argument function to a two-argument function pointer is a
 * compile error, not a runtime one, so the two cases are selected
 * with a preprocessor switch driven by S05A_FIXED.
 *
 * The switch is set by the build script from the *content* of the
 * production source, so it cannot drift from the code under test.
 */
#ifdef S05A_FIXED
#  define CALL_GETRESUID(tracee, config, sysnum) handle_getresuid_exit_end(tracee, config, sysnum)
#  define CALL_GETRESGID(tracee, config, sysnum) handle_getresgid_exit_end(tracee, config, sysnum)
#else
#  define CALL_GETRESUID(tracee, config, sysnum) handle_getresuid_exit_end(tracee, config)
#  define CALL_GETRESGID(tracee, config, sysnum) handle_getresgid_exit_end(tracee, config)
#endif

static Abi stub_get_abi(const Tracee *tracee UNUSED)
{
	return stub_abi;
}

static word_t stub_peek_reg(const Tracee *tracee UNUSED, RegVersion version UNUSED, Reg reg)
{
	switch (reg) {
	case SYSARG_1: return STUB_ADDR_1;
	case SYSARG_2: return STUB_ADDR_2;
	case SYSARG_3: return STUB_ADDR_3;
	default:       return 0;
	}
}

static void stub_poke_uint16(Tracee *tracee UNUSED, word_t address UNUSED, uint16_t value)
{
	if (observed_writes < sizeof(observed_values) / sizeof(observed_values[0]))
		observed_values[observed_writes] = value;
	observed_writes++;
	observed_width = 16;
}

static void stub_poke_uint32(Tracee *tracee UNUSED, word_t address UNUSED, uint32_t value)
{
	if (observed_writes < sizeof(observed_values) / sizeof(observed_values[0]))
		observed_values[observed_writes] = value;
	observed_writes++;
	observed_width = 32;
}

static Sysnum stub_get_sysnum(const Tracee *tracee UNUSED, RegVersion version UNUSED)
{
	return stub_sysnum;
}

/* ------------------------------------------------------------------ *
 * Stubs for the remaining symbols fake_id0.c refers to.
 *
 * None of them is reached by the code paths under test; they exist
 * only so that the translation unit links.
 * ------------------------------------------------------------------ */

void poke_reg(Tracee *tracee UNUSED, Reg reg UNUSED, word_t value UNUSED) { }
void set_sysnum(Tracee *tracee UNUSED, Sysnum sysnum UNUSED) { }
int write_data(Tracee *tracee UNUSED, word_t dest UNUSED, const void *src UNUSED, word_t size UNUSED) { return 0; }
bool should_skip_file_access_due_to_f2fs_bug(const Tracee *tracee UNUSED, const char *path UNUSED) { return false; }
word_t get_elf_aux_vectors_address(const Tracee *tracee UNUSED) { return 0; }
ElfAuxVector *fetch_elf_aux_vectors(const Tracee *tracee UNUSED, word_t address UNUSED) { return NULL; }
int push_elf_aux_vectors(const Tracee *tracee UNUSED, ElfAuxVector *vectors UNUSED, word_t address UNUSED) { return 0; }

int handle_chown_enter_end(Tracee *tracee UNUSED, Config *config UNUSED, Reg uid_sysarg UNUSED, Reg gid_sysarg UNUSED) { return 0; }
int handle_chroot_exit_end(Tracee *tracee UNUSED, Config *config UNUSED, bool from_sigsys UNUSED) { return 0; }
int handle_getsockopt_exit_end(Tracee *tracee UNUSED) { return 0; }
int handle_sendmsg_enter_end(Tracee *tracee UNUSED, word_t sysnum UNUSED) { return 0; }
int handle_socket_exit_end(Tracee *tracee UNUSED, Config *config UNUSED) { return 0; }
int handle_stat_exit_end(Tracee *tracee UNUSED, Config *config UNUSED, Reg stat_sysarg UNUSED) { return 0; }
int fake_id0_handle_statx_syscall(Tracee *tracee UNUSED, Config *config UNUSED, uintptr_t statx_state_raw UNUSED) { return 0; }

/* ------------------------------------------------------------------ *
 * Test harness
 * ------------------------------------------------------------------ */

static unsigned int failures = 0;
static unsigned int checks = 0;

#define CHECK(cond, fmt, ...) do {						\
	checks++;								\
	if (!(cond)) {								\
		failures++;							\
		printf("  FAIL %s:%d: " fmt "\n", __FILE__, __LINE__, ##__VA_ARGS__); \
	}									\
} while (0)

/*
 * Expected width of the ID written by getres[ug]id.
 *
 * The table below is written out literally on purpose: the
 * expectations must NOT be derived from the production macro,
 * otherwise changing the production code would silently change the
 * test too (the test would keep passing while the bug comes back).
 */
typedef struct {
	Sysnum sysnum;
	Abi abi;
	unsigned int width;  /* Expected write width, in bits.  */
} Expectation;

/*
 * On ARM64, ABI_DEFAULT is the 64-bit ABI and ABI_2 is the 32-bit
 * (AArch32) one.  The legacy syscalls report 16-bit IDs only on
 * ABI_2; everything else reports 32-bit IDs.
 */
static const Expectation expectations[] = {
	{ PR_getresuid,   ABI_DEFAULT, 32 },
	{ PR_getresuid32, ABI_DEFAULT, 32 },
	{ PR_getresuid,   ABI_2,       16 },
	{ PR_getresuid32, ABI_2,       32 },

	{ PR_getresgid,   ABI_DEFAULT, 32 },
	{ PR_getresgid32, ABI_DEFAULT, 32 },
	{ PR_getresgid,   ABI_2,       16 },
	{ PR_getresgid32, ABI_2,       32 },
};

static const char *abi_name(Abi abi)
{
	return (abi == ABI_2) ? "ABI_2" : "ABI_DEFAULT";
}

static const char *sysnum_name(Sysnum sysnum)
{
	switch (sysnum) {
	case PR_getresuid:   return "getresuid";
	case PR_getresuid32: return "getresuid32";
	case PR_getresgid:   return "getresgid";
	case PR_getresgid32: return "getresgid32";
	default:             return "?";
	}
}

/*
 * Drive one getres[ug]id through the production handler and check the
 * width of the three writes.
 */
static void check_width(const Expectation *expect)
{
	Config config;
	int status;

	memset(&config, 0, sizeof(config));
	config.ruid = STUB_RUID;
	config.euid = STUB_EUID;
	config.suid = STUB_SUID;
	config.rgid = STUB_RGID;
	config.egid = STUB_EGID;
	config.sgid = STUB_SGID;

	stub_abi = expect->abi;
	stub_sysnum = expect->sysnum;
	observed_writes = 0;
	observed_width = 0;
	memset(observed_values, 0, sizeof(observed_values));

	printf("- %s / %s: expected %u-bit writes\n",
	       sysnum_name(expect->sysnum), abi_name(expect->abi), expect->width);

	if (expect->sysnum == PR_getresuid || expect->sysnum == PR_getresuid32)
		status = CALL_GETRESUID(NULL, &config, expect->sysnum);
	else
		status = CALL_GETRESGID(NULL, &config, expect->sysnum);

	CHECK(status == 0, "handler returned %d", status);

	/* Three out-parameters: ruid/euid/suid (resp. g).  */
	CHECK(observed_writes == 3, "expected 3 writes, got %u", observed_writes);

	CHECK(observed_width == expect->width,
	      "expected %u-bit writes, got %u-bit", expect->width, observed_width);
}

/*
 * Sentinel test: the fake ID is above 65535, so a 32-bit write must
 * carry the whole value while a 16-bit write must truncate it.
 */
static void check_high_bit_sentinel(Sysnum sysnum, Abi abi, unsigned int want)
{
	uint32_t full = (uint32_t) STUB_EUID;
	uint32_t truncated = full & 0xFFFF;
	uint32_t euid_written = 0;

	printf("- sentinel %s / %s: expected %u-bit write\n",
	       sysnum_name(sysnum), abi_name(abi), want);

	Config config;

	memset(&config, 0, sizeof(config));
	config.ruid = STUB_RUID;
	config.euid = STUB_EUID;
	config.suid = STUB_SUID;

	stub_abi = abi;
	stub_sysnum = sysnum;
	observed_writes = 0;
	observed_width = 0;
	memset(observed_values, 0, sizeof(observed_values));

	CALL_GETRESUID(NULL, &config, sysnum);

	/* The 2nd write is the effective ID.  */
	CHECK(observed_writes == 3, "expected 3 writes, got %u", observed_writes);
	euid_written = observed_values[1];

	if (want == 32) {
		CHECK(euid_written == full,
		      "expected the full ID 0x%X, got 0x%X", full, euid_written);
		CHECK(euid_written != truncated,
		      "the ID was truncated to 0x%X", truncated);
	} else {
		CHECK(euid_written == truncated,
		      "expected the truncated ID 0x%X, got 0x%X", truncated, euid_written);
	}
}

int main(void)
{
	unsigned int i;

	printf("fake_id0 getres[ug]id ID width\n");

	for (i = 0; i < sizeof(expectations) / sizeof(expectations[0]); i++)
		check_width(&expectations[i]);

	/* Sentinel: an ID above 65535 must survive on a 64-bit tracee.  */
	check_high_bit_sentinel(PR_getresuid, ABI_DEFAULT, 32);
	check_high_bit_sentinel(PR_getresuid32, ABI_DEFAULT, 32);

	printf("%u checks, %u failures\n", checks, failures);

	return failures == 0 ? 0 : 1;
}

/*
 * Build:
 *
 *   gcc -D_FILE_OFFSET_BITS=64 -D_GNU_SOURCE -I src \
 *       -ffunction-sections -fdata-sections \
 *       -o /tmp/s05a_test tests/fake_id0_getresuid_width_test.c \
 *       -Wl,--gc-sections -ltalloc
 *
 * -ffunction-sections/-fdata-sections plus --gc-sections let the
 * linker drop the parts of fake_id0.c this test does not reach, so
 * only the symbols used by the test have to be resolvable.
 */
