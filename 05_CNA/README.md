## Compact NUMA-aware Locks (CNA)
### Introduction
Compact NUMA-aware Locks, also known as CNA, are NUMA-aware Locks.  
The advantages can be summarized as follows:
1. It tend to pass the lock to a thread running on the same NUMA node/socket, which avoids remote cache access latency and reduces cache misses.
2. It keeps each lock instance compact; the global lock state still fits in the 32-bit `qspinlock` word, while additonal CNA metadata is kept in per-CPU nodes.
3. Lock instances do not require dynamic allocation. This is important because `kmalloc()` cannot be called in the `qspinlock`.
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

<details>

<summary>simplified CNA node structure</summary>

```text

+------+------------------+----------------+------+
| spin | NUMA/socket id   | secondary_tail | next |
+------+------------------+----------------+------+

```

</details>

This simplified structure is similar to the kernel CNA queue node but with a few differences:
1. The `spin` is the same as `locked`
2. The `NUMA/socket id` is `socket_and_count` but without count
3. The `secondary_tail` is the same as `tail`

We defined the address as `a` and initialized queue as following graph:

<details>

<summary>initialized queue</summary>

```text

                                                                                              tail
                                                                                               |
                                                                                               |
                                                                                               v
       t1                    t2                  t3                    t4                  t5                    t6
+---+---+---+--+     +---+---+---+--+     +---+---+---+--+     +---+---+---+--+     +---+---+---+--+     +---+---+---+--+
| 1 | 0 |   | ------>| 0 | 1 |   | ------>| 0 | 1 |   | ------>| 0 | 0 |   | ------>| 0 | 0 |   | ------>| 0 | 1 |   |   |
+---+---+---+--+     +---+---+---+--+     +---+---+---+--+     +---+---+---+--+     +---+---+---+--+     +---+---+---+--+

```

</details>

1. Now `t1` leaves the critical section and wants to handoff the ownership
   * `find_successor` finds out the next node on main queue with the same NUMA/socket id is `t4`, also finds out that nodes (`t2`, `t3`) between `t1` to `t4` (excluding `t1` and `t4`) have different NUMA/socket id; hence needs to move it to secondary queue
   * Saves the pointer of head of secondary queue head `t2` to the `spin` of next handoff `t4`   

<details>

<summary>first step</summary>

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
                                                       | 0 | 1 | | | ------>| 0 | 1 |   |   |
                                                       +---+---+-|-+--+     +---+---+---+--+
                                                                 |
                                                                 |
                                                                 +---------> secondaryTail = t3

```

</details>

2. `t1` has left the ciritical section, and wants to acquire the lock again
   * Note that the new coming node will always join the main queue. (The global lock structure `tail` points to the tail node of main queue) 

<details>

<summary>second step</summary>

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
                                                       | 0 | 1 | | | ------>| 0 | 1 |   |   |
                                                       +---+---+-|-+--+     +---+---+---+--+
                                                                 |
                                                                 |
                                                                 +---------> secondaryTail = t3

```

</details>

3. Now `t4` leaves the critical section and wants to handoff the ownership
   * `find_successor` finds out the next node on main queue with the same NUMA/socket id is `t5`, also finds out that no node needs to move to 
   * Saves the pointer of head of secondary queue head `t2` to the `spin` of next handoff `t5`   

<details>

<summary>third step</summary>

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
                                                       | 0 | 1 | | | ------>| 0 | 1 |   |   |
                                                       +---+---+-|-+--+     +---+---+---+--+
                                                                 |
                                                                 |
                                                                 +---------> secondaryTail = t3

```

</details>


4. A new node `t7` wants to acquire the lock
 
<details>

<summary>fourth step</summary>

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
                                                       | 0 | 1 | | | ------>| 0 | 1 |   |   |
                                                       +---+---+-|-+--+     +---+---+---+--+
                                                                 |
                                                                 |
                                                                 +---------> secondaryTail = t3

```

</details>


5. Now `t5` leaves the critical section and wants to handoff the ownership
   * `find_successor` finds out the next node on main queue with the same NUMA/socket id is `t1`, also finds out that node (`t6`) between `t5` to `t1` (excluding `t5` and `t1`) have different NUMA/socket id; hence needs to move it to secondary queue
   * Saves the pointer of head of secondary queue head `t2` to the `spin` of next handoff `t1`   

<details>

<summary>fifth step</summary>

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
                                                       | 0 | 1 | | | ------>| 0 | 1 |   |   |
                                                       +---+---+-|-+--+     +---+---+---+--+
                                                                 |
                                                                 |
                                                                 +---------> secondaryTail = t3

```

</details>
