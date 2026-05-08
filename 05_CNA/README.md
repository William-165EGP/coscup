## Compact NUMA-aware Locks (CNA)
### Introduction
Compact NUMA-aware Locks, also known as CNA, are NUMA-aware Locks. Their advantages can be summarized as follows:
1. It tend to pass the lock to a thread running on the same NUMA node/socket, which avoids remote cache access latency and reduces cache misses.
2. It keeps each lock instance compact; the global lock state still fits in the 32-bit `qspinlock` word, while additonal CNA metadata is kept in per-CPU nodes.
3. Lock instances do not require dynamic allocation. This is important because `kmalloc()` cannot be called in the `qspinlock`.
### Data Structure (CNA metadata)
The next plot shows the data structure in Linux kernel, which is a little bit different from userspace implementaion

Kernel CNA queue node:

   +------+------------------+------+------------------+--------------+
   | next | locked           | tail | socket_and_count | encoded_tail |
   +------+------------------+------+------------------+--------------+

The usage of each member is described below:
1. `next` links this CNA node to the next node in the queue.
2. `locked` represents the local spin state of the node.
   * In the original MCS spinlock, `locked` has two states:
      * `0`: the node should keep spinning
      * `1`: the node becomes the queue head and is allowed to contend for the global lock.
   * In the modified CNA version, `lock` has three kinds of states:
      * `0`: the node should keep spinning
      * `1`: the node becomes the queue head and is allowed to contend for the global lock.
      * A value greater than `1`: the node is also allowed to proceed, and the value stores a pointer to the head of the secondary queue.
3. `tail` points to the tail of secondary queue. Note that it is meaningful only if the node is in secondary queue.
4. `socket_and_count` encodes the current NUMA/socket id together with the qnode index, also known as nesting count.
      * The qnode index is limited to 4 values, corresponding to the 4 possible locking contexts: task, softirq, hardirq, and NMI.
5. `encoded_tail` stores the encoded form of this node as a qspinlock queue tail.
   * It encodes the CPU id and the qnode index.
   * It is used when this node is inserted into the main queue, for example through `xchg_tail()`.
### The Design of CNA
To better show the design of CNA, the data structure is simplified and moved as below

   +------+------------------+----------------+------+
   | spin | NUMA/socket id   | secondary_tail | next |
   +------+------------------+----------------+------+

This simplified structure is similar to the kernel CNA queue node but with a few differences:
1. The `spin` is the same as `locked`
2. The `NUMA/socket id` is `socket_and_count` but without count
3. The `secondary_tail` is the same as `tail`

We defined the address as `a` and initialized queue as following graph:
   
           t1                   t2                   t3                   t4                   t5                   t6
   +---+---+---+--+     +---+---+---+--+     +---+---+---+--+     +---+---+---+--+     +---+---+---+--+     +---+---+---+--+
   | 1 | 0 |   | ------>| 0 | 1 |   | ------>| 0 | 1 |   | ------>| 0 | 0 |   | ------>| 0 | 0 |   | ------>| 0 | 1 |   |   |
   +---+---+---+--+     +---+---+---+--+     +---+---+---+--+     +---+---+---+--+     +---+---+---+--+     +---+---+---+--+

1. Now `t1` wants to handoff the ownership
   * `find_successor` finds out the nodes between `t1` to `t4` (excluding `t1` and `t4`) have different NUMA/socket id; hence needs to move it to secondary queue
   * Saves the pointer of head of secondary queue head `t2` to the next handoff `t4`   

            t4                    t5                    t6
      +---+---+---+--+     +---+---+---+--+     +---+---+---+--+
      |   | 0 |   | ------>| 0 | 0 |   | ------>| 0 | 1 |   |   |
      +---+---+---+--+     +---+---+---+--+     +---+---+---+--+
        |
        |
        +--------------------------------------------------------------+
                                                                       |
                                                                       v
                                                                  t2                    t3
                                                            +---+---+---+--+     +---+---+---+--+
                                                            | 0 | 1 |   | ------>| 0 | 1 |   |   |
                                                            +---+---+---+--+     +---+---+---+--+
                                                                      |
                                                                      |
                                                                      +---------> secondaryTail = t3
