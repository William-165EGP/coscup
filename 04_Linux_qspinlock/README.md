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

### Data Structure (Global Lock Data Structure)
The global qspinlock lock data structure is encoded in a single 32-bit word. The source code of data structure can be found in `include/asm-generic/qspinlock_types.h`

<details>

<summary>data structure of qspinlock</summary>

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
Note that we only discuss little_endian and nprocs is smaller than 16K here. The size of every fields are below:
+---------+------+
| field   | size |
+---------+------+
| locked  |   8  |
+---------+------+
| pending |   8  |
+---------+------+
| tail    |  16  |
+---------+------+
The size of every field is deliberately designed as above, and it is likely to be compiled with efficient instruction.
We define the address of global lock variable is in register `rdi`, the `esi` is field `tail`, and the compiled efficient may be below:


1. `locked`: It is likely to be compiled with this `movb` instruction when unlocking the lock. Therefore, the unlock path can be efficient

```asm
movb $0, 0(%rdi)
```

2. `pending`: It is likely to be compiled with this `movb` instruction when leaving the `pending`. Therefore, the leaving can be efficient
3. `locked_pending`: It is likely to be compiled with this `movw` instruction when clearing `pending` and set `locked`. Therefore, can be efficient

```asm
mobw $1, 0(%rdi)
```

4. `tail`: It is likely to be compiled with this `xchgw` instruction when `xchg_tail`. Therefore, this operation is more efficient. The following source code and example asm (for demo only) is below

```c
// The _Q_TAIL_OFFSET is 16 as the data structure 32 - sizeof(locked) - sizeof(pending)
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

With the size limitation (32 bits) of global lock data structure, and I believe the following reason can explain that:

1. Compatible with 32-bit architecture:
Although most modern processors are 64-bit, Linux still supports many 32-bit architectures. Keeping qspinlock within a 32-bit word makes the lock state easier to manipulate with native atomic operations such as `cmpxchg`.
If the lock state requires more than 32 bits, some 32-bit architectures might not be able to update it atomically with a single instruction.
This would make the implementation more expensive and could require additional synchronization mechanisms. 
An example that only supports 32-bit `cmpxchg` is older 32-bit ARM, where Linux explicitly notes that on `arch/arm/include/asm/cmpxchg.h`

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

2. Keep the memory usage low:
Spinlocks are used to provide synchronization, so their own memory overhead should be as small as possible.
Since spinlocks are often embedded directly inside kernel data structures, increasing the size of each lock would also increase the size of every object that contains one.

This matters because there can be many lock instances in the kernel.
For example, in mm (memory management), split page table lock can place a lock at the granularity of a page-table page.
On a typical 4KB-page system, one PTE page maps 2MB of virtual memory address space, so mapping 32GB with normal 4KB pages may require about 16384 PTE pages, and therefore up to roughly 16384 PTE-level lock instances.

Below is the data structure of `ptdesc`, which can be found in `include/linux/mm_types.h`
It contains `ptl`, the page-table lock used by split page table lock.

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
