/* Adapted from dipa-studio/ios-sweet fdad0de8dc0e75c5ea84762adc03f7232dc91f19. */
/*
 * First userspace program for A04BR3 bring-up: mockfs serves the RAM disk
 * itself as /sbin/launchd, so this static binary is PID 1. It proves that
 * exec, the user/kernel boundary, devfs and the console all work, then
 * stays alive (init exiting panics the kernel).
 *
 * tools/mkinit.py wraps the assembled __text into a static Mach-O, so this
 * must stay position independent with no relocations and no writable data:
 * every reference is adr (PC-relative), the PID digit lives on the stack.
 *
 * Raw XNU syscalls: number in x16, svc #0x80, carry set on error.
 */
#define SYS_read 3
#define SYS_write	4
#define SYS_open	5
#define SYS_getpid	20
#define SYS_select	93
#define O_RDWR		2

	.text
	.globl	_start
	.p2align 2
_start:
	/*
	 * The kernel enters a static binary with sp at argc, aligned only to
	 * what the argument/apple strings happened to need. Using a misaligned
	 * sp as a base register raises an SP alignment fault (SIGBUS) on real
	 * hardware (QEMU does not check), so align it like crt0/dyld do.
	 */
	mov	x9, sp
	and	x9, x9, #~15
	mov	sp, x9

	adr	x0, path
	mov	x1, #O_RDWR
	mov	x2, #0
	mov	x16, #SYS_open
	svc	#0x80
	b.cs	hang			/* no console: nothing to report to */
	mov	x19, x0
    mov x25, #0
    /* Give the diagnostic child real standard console descriptors. */
    mov x0, x19
    mov x1, #1
    mov x16, #90 /* dup2 */
    svc #0x80
    mov x0, x19
    mov x1, #2
    mov x16, #90
    svc #0x80

	adr	x1, hello
	adr	x2, hello_tail
	sub	x2, x2, x1
	bl	write_console

	mov	x16, #SYS_getpid
	svc	#0x80
	add	w0, w0, #'0'		/* PID 1 expected: one digit */
	sub	sp, sp, #16
	strb	w0, [sp]
	mov	x1, sp
	mov	x2, #1
	bl	write_console
	add	sp, sp, #16

	adr	x1, hello_tail
	adr	x2, alive
	sub	x2, x2, x1
	bl	write_console

    /* An ordinary root can provide this marker; mockfs has no /etc. */
    adr x0, stage_path
    mov x1, #0
    mov x2, #0
    mov x16, #SYS_open
    svc #0x80
    b.cs tick
    sub sp, sp, #128
    mov x1, sp
    mov x2, #128
    mov x16, #SYS_read
    svc #0x80
    b.cs stage_done
    mov x2, x0
    mov x1, sp
    bl write_console
