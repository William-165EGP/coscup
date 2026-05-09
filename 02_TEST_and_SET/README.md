## Test-and-Set Lock
### Introduction
A test-and-set lock is one of the simplest ways to implement a lock.
The core idea is to use a single shared variable, usually called `lock`. All threads that want to enter the critical section repeatedly execute an atomic test-and-set operation on this variable until they successfully acquire the lock.
The value of this lock is not that it is used directly in modern kernels, but that it clearly demonstrates the fundamental idea behind synchronization: an atomic instruction can combine reading and writing into one indivisible operation.
In other words, when one CPU performs the test-and-set operation, no other CPU can interrupt it in the middle. This guarantees that only one thread can successfully acquire the lock at a time.

### Implementation
The basic implementation of a test-and-set lock is shown below:

```c

#include <stdatomic.h>

typedef atomic_int tns_lock_type;

void acquire_tns_lock(tns_lock_type *lock){
  while (atomic_exchange_explicit(lock, 1, memory_order_acquire) == 1) {
    // spinning here
  } 
}

void release_tns_lock(tns_lock_type *lock){
  atomic_store_explicit(lock, 0 , memory_order_release);
}


```

The acquire behavior is similar to a test-and-set operation. Threads that want to acquire the lock repeatedly try to set `lock` to `1`.

#### Lock Section
The `atomic_exchange_explicit` function atomically stores `1` into `lock` and returns the old value of `lock`.

The returned old value has the following meaning:
  * `0`: The lock was previously unlocked. The thread has successfully changed `lock` from `0` to `1`, so it can enter the critical section.
  * `1`: The lock was already held by another thread. The thread did not acquire the lock, so it should keep trying until it succeeds.

The `memory_order_acquire` ordering is used here to prevent memory operations inside the critical section from being reordered before the lock is acquired.

#### Unlock Section
The `atomic_store_explicit` function atomically stores `0` into `lock`. A thread that leaves the critical section releases the lock by storing `0` into `lock`.

The `memory_order_release` ordering is used here to prevent memory operations inside the critical section from being reordered after the lock is released.
