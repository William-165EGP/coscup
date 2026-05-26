## Linux qspinlock
### Introduction
Linux kernel spinlocks have evolved several times. From simple Test-and-Set locks, to ticket locks, and eventually to the current qspinlock design.
The qspinlock is a compact variant of the MCS lock. Its design tries to balance the advantages of different designs:

1. **Test-and-Set:**
  This method is simple and requires only a small number of operations to acquire the lock.
  Therefore, it works well under low contention.
  Since most spinlock acquisitions happen under low contention, a spinlock should be acquired as quickly as possible in the uncontended case.
2. **Ticket Lock:**
  The core idea of a ticket lock is fairness. Each arriving CPU obtains a ticket number and waits until its ticket becomes the owner ticket. This guarantees FIFO ordering, so CPUs acquire the lock in the same order in which they arrive.
  qspinlock borrows this fairness idea in its contended path. Waiting CPUs are ordered so that older waiters are generally served before newly arriving CPUs. This prevents new arrivals from repeatedly bypassing existing waiters and provides a fairer acquisition order.
3. **MCS Lock:**
  An MCS lock aims to balance fairness and cache-coherence traffic. Similar to ticket locks, it provides queue-based ordering for waiting CPUs. However, instead of letting all CPUs spin on the same global variable, MCS organizes waiters into a linked-list queue.
  Each waiting CPU owns a local MCS node and spins on a flag field inside its own node.
  When the current lock holder releases the lock, it directly notifies its own successor by updating the successor's flag.
  As a result, spinning is distributed across per-node variables rather than concentrated on a single shared cache line, which reduces cache-line bouncing under contention.
  qspinlock borrows this queue-based idea for its contended path, where waiting CPUs are organized in an MCS-like queue. 

In short, qspinlock keeps a fast path for the common uncontended case, while using an MCS-like queue to provide better fairness and scalability under contention.

### Global qspinlock Structure
The global qspinlock is represented as a single 32-bit word. Its definition can be found in `include/asm-generic/qspinlock_types.h`

<details>

<summary>Definition of qspinlock</summary>

```c

typedef struct qspinlock {
	union {
		atomic_t val;

		/*
		 * By using the whole 2nd least significant byte for the
		 * pending bit, we can allow better optimization of the lock
		 * acquisition for the pending bit holder.
		 */
#ifdef __LITTLE_ENDIAN
		struct {
			u8	locked;
			u8	pending;
		};
		struct {
			u16	locked_pending;
			u16	tail;
		};
#else
		struct {
			u16	tail;
			u16	locked_pending;
		};
		struct {
			u8	reserved[2];
			u8	pending;
			u8	locked;
		};
#endif
	};
} arch_spinlock_t;

```

</details>
Here, we only consider the little-endian case where nprocs is less than 16K. The sizes of the fields are shown below:

```text
+---------+------+
| field   | bits |
+---------+------+
| locked  |   8  |
+---------+------+
| pending |   8  |
+---------+------+
| tail    |  16  |
+---------+------+
```

These field sizes are deliberately chosen so that some operations can be compiled into efficient instructions.

Assume that the address of the global lock variable is stored in `%rdi`, and the new `tail` value is stored in `%esi`.
With this layout, the compiler may generate efficient byte- or word-sized instructions for several common operarions:

1. `locked`: When releasing the lock, the compiler can clear only the `locked` byte using a single `movb` instruction.
This makes the unlock path efficient.

```asm
movb $0, 0(%rdi)
```

2. `pending`: Similarly, when clearing the `pending` byte, the compiler can use a single byte store.

```asm
movb $0, 1(%rdi)
```

3. `locked_pending`: Since `locked` and `pending` occupy the lower 16 bits, the compiler can update them together with a single 16-bit store.
For example, it can clear `pending` and set `locked` at the same time:

```asm
movw $1, 0(%rdi)
```

This writes `locked = 1` and `pending = 0` in one instruction, and the field `locked_pending` is used here. 

4. `tail`: In `xchg_tail()`, the compiler can use a 16-bit `xchgw` instruction to exchange only the `tail` field. This avoids touching the lower 16 bits, which contain `locked` and `pending`.
The following source code and example assembly illustrate this optimization. The exact generated assembly may vary by compiler and configuration:

```c
// The _Q_TAIL_OFFSET is 16 because `locked` and `pending` occupy the lower 16 bits.
static __always_inline u32 xchg_tail(struct qspinlock *lock, u32 tail)
{
	return (u32)xchg_relaxed(&lock->tail,
				 tail >> _Q_TAIL_OFFSET) << _Q_TAIL_OFFSET;
}
```

```asm
shrl $16, %esi     # tail >> _Q_TAIL_OFFSET
xchgw %si, 2(%rdi) # exchange u16 with lock->tail
movzwl %si, %eax   # old tail value
shll $16, %eax     # return old_tail << _Q_TAIL_OFFSET
ret
```

These examples show why the fields in the global lock are arranged in this way.
By placing `locked` and `pending` in the lower 16 bits and `tail` in the upper 16 bits, several common operations can be implemented with byte- or word-sized instructions, without unnecessarily modifying neighboring fields.

The 32-bit size of the global qspinlock is also a deliberate design choice.
There are two main reasons for keeping the lock state within a single 32-bit word:

