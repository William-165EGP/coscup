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
This previous value is stored in `my_ticket`, and represents the ticket number assigned to the current thread.
The basic pseudo code can be written as below:

```c

my_ticket = next_ticket++;

```

The thread repeatedly loads `now_serving` until its value equals `my_ticket`.
Therefore, a thread uses function `atomic_load_explicit` in a while loop until `now_serving` reaches its ticket number.
Once `now_serving` equals to `my_ticket`, that thread can enter the critical section.

The memory order `memory_order_acquire` is used here to prevent memory operations inside the critical section being reordered before the lock is acquired. 

#### Unlock Section
The function `atomic_fetch_add_explicit` increments the number of `now_serving` by `1`.
This advances the serving number, so that the thread with the next ticket number can enter the critical section.

The memory order `memory_order_release` is used here to prevent memory operations inside the critical section being reordered after the lock is released. 
