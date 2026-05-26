## Compact NUMA-aware Locks (CNA)
### Introduction
Compact NUMA-aware Locks, also known as CNA, are NUMA-aware Locks.  
The advantages can be summarized as follows:
#### 1. NUMA-local lock handoff
It tends to pass the lock to a thread running on the same NUMA node/socket, which avoids remote cache access latency and reduces cache misses.
#### 2. Compact global lock representation
It keeps each lock instance compact; the global lock state still fits in the 32-bit `qspinlock` word, while additonal CNA metadata is kept in per-CPU nodes.
#### 3. No dynamic allocation in the lock path
Lock instances do not require dynamic allocation. This is important because `kmalloc()` cannot be called in the `qspinlock`.
### Data Structure (CNA metadata)
The next plot shows the data structure in Linux kernel, which is a little bit different from userspace implementaion

<details>

<summary>Kernel CNA queue node</summary>

```text
+------+--------+------+------------------+--------------+
| next | locked | tail | socket_and_count | encoded_tail |
+------+--------+------+------------------+--------------+
```

</details>

#### Field Usage
The usage of each field is described below:
##### 1. `next`:
`next` links this CNA node to the next node in the queue.
##### 2. `locked`:
`locked` represents the local spin state of the node.
* In the original MCS spinlock, `locked` has two states:
  * `0`: the node should keep spinning
  * `1`: the node becomes the queue head and is allowed to contend for the global lock.
* In the modified CNA version, `lock` has three kinds of states:
  * `0`: the node should keep spinning
  * `1`: the node becomes the queue head and is allowed to contend for the global lock.
  * A value greater than `1`: the node is also allowed to proceed, and the value stores a pointer to the head of the secondary queue.
##### 3. `tail`:
`tail` points to the tail of secondary queue. Note that it is meaningful only if the node is in secondary queue.
##### 4. `socket_and_count`:
`socket_and_count` encodes the current NUMA/socket id together with the qnode index, also known as nesting count.
  * The qnode index is limited to 4 values, corresponding to the 4 possible locking contexts: task, softirq, hardirq, and NMI.
##### 5. `encoded_tail`:
`encoded_tail` stores the encoded form of this node as a qspinlock queue tail.
  * It encodes the CPU id and the qnode index.
  * It is used when this node is inserted into the main queue, for example through `xchg_tail()`.
### The Design of CNA
#### Basic Structure
To better show the design of CNA, the data structure is simplified and moved as below

<details>

<summary>simplified CNA node structure</summary>

```text

+------+------------------+----------------+------+
| spin | NUMA/socket id   | secondary_tail | next |
+------+------------------+----------------+------+

```

</details>

This simplified structure is similar to the kernel CNA queue node but with a few differences:
* The `spin` is the same as `locked`
* The `NUMA/socket id` is `socket_and_count` but without count
* The `secondary_tail` is the same as `tail`
#### Example Workflow
The queue is initialized with following graph, which covers most cases:

<details>

<summary>initialized queue</summary>

```text

                                                                                                                   tail
                                                                                                                     |
                                                                                                                     |
                                                                                                                     v
       t1                    t2                  t3                    t4                  t5                    t6
+---+---+---+--+     +---+---+---+--+     +---+---+---+--+     +---+---+---+--+     +---+---+---+--+     +---+---+---+--+
| 1 | 0 |   | ------>| 0 | 1 |   | ------>| 0 | 1 |   | ------>| 0 | 0 |   | ------>| 0 | 0 |   | ------>| 0 | 1 |   |  |
+---+---+---+--+     +---+---+---+--+     +---+---+---+--+     +---+---+---+--+     +---+---+---+--+     +---+---+---+--+

```

</details>

##### 1. `t1` wants to handoff the ownership
`t1` leaves the critical section and wants to handoff the ownership
  * `find_successor` finds out the next node on main queue with the same NUMA/socket id is `t4`, also finds out that nodes (`t2`, `t3`) between `t1` to `t4` (excluding `t1` and `t4`) have different NUMA/socket id; hence needs to move it to secondary queue
  * Saves the pointer of head of secondary queue head `t2` to the `spin` of next handoff `t4`   

<details>

<summary>`t1` wants to handoff the ownership</summary>

```text

                                                  tail
                                                    |
                                                    |
                                                    v
      t4                    t5                   t6
+---+---+---+--+     +---+---+---+--+     +---+---+---+--+
| | | 0 |   | ------>| 0 | 0 |   | ------>| 0 | 1 |   |  |
+-|-+---+---+--+     +---+---+---+--+     +---+---+---+--+
  |
  |
  +------------------------------------------------------------+
                                                               |
                                                               v
                                                              t2                    t3
                                                       +---+---+---+--+     +---+---+---+--+
                                                       | 0 | 1 | | | ------>| 0 | 1 |   |  |
                                                       +---+---+-|-+--+     +---+---+---+--+
                                                                 |
                                                                 |
                                                                 +---------> secondaryTail = t3

```

