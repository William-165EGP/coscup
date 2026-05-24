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

