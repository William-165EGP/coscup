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

typedef atomic_int tas_lock_type;

void acquire_tas_lock(tas_lock_type *lock){
  while (atomic_exchange_explicit(lock, 1, memory_order_acquire) == 1) {
    // spinning here
  } 
}

void release_tas_lock(tas_lock_type *lock){
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

### Disadvantages
As modern systems develop, machines contain more and more cores. Therefore, the test-and-set lock does not scale well for the following reasons:

#### 1. Cache Coherence Traffic
One major disadvantage of a test-and-set lock is that it causes heavy cache coherence traffic.
In the lock section, every waiting thread repeatedly executes `atomic_exchange_explicit`. This operation is a write operation, so even if the value of `lock` is already `1`, the CPU still tries to write `1` to the same shared variable.
Because the lock variable is stored in a cache line shared by multiple CPUs, each atomic exchange may invalidate the cache line in other CPUs' caches. When many threads are spinning on the same lock, the cache line containing `lock` keeps moving between CPUs.

Although we can replace `atomic_exchange_explicit` with `atomic_compare_exchange_explicit` to reduce unnecessary writes, the coherence and propagation overhead can still be heavy under contention.
As a result, the system spends a lot of time maintaining cache coherence instead of doing useful work. This makes the test-and-set lock perform poorly under high contention.

#### 2. Unfairness
A test-and-set lock does not provide fairness.

When the lock is released, all waiting threads compete for the same lock variable at the same time. Since there is no queueing mechanism, the lock does not guarantee that the longest-waiting thread will acquire the lock first.
This unfairness can be affected by cache coherence behavior. When one CPU releases the lock, the update to the cache line must be propagated to other CPUs. Because this propagation takes time, different CPUs may observe the released lock at slightly different times.

In addition, CPUs that are closer in the cache coherence topology, or CPUs that can obtain ownership of the cache line earlier, may have a higher chance of acquiring the lock first. Therefore, some threads may repeatedly acquire the lock while others keep waiting.
In the worst case, this unfairness may lead to starvation.

### Advantages
Although a basic test-and-set lock has several scalability problems, it still has some important advantages.

#### 1. Efficient Fast Path

A test-and-set lock is efficient when there is no contention. If the lock is free, a thread can acquire it with a single atomic operation.
This idea is still used in more advanced lock implementations. Although modern locks often have more complex slow paths to handle contention, their fast paths usually still try to acquire the lock immediately using a simple atomic operation.
Therefore, the value of a test-and-set lock is not only its simplicity. Many practical locks still use the same idea in their fast path: first try to acquire the lock with a simple atomic operation, and only enter the slow path when contention occurs.