</details>

##### 2. `t1` wants to acquire the lock
`t1` has left the ciritical section, and wants to acquire the lock again
  * Note that the new coming node will always join the main queue. (The global lock structure `tail` points to the tail node of main queue) 

<details>

<summary>`t1` wants to acquire the lock</summary>

```text

                                                                          tail
                                                                           |
                                                                           |
                                                                           v
      t4                    t5                   t6                     t1
+---+---+---+--+     +---+---+---+--+     +---+---+---+---+     +---+---+---+--+
| | | 0 |   | ------>| 0 | 0 |   | ------>| 0 | 1 |   | ------->| 0 | 0 |   |  |
+-|-+---+---+--+     +---+---+---+--+     +---+---+---+---+     +---+---+---+--+
  |
  |
  +------------------------------------------------------------+
                                                               |
                                                               v
                                                              t2                    t3
                                                       +---+---+---+--+     +---+---+---+--+
                                                       | 0 | 1 | | | ------>| 0 | 1 |   |  |
                                                       +---+---+-|-+--+     +---+---+---+--+
                                                                 |
                                                                 |
                                                                 +---------> secondaryTail = t3

```

</details>

##### 3. `t4` wants to handoff the ownership
`t4` leaves the critical section and wants to handoff the ownership
  * `find_successor` finds out the next node on main queue with the same NUMA/socket id is `t5`, also finds out that no node needs to move to 
  * Saves the pointer of head of secondary queue head `t2` to the `spin` of next handoff `t5`   

<details>

<summary>`t4` wants to handoff the ownership</summary>

```text

                                                  tail
                                                    |
                                                    |
                                                    v
      t5                    t6                   t1
+---+---+---+--+     +---+---+---+---+     +---+---+---+--+
| | | 0 |   | ------>| 0 | 1 |   | ------->| 0 | 0 |   |  |
+-|-+---+---+--+     +---+---+---+---+     +---+---+---+--+
  |
  |
  +------------------------------------------------------------+
                                                               |
                                                               v
                                                              t2                    t3
                                                       +---+---+---+--+     +---+---+---+--+
                                                       | 0 | 1 | | | ------>| 0 | 1 |   |  |
                                                       +---+---+-|-+--+     +---+---+---+--+
                                                                 |
                                                                 |
                                                                 +---------> secondaryTail = t3

```

</details>

##### 4. `t7` wants to acquire the lock
A new node `t7` wants to acquire the lock
 
<details>

<summary>`t7` wants to acquire the lock</summary>

```text

                                                                           tail
                                                                             |
                                                                             |
                                                                             v
      t5                    t6                   t1                      t7
+---+---+---+--+     +---+---+---+---+     +---+---+---+---+     +---+---+---+--+
| | | 0 |   | ------>| 0 | 1 |   | ------->| 0 | 0 |   | ------->| 0 | 1 |   |  |
+-|-+---+---+--+     +---+---+---+---+     +---+---+---+---+     +---+---+---+--+
  |
  |
  +------------------------------------------------------------+
                                                               |
                                                               v
                                                              t2                    t3
                                                       +---+---+---+--+     +---+---+---+--+
                                                       | 0 | 1 | | | ------>| 0 | 1 |   |  |
                                                       +---+---+-|-+--+     +---+---+---+--+
                                                                 |
                                                                 |
                                                                 +---------> secondaryTail = t3

```

</details>

##### 5. `t5` wants to handoff the ownership
`t5` leaves the critical section and wants to handoff the ownership
  * `find_successor` finds out the next node on main queue with the same NUMA/socket id is `t1`, also finds out that node (`t6`) between `t5` to `t1` (excluding `t5` and `t1`) have different NUMA/socket id; hence needs to move it to secondary queue
  * Saves the pointer of head of secondary queue head `t2` to the `spin` of next handoff `t1`
  * We don't need to traverse the secondary queue every time moving some nodes to secondary queue, because secondary tail can help us to point to that

<details>

<summary>`t5` wants to handoff the ownership</summary>