stage_done:
    add sp, sp, #128

    /* Read the real disk through XNU's raw BSD device, without writing. */
    adr x0, emmc_path
    mov x1, #0
    mov x2, #0
    mov x16, #SYS_open
    svc #0x80
    b.cs emmc_open_failed
    mov x20, x0
    mov x1, #512
    mov x2, #0
    mov x16, #199 /* lseek */
    svc #0x80
    b.cs emmc_seek_failed
    sub sp, sp, #512
    mov x0, x20
    mov x1, sp
    mov x2, #512
    mov x16, #SYS_read
    svc #0x80
    b.cs emmc_read_failed
    cmp x0, #512
    b.ne emmc_read_failed
    ldr w1, [sp]
    mov w2, #0x4645
    movk w2, #0x2049, lsl #16
    cmp w1, w2
    b.ne emmc_read_failed
    ldr w1, [sp, #4]
    mov w2, #0x4150
    movk w2, #0x5452, lsl #16
    cmp w1, w2
    b.ne emmc_read_failed
    add sp, sp, #512
    mov x0, x20
    mov x16, #6 /* close */
    svc #0x80
    adr x1, emmc_ok
    adr x2, emmc_ok_end
    sub x2, x2, x1
    bl write_console
    b dynamic_start
emmc_read_failed:
    add sp, sp, #512
emmc_seek_failed:
    mov x0, x20
    mov x16, #6
    svc #0x80
emmc_open_failed:
    adr x1, emmc_bad
    adr x2, emmc_bad_end
    sub x2, x2, x1
    bl write_console

dynamic_start:
    adr x0, dynamic_path
    mov x1, #0
    mov x2, #0
    mov x16, #SYS_open
    svc #0x80
    b.cs tick /* ordinary diagnostic roots have no dynamic probe */
    mov x16, #6
    svc #0x80
    mov x16, #2 /* fork: x1 is the child-return flag */
    svc #0x80
    b.cs tick
    cbnz x1, dynamic_child
    mov x25, x0
    b tick
dynamic_poll:
    sub sp, sp, #32
    mov x0, x25
    mov x1, sp
    mov x2, #1 /* WNOHANG */
    mov x3, #0
    mov x16, #7 /* wait4 */
    svc #0x80
    b.cs dynamic_poll_failed
    cbz x0, dynamic_wait_done
    mov x25, #0
    ldr w20, [sp]
    adr x1, child_result
    adr x2, child_result_end
    sub x2, x2, x1
    bl write_console
    adr x22, hex_digits
    mov x23, #8
    mov x24, #28
child_hex:
    lsr w13, w20, w24
    and w13, w13, #15
    ldrb w13, [x22, x13]
    strb w13, [sp, #16]
    mov x1, sp
    add x1, x1, #16
    mov x2, #1
    bl write_console
    sub x24, x24, #4
    subs x23, x23, #1
    b.ne child_hex
    adr x1, newline
    mov x2, #1
    bl write_console
    b dynamic_wait_done
dynamic_poll_failed:
    mov x25, #0
dynamic_wait_done:
    add sp, sp, #32
    b time_sleep
dynamic_child:
    adr x0, dynamic_path
    sub sp, sp, #32
    stp x0, xzr, [sp]
    str xzr, [sp, #16]
    mov x1, sp
    add x2, sp, #16
    mov x16, #59 /* execve */
    svc #0x80
    mov x0, #127
    mov x16, #1 /* exit: never terminate diagnostic PID 1 */
    svc #0x80
    b hang

tick:
    cbnz x25, dynamic_poll
time_sleep:
	/* select(0, NULL, NULL, NULL, &5s) as a portable sleep */
	mov	x0, #0
	mov	x1, #0
	mov	x2, #0
	mov	x3, #0
	adr	x4, five_sec
	mov	x16, #SYS_select
	svc	#0x80

	adr	x1, alive
	adr	x2, alive_end
	sub	x2, x2, x1
	bl	write_console
	b	tick

/* write(x19, x1, x2) */
write_console:
	mov	x0, x19
	mov	x16, #SYS_write
	svc	#0x80
	ret

hang:
	b	hang

	.p2align 3
five_sec:
	.quad	5			/* tv_sec */
	.quad	0			/* tv_usec (int32 + padding) */
path:
	.asciz	"/dev/console"
hello:
	.ascii	"\n*** A04BR3 userspace: init running as PID "
hello_tail:
	.ascii	" ***\n"
alive:
	.ascii	"A04BR3 init: still alive\n"
alive_end:

stage_path:
    .asciz "/etc/a04br3-stage"

emmc_path:
    .asciz "/dev/remmc0"
emmc_ok:
    .ascii "A04BR3 userspace eMMC GPT read: PASS\n"
emmc_ok_end:
emmc_bad:
    .ascii "A04BR3 userspace eMMC GPT read: FAIL\n"
emmc_bad_end:

dynamic_path:
    .asciz "/usr/bin/a04br3-dynamic-probe"
child_result:
    .ascii "A04BR3 dynamic child wait status: 0x"
child_result_end:
hex_digits:
    .ascii "0123456789abcdef"
newline:
    .ascii "\n"