1. Compatibility with 32-bit architectures:
Although most modern processors are 64-bit, Linux still supports many 32-bit architectures. Keeping qspinlock within a 32-bit word makes the lock state easier to manipulate with native atomic operations such as `cmpxchg`.
If the lock state required more than 32 bits, some 32-bit architectures might not be able to update it atomically with a single native instruction.
This would make the implementation more expensive and could require additional synchronization mechanisms. 
Older 32-bit ARM provides a good example of this constraint.
In `arch/arm/include/asm/cmpxchg.h`, Linux explicitly notes that `cmpxchg` only supports 32-bit operands on ARMv6:

<details>

<summary>ARMv6 cmpxchg limitation in Linux</summary>
  
```c

/*
 * cmpxchg only support 32-bits operands on ARMv6.
 */

static inline unsigned long __cmpxchg(volatile void *ptr, unsigned long old,
				      unsigned long new, int size)
{
	unsigned long oldval, res;

	prefetchw((const void *)ptr);

	switch (size) {
#ifdef CONFIG_CPU_V6	/* ARCH == ARMv6 */
	case 1:
		oldval = cmpxchg_emu_u8((volatile u8 *)ptr, old, new);
		break;
#else /* min ARCH > ARMv6 */
	case 1:
		do {
			asm volatile("@ __cmpxchg1\n"
			"	ldrexb	%1, [%2]\n"
			"	mov	%0, #0\n"
			"	teq	%1, %3\n"
			"	strexbeq %0, %4, [%2]\n"
				: "=&r" (res), "=&r" (oldval)
				: "r" (ptr), "Ir" (old), "r" (new)
				: "memory", "cc");
		} while (res);
		break;
	case 2:
		do {
			asm volatile("@ __cmpxchg1\n"
			"	ldrexh	%1, [%2]\n"
			"	mov	%0, #0\n"
			"	teq	%1, %3\n"
			"	strexheq %0, %4, [%2]\n"
				: "=&r" (res), "=&r" (oldval)
				: "r" (ptr), "Ir" (old), "r" (new)
				: "memory", "cc");
		} while (res);
		break;
#endif
	case 4:
		do {
			asm volatile("@ __cmpxchg4\n"
			"	ldrex	%1, [%2]\n"
			"	mov	%0, #0\n"
			"	teq	%1, %3\n"
			"	strexeq %0, %4, [%2]\n"
				: "=&r" (res), "=&r" (oldval)
				: "r" (ptr), "Ir" (old), "r" (new)
				: "memory", "cc");
		} while (res);
		break;
	default:
		__bad_cmpxchg(ptr, size);
		oldval = 0;
	}

	return oldval;
}


  ```
</details>

2. Keeping memory overhead low:
Spinlocks are synchronization primitives, and they are often embedded directly inside kernel data structures.
Therefore, their own memory overhead must be kept as small as possible.
This matters because there can be many lock instances in the kernel.
For example, in the memory-management subsystem, split page table lock can place locks at the granularity of page-table pages.
On a typical 4KB-page system, one PTE page maps 2MB of virtual memory address space. Therefore, mapping 32GB of virtual memory with normal 4KB pages may require up to about 16384 PTE pages, and thus up to roughly 16384 PTE-level lock instances, depending on how the page tables are populated and configured.
Below is the data structure of `ptdesc`, which can be found in `include/linux/mm_types.h`
It contains `ptl`, which is either an embedded `spinlock_t` or a pointer to one, depending on the configuration.
Keeping `arch_spinlock_t` compact reduces memory overhead and also helps to reduce cache footprint.
This is especially important for small-memory systems and embedded systems, where both RAM capacity and cache capacity are limited.

<details>

<summary>ptdesc structure</summary>

```c

struct ptdesc {
	memdesc_flags_t pt_flags;

	union {
		struct rcu_head pt_rcu_head;
		struct list_head pt_list;
		struct {
			unsigned long _pt_pad_1;
			pgtable_t pmd_huge_pte;
		};
	};
	unsigned long __page_mapping;

	union {
		pgoff_t pt_index;
		struct mm_struct *pt_mm;
		atomic_t pt_frag_refcount;
#ifdef CONFIG_HUGETLB_PMD_PAGE_TABLE_SHARING
		atomic_t pt_share_count;
#endif
	};

	union {
		unsigned long _pt_pad_2;
#if ALLOC_SPLIT_PTLOCKS
		spinlock_t *ptl;
#else
		spinlock_t ptl;
#endif
	};
	unsigned int __page_type;
	atomic_t __page_refcount;
#ifdef CONFIG_MEMCG
	unsigned long pt_memcg_data;
#endif
};

```

</details>

Therefore, the 32-bit qspinlock design reflects considerations across both architecture support and subsystem-level momory overhead.

For clarity, we can abstract the global qspinlock layout as follows.
Note that this diagram shows the lower bits on the left and the higher bits on the right, which is the opposite of the convention used in the original qspinlock comment.
This presentation is chosen because the `tail` field will later be used to explain MCS node enqueueing.
The original comment can be found in `kernel/locking/qspinlock.c` 

```text
    +--------+--------+----------------+
    | locked |pending |      tail      |
    +--------+--------+----------------+
    ^                                  ^
    |                                  |
    |                                  |
lower bit                          higher bit
```