```text

                              tail
                                |
                                |
                                v
     t1                      t7
+---+---+---+---+     +---+---+---+--+
| | | 0 |   | ------->| 0 | 1 |   |  |
+-|-+---+---+---+     +---+---+---+--+
  |
  |
  +------------------------------------------------------------+
                                                               |
                                                               v
                                                              t2                    t3                   t6
                                                       +---+---+---+--+     +---+---+---+--+     +---+---+---+--+
                                                       | 0 | 1 | | | ------>| 0 | 1 |   | ------>| 0 | 1 |   |  |
                                                       +---+---+-|-+--+     +---+---+---+--+     +---+---+---+--+
                                                                 |
                                                                 |
                                                                 +-------------------------------> secondaryTail = t6

```

</details>

##### 6. `t1` wants to handoff the ownership
Now `t1` leaves the critical section and wants to handoff the ownership
  * `find_successor` cannot find out the next node on main queue with the same NUMA/socket id.
  * Moves all of the node (`t7`) next to `t1` to secondary queue tail.
  * Pass the lock to secondary queue head (store `1` to the `spin` of secondary queue head node)
  * The secondary becomes the new main queue 

<details>

<summary>`t1` wants to handoff the ownership</summary>

```text

                                                                        tail
                                                                         |
                                                                         |
                                                                         v
        t2                   t3                   t6                   t7
+---+---+---+--+     +---+---+---+--+     +---+---+---+--+     +---+---+---+--+
| 0 | 1 | | | ------>| 0 | 1 |   | ------>| 0 | 1 |   | ------>| 0 | 1 |   |  |
+---+---+-|-+--+     +---+---+---+--+     +---+---+---+--+     +---+---+---+--+
          |
          |
          +-------------------------------> secondaryTail = t6 (useless in main queue)

```

</details>

### The Comments from Reviewers
#### Comments from LKML (Linux Kernel Mailing List)
##### 1. Conflict with RT-kernel Requirements
The unfairness aspect may conflict with RT-kernel requirements for deterministic raw spinlocks.

The [comment](https://lore.kernel.org/all/20210922192528.ob22pu54oeqsoeno@offworld/) from Davidlohr Bueso

```text
>+	default y
>+	help
>+	  Introduce NUMA (Non Uniform Memory Access) awareness into
>+	  the slow path of spinlocks.
>+
>+	  In this variant of qspinlock, the kernel will try to keep the lock
>+	  on the same node, thus reducing the number of remote cache misses,
>+	  while trading some of the short term fairness for better performance.
>+
>+	  Say N if you want absolute first come first serve fairness.

This would also need a depends on !PREEMPT_RT, no? Raw spinlocks really want
the determinism.
```

The [comment](https://lore.kernel.org/all/20190131100009.GB31534@hirez.programming.kicks-ass.net/) from Peter Zijlstra

```text
> Choose the next lock holder among spinning threads running on the same
> socket with high probability rather than always. With small probability,
> hand the lock to the first thread in the secondary queue or, if that
> queue is empty, to the immediate successor of the current lock holder
> in the main queue.  Thus, assuming no failures while threads hold the
> lock, every thread would be able to acquire the lock after a bounded
> number of lock transitions, with high probability.
> 
> Note that we could make the inter-socket transition deterministic,
> by sticking a counter of intra-socket transitions in the head node
> of the secondary queue. At the handoff time, we could increment
> the counter and check if it is below a threshold. This adds another
> field to queue nodes and nearly-certain local cache miss to read and
> update this counter during the handoff. While still beating stock,
> this variant adds certain overhead over the probabilistic variant.

(also heavily suffers from the socket == node confusion)

How would you suggest RT 'tunes' this?

RT relies on FIFO fairness of the basic spinlock primitives; you just
completely wrecked that.   
```

Those comments suggest that CNA may sacrifice FIFO fairness, which is unacceptable for RT kernel
RT kernel requires *determinism* so that user can reason about worst-case latency
##### 2. Distance Differences
2. CNA ignores distance differences between NUMA nodes

The [comment](https://lore.kernel.org/all/20210930094447.9719-1-21cnbao@gmail.com/) from Barry Song

```text

do we need to consider the distances of numa nodes in the secondary
queue? does it still make sense to treat everyone else equal in
secondary queue?

Thanks
barry

```

Using NUMA-node-based classification is too coarse. We should also consider the CPU-to-CPU locality, such as LLC sharing, chiplet topology, or inter-node distance
#### Comments Summary
These comments show the concerns from the Linux kernel community, but I believe they mainly indicate that CNA should be applied selectively rather than rejected entirely.

CNA may not be suitable for RT kernels because it relaxes FIFO fairness, and its NUMA-node-based policy may be too coarse for modern CPU topologies.

Therefore, CNA is more appropriate as a throughput-oriented optimization for non-RT kernels, with possible future improvements using finer-grained topology information.

Moreover, RON's design naturally addresses both concerns. The details of RON will be discussed in the next session.
