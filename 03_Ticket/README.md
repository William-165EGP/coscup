## Ticket Lock
### Introduction
In a ticket lock, every thread that wants to enter the critical section first draws a ticket number. When the serving number equals the thread's ticket number, the thread can enter the critical section.
This method follows a first-come, first-served policy, which ensures strict fairness.

The core idea is to use two shared atomic variables `next_ticket` and `now_serving`, and one local variable, `my_ticket`.

### Implementation 
The basic implementation of a ticket lock is shown below:

```c

#include <stdatomic.h>

typedef struct {
  atomic_uint next_ticket;
  atomic_uint now_serving;
} ticket_lock_type;

void ticket_lock_init(ticket_lock_type *ticket_lock) {
  atomic_init(&ticket_lock->next_ticket, 0);
  atomic_init(&ticket_lock->now_serving, 0);
}

void acquire_ticket_lock(ticket_lock_type *ticket_lock) {
  unsigned int my_ticket = atomic_fetch_add_explicit(&ticket_lock->next_ticket, 1, memory_order_relaxed);

  while (atomic_load_explicit(&ticket_lock->now_serving, memory_order_acquire) != my_ticket) {
    // spinning here
  }
}

void release_ticket_lock(ticket_lock_type *ticket_lock) {
  atomic_fetch_add_explicit(&ticket_lock->now_serving, 1, memory_order_release);
  /*
  * Equivalent in a correct ticket-lock usage, since only the lock owner
  * should update now_serving during unlock.
  *
  * unsigned int next_serving = atomic_load_explicit(&ticket_lock->now_serving, memory_order_relaxed) + 1;
  * 
  * atomic_store_explicit(&ticket_lock->now_serving, next_serving, memory_order_release);
  *
  */
  
}

```
#### Lock Section
The function `atomic_fetch_add_explicit` atomically increments the number of `next_ticket` by `1` and returns its previous value.

This previous value is stored in `my_ticket` and represents the ticket number assigned to the current thread.

The basic pseudocode can be written as follows:

```c

my_ticket = next_ticket++;

```

The thread repeatedly loads `now_serving` until its value equals `my_ticket`.
Therefore, a thread uses the function `atomic_load_explicit` in a while loop until `now_serving` reaches its ticket number.

Once `now_serving` equals `my_ticket`, that thread can enter the critical section.

The memory order `memory_order_acquire` is used here to prevent memory operations inside the critical section being reordered before the lock is acquired. 

#### Unlock Section
The function `atomic_fetch_add_explicit` increments the number of `now_serving` by `1`.
This advances the serving number, so that the thread with the next ticket number can enter the critical section.

The memory order `memory_order_release` is used here to prevent memory operations inside the critical section being reordered after the lock is released. 

### Advantages

#### 1. Fairness
A ticket lock provides strict first-come, first-served fairness.

Each thread receives a unique ticket number when it tries to acquire the lock, and threads enter the critical section in increasing ticket order.
Therefore, no thread can bypass another thread that arrived earlier.

As a result, a ticket lock not only ensures fairness but also provides bounded waiting.

### Disadvantages

#### 1. Cache Coherence Traffic
A ticket lock reduces unnecessary write operations while spinning because waiting threads repeatedly read the shared variable `now_serving`.

However, all waiting threads still spin on the same shared variable. When the lock owner releases the lock, it increments `now_serving`, which updates the cache line containing that variable. Since this cache line is shared by all waiting CPUs, the update may invalidate or update the cached copies in many CPUs.

Under high contention, this causes significant cache coherence traffic. As a result, the system may spend considerable time propagating updates to `now_serving` instead of doing useful work.

This issue can be further understood through cache coherence protocols such as MESI, which describe how cache lines are shared, invalidated, and transferred between CPUs. When `now_serving` is updated, the cache coherence protocol must propagate the new value or invalidate stale cached copies in other CPUs. This extra propagation and invalidation overhead contributes to cache coherence traffic.

### Additional Information
For readers who are interested in the MESI protocol, more details can be found here: https://en.wikipedia.org/wiki/MESI_protocol
