## RON
### Introduction
Routing on Network-on-Chip, also known as RON, is a spinlock design aims to address the communication overhead caused by coherence on modern multicore systems.

As discussed in the previous chapters, when a lock is handed over from one core to another, the lock variable and the protected shared data may need to be transferred through the cache coherence protocol.

If the two cores have a long core-to-core latency, this propogation overhead can become significant, especially under high lock contention.

To reduce this cost, RON used a precomputed routing order based on measured core-to-core latency.
Instead of granting the lock to waiting threads in FIFO order, RON transfers the lock ownership according to a one-way circular route.

The route is designed to reduce the total handover cost between cores while still ensuring bounded waiting.

### Generate Core Latency
To generate the optimised routing table, we can divide the process into two steps:

1. Measure core-to-core latency:

   First, we measure the communication latency between every pair of cores.
   This can be done using a core-to-core latency benchmark, a compare-and-swap based benchmark, or a message-passing benchmark between two cores.

   The result is a latency matrix, where each entry represents the communication cost from one core to another.

2. Generate the Traveling Salesman Problem order:

   After obtaining the core latency matrix, we can model the cores as cities and the core-to-core latencies as distance between cities.
   The problem then becomes similar to the Traveling Salesman Problem.

   The Traveling Salesman Problem asks the following question: given the distance between each pair of cities, what is the shortest possible route that visits every city exactly once and returns to the starting city?

   In RON, this route is used as the lock handover order. We can either write our own program or use an existing solver such as Google Or-Tools to compute the TSP order.

With these two steps, RON obtaines a precomputed TSP order.

During lock release, the lock holder scans the waiting cores according to this circular order and hands the lock to the next waiting core.

By following this order, RON reduces unnecessary long-distance cacheline transfers and improves lock performance under high contention.

### The Design of RON
#### Basic Structure
The RON implementation uses the following data structures and variables:

1. `plock`

   `plock` is the type of each slot in `arrayLock`.
   Each slot contains two atomic fields: `numWait` and `lock` 

   1. `numWait`:
   
      `numWait` records the number of threads associated with this CPU slot that are currently waiting for the lock.

      * `0`: No thread is waiting on this CPU slot.
      * Greater than `0`: One or more threads are waiting on or using this CPU slot.

      A counter is required because multiple threads may be assigned to the same CPU under oversubscription.
      
   2. `lock`:

      `lock` represents the handover state of this CPU slot.
       
      * `1` (MUST_WAIT): Threads associated with this slot must continue waiting.
      * `0` (HAS_LOCK): The lock has been handed over to this slot.

      When `lock` becomes `0`, the waiting threads complete to atomically change it from `0` back to `1`.
      Only one thread can succeed and enter the critical section.

2. `arrayLock`:

   `arrayLock` is a per-lock array of `plock` slots.
   Its size is equal to `CPU_NUMBER`, so each TSP position has one corresponding slot.

   The indices of `arrayLock` follow the precomputed TSP order rather than the CPU numbering.
   A thread accesses its slot using its TSP position:

   ```c
   impl->arrayLock[order]
   ```

   During unlocking, the current lock holder scans `arrayLock` circularly from the next TSP position and searches for the next slot whose `numWait` is greater than `0`.
   

3. `inUse`:

   `inUse` is a per-lock atomic bool indicating whether the critical section currently has an owner or whether lock ownership is being handed over between threads.

   * `false`: The lock is idle, so a thread may acquire it by atomically changing `inUse` from `false` to `true`.
   * `true`: The lock is currently held or is being transferred to another waiting slot.
   
   If the unlock operation cannot find another waiting slot, it sets `inUse` to `false`.
   Therefore, `inUse` provides the initial acquisition and fallback path, while `arrayLock` provides the normal RON handover path.

4. `routing`:

   `routing` stores the precomputed TSP route. It maps a TSP position to the CPU ID.

   ```text
   TSP position -> CPU ID
   ```

   For example, if:

   ```c
   routing[0] = 3;
   ```

   then CPU 3 is the first CPU in the TSP route.

5. `cpu_order`:

   `cpu_order` is the inverse mapping of `routing`. It maps a CPU ID to its position in the TSP route:

   ```text
   CPU ID -> TSP position
   ```

   It is initialised as follows:

   ```c
   for (int position = 0; position < CPU_NUMBER; position++)
      cpu_order[routing[position]] = position;
   ```

   After a thread is pinned to a CPU, it obtains its TSP position using:

   ```c
   order = cpu_order[cpu];
   ```

   The thread then uses `order` to access its corresponding slot in `arrayLock`.
#### Example Setup
To better demonstrate how the RON algorithm works, we first define a simple system configuration:

1. Number of CPUs:

   Assume that the system contains four CPUs:

   ```c
   #define CPU_NUMBER 4
   ```

2. Routing array

   The precomputed TSP route is represented by the following `routing` array:

   ```c
   static int routing[CPU_NUMBER] = {0, 2, 3, 1};
   ```

3. Constructing the `cpu_order` array

   Since a thread initially knows its CPU ID rather than its TSP position, RON constructs the inverse mapping, called `cpu_order`.

   ```c
   for (int position = 0; position < CPU_NUMBER; position++)
      cpu_order[routing[position]] = position;
   ```

   Based on the routing array above, the assignments are:

   ```c
   cpu_order[0] = 0;
   cpu_order[2] = 1;
   cpu_order[3] = 2;
   cpu_order[1] = 3;
   ```

   Therefore, the resulting `cpu_order` array is:

   ```c
   static int cpu_order[CPU_NUMBER] = {0, 3, 1, 2};
   ```

   The two arrays provide inverse mapping:

   ```text
   routing:   TSP position -> CPU ID
   cpu_order: CPU ID -> TSP position
   ```
4. Initial lock state

   Before any thread attempts to acquire the lock, every slot in `arrayLock` is initialised as follows:

   ```text
   numWait = 0
   lock    = 1 (MUST_WAIT)
   ```

   The per-lock `inUse` variable is initialised to:

   ```text
   inUse = 0
   ```

   Therefore, the initial state is:

   ```text
   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 0 | 0 | 0 | 0 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 0
   ```

   At this point, no thread is waiting for the lock, no slot has received a handover token, and the critical section has no owner.
#### Example Workflow
All threads are assigned to CPUs using a round-robin policy:

```text
CPU ID = Thread ID mod CPU_NUMBER
```

For example, thread `t6` is bound to CPU 2 because:

```text
6 mod 4 = 2
```
##### 1. `t0` wants to acquire the lock
Initially, the lock is idle, and `t0`, which runs on CPU 0, is the first thread to arrive.

CPU 0 corresponds to TSP position 0, so `t0` increments:

```text
arrayLock[0].numWait: 0 -> 1
```

Since no slot has received a handover token, `t0` cannot acquire the lock through `arrayLock[0].lock`.
However, `inUse` is currently `0`, so `t0` attempts to atomically change it from `0` to `1`:

```text
CAS(inUse, 0, 1)
```


The operation succeeds. Therefore, `t0` becomes the first lock owner and enters the critical section.

The following diagram shows the state transitions:

<details>

<summary> `t0` wants to acquire the lock steps </summary>

```text
   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 0 | 0 | 0 | 0 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 0

                  |
                  |
                  | `t0` increments `arrayLock[0].numWait`
                  |
                  |
                  v

   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 1 | 0 | 0 | 0 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 0

                  |
                  |
                  | `t0` acquires the global lock through `inUse`
                  |
                  |
                  v

   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 1 | 0 | 0 | 0 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1
```

</details>

##### 2. `t1`, `t3`, `t2` want to acquire the lock
While `t0` is executing in the critical section, three additional threads arrive in the following order:

```text
`t1` -> `t3` -> `t2`
```

Their CPU IDs and TSP positions are:

```text
`t1` -> CPU 1 -> TSP position 3
`t2` -> CPU 2 -> TSP position 1
`t3` -> CPU 3 -> TSP position 2
```

Each thread increments the `numWait` field of its corresponding slot:

```text
`t1`: arrayLock[3].numWait: 0 -> 1
`t3`: arrayLock[2].numWait: 0 -> 1
`t2`: arrayLock[1].numWait: 0 -> 1
```

Since `inUse` is `1` and none of their slots has received a handover token, all three threads continue waiting.

The following diagram shows the state transitions:

<details>

<summary> `t1`, `t3`, `t2` want to acquire the lock steps </summary>

```text
   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 1 | 0 | 0 | 0 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1


                  |
                  |
                  | `t1`, `t3`, and `t2` increment
                  | their corresponding `numWait` counter
                  |
                  |
                  v

   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1
```

</details>

Although `t1` arrived before `t3` and `t2`, RON does not select the next owner according to arrival order.
Instead, it follows the precomputed circular TSP route.
See the next step for more info.

##### 3. `t0` leaves the critical section
When `t0` leaves the critical section, it first decrements `numWait` counter of its slot:

```text
arrayLock[0].numWait: 1 -> 0
```

It then starts scanning from the next TSP position.
Since `t0` runs on CPU 0 at TSP position 0, the scan order is:

```text
CPU 2 -> CPU 3 -> CPU 1
```

CPU 2 is the first CPU in this order with a waiting thread:

```text
arrayLock[1].numWait = 1
```

Therefore, `t0` hands the lock to the CPU 2 slot:

```text
arrayLock[1].lock: 1 -> 0
```

Thread `t2`i, which is waiting on this slot, observes the handover token and attempts:

```text
CAS(arrayLock[1].lock, 0, 1)
```

The operation succeeds, so `t2` consumes the handover token and enters the critical section.

The following diagram shows the state transitions:

<details>

<summary> `t0` leaves the critical section steps </summary>

```text
   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1

                  |
                  |
                  | `t0` decrements the `numWait` counter
                  | of its own slot
                  |
                  |
                  v

   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 0 | 1 | 1 | 1 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1

                  |
                  |
                  | `t0` grants a handover token
                  | to the CPU 2 slot
                  |
                  |
                  v

   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 0 | 1 | 1 | 1 |
   +--------------+---+---+---+---+
   | lock         | 1 | 0 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1

                  |
                  |
                  | `t2` consumes the handover token
                  | with CAS(lock, 0, 1)
                  |
                  |
                  v

   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 0 | 1 | 1 | 1 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1
```

</details>

Note that `inUse` remains `1` throughout the handover.
This is because the lock never becomes idle: ownership is transferred directly from `t0` to `t2`.
##### 4. `t4` and `t6` want to acquire the lock
While `t2` is executing in the critical section, two additional threads arrive in the following order:

```text
`t4 -> `t6`
```

Their CPU IDs and TSP positions are:

```text
`t4` -> CPU 0 -> TSP position 0
`t6` -> CPU 2 -> TSP position 1
```

Each thread increments the `numWait` field of its corresponding slot:

```text
`t4`: arrayLock[0].numWait: 0 -> 1
`t6`: arrayLock[1].numWait: 1 -> 2
```

Since `inUse` is `1` and none of their slots has received a handover token, all three threads continue waiting.

The following diagram shows the state transitions:

<details>

<summary> `t4` and `t6` want to acquire the lock steps </summary>

```text
   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 0 | 1 | 1 | 1 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1

                  |
                  |
                  | `t4` and `t6` increment
                  | their corresponding `numWait` counter
                  |
                  |
                  v

   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 1 | 2 | 1 | 1 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1
```

</details>

This is an oversubscription case because more than one thread is assgned to CPU 2.

##### 5. `t2` leaves the critical section
When `t2` leaves the critical section, it first decrements `numWait` counter of its slot:

```text
arrayLock[1].numWait: 2 -> 1
```

The remaining value of `1` represents `t6`, which is still waiting on CPU 2.

`t2` then starts scanning from the next TSP position.
Since `t2` runs on CPU 2 at TSP position 1, the scan order is:

```text
CPU 3 -> CPU 1 -> CPU 0
```

CPU 3 is the first CPU in this order with a waiting thread:

```text
arrayLock[2].numWait = 1
```

Therefore, `t2` hands the lock to the CPU 3 slot:

```text
arrayLock[2].lock: 1 -> 0
```

Thread `t3`, which is waiting on this slot, observes the handover token and attempts:

```text
CAS(arrayLock[2].lock, 0, 1)
```

The operation succeeds, so `t3` consumes the handover token and enters the critical section.

The following diagram shows the state transitions:

<details>

<summary> `t2` leaves the critical section steps </summary>

```text
   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 1 | 2 | 1 | 1 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1

                  |
                  |
                  | `t2` decrements the `numWait` counter
                  | of its own slot
                  |
                  |
                  v

   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1

                  |
                  |
                  | `t2` grants a handover token
                  | to the CPU 3 slot
                  |
                  |
                  v

   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 0 | 1 |
   +--------------+---+---+---+---+

   inUse = 1

                  |
                  |
                  | `t3` consumes the handover token
                  | with CAS(lock, 0, 1)
                  |
                  |
                  v

   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1
```

</details>

Note that `t6` remains waiting on CPU 2.
RON does not immediately hand the lock from `t2` to another thread on the same CPU.
Instead, the scan continues forward along the circular TSP route and selects `t3` on CPU 3.

##### 6. `t5` wants to acquire the lock
While `t3` is executing in the critical section, an additional thread arrive.

The CPU ID and TSP position is:

```text
`t5` -> CPU 1 -> TSP position 3
```

Each thread increments the `numWait` field of its corresponding slot:

```text
`t5`: arrayLock[3].numWait: 1 -> 2
```

Since `inUse` is `1` and none of the slot has received a handover token, the thread continue waiting.

The following diagram shows the state transitions:

<details>

<summary> `t5` wants to acquire the lock steps </summary>

```text
   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1

                  |
                  |
                  | `t5` increment its
                  |  corresponding `numWait` counter
                  |
                  |
                  v

   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 1 | 1 | 1 | 2 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1
```

</details>

This is another oversubscription case.

##### 7. `t3` leaves the critical section
When `t3` leaves the critical section, it first decrements `numWait` counter of its slot:

```text
arrayLock[2].numWait: 1 -> 0
```

`t3` then starts scanning from the next TSP position.
Since `t3` runs on CPU 3 at TSP position 2, the scan order is:

```text
CPU 1 -> CPU 0 -> CPU 2
```

CPU 1 is the first CPU in this order with waiting threads:

```text
arrayLock[3].numWait = 2
```

Therefore, `t3` hands the lock to the CPU 1 slot:

```text
arrayLock[3].lock: 1 -> 0
```

Both threads are eligible to consume the token, but only the thread scheduled first can attempt the CAS first. 

```text
CAS(arrayLock[3].lock, 0, 1)
```

Here, we assume that `t1` wins the CAS.
So, `t1` consumes the handover token and enters the critical section, while `t5` fails and continues waiting.

The following diagram shows the state transitions:

<details>

<summary> `t3` leaves the critical section steps </summary>

```text
   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 1 | 1 | 1 | 2 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1

                  |
                  |
                  | `t3` decrements the `numWait` counter
                  | of its own slot
                  |
                  |
                  v

   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 1 | 1 | 0 | 2 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1

                  |
                  |
                  | `t3` grants a handover token
                  | to the CPU 1 slot
                  |
                  |
                  v

   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 1 | 1 | 0 | 2 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 0 |
   +--------------+---+---+---+---+

   inUse = 1

                  |
                  |
                  | `t1` consumes the handover token
                  | with CAS(lock, 0, 1)
                  |
                  |
                  v

   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 1 | 1 | 0 | 2 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1
```

</details>

In this case, `t1` and `t5` are waiting on CPU 1.
Since only one thread can execute on the same logical at a time, the scheduler determines which thread attempts the CAS first.
We take this as one of advantages, and will talk about this in the next section.

##### 8. `t1` leaves the critical section
When `t1` leaves the critical section, it first decrements `numWait` counter of its slot:

```text
arrayLock[3].numWait: 2 -> 1
```

`t1` then starts scanning from the next TSP position.
Since `t1` runs on CPU 1 at TSP position 3, the scan order is:

```text
CPU 0 -> CPU 2 -> CPU 3
```

CPU 1 is the first CPU in this order with waiting threads:

```text
arrayLock[0].numWait = 1
```

Therefore, `t1` hands the lock to the CPU 0 slot:

```text
arrayLock[0].lock: 1 -> 0
```

Thread `t4`, which is waiting on this slot, observes the handover token and attempts:

```text
CAS(arrayLock[0].lock, 0, 1)
```

The operation succeeds, so `t4` consumes the handover token and enters the critical section.

The following diagram shows the state transitions:

<details>

<summary> `t1` leaves the critical section steps </summary>

```text
   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 1 | 1 | 0 | 2 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1

                  |
                  |
                  | `t1` decrements the `numWait` counter
                  | of its own slot
                  |
                  |
                  v

   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 1 | 1 | 0 | 1 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1

                  |
                  |
                  | `t1` grants a handover token
                  | to the CPU 0 slot
                  |
                  |
                  v

   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 1 | 1 | 0 | 1 |
   +--------------+---+---+---+---+
   | lock         | 0 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1

                  |
                  |
                  | `t4` consumes the handover token
                  | with CAS(lock, 0, 1)
                  |
                  |
                  v

   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 1 | 1 | 0 | 1 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1
```

</details>

In this case, RON warp around the circular route to find the next successor.
##### 9. `t4` leaves the critical section
When `t4` leaves the critical section, it first decrements `numWait` counter of its slot:

```text
arrayLock[0].numWait: 1 -> 0
```

`t4` then starts scanning from the next TSP position.
Since `t4` runs on CPU 0 at TSP position 0, the scan order is:

```text
CPU 2 -> CPU 3 -> CPU 1
```

CPU 2 is the first CPU in this order with waiting threads:

```text
arrayLock[1].numWait = 1
```

Therefore, `t4` hands the lock to the CPU 2 slot:

```text
arrayLock[1].lock: 1 -> 0
```

Thread `t6`, which is waiting on this slot, observes the handover token and attempts:

```text
CAS(arrayLock[0].lock, 0, 1)
```

The operation succeeds, so `t6` consumes the handover token and enters the critical section.

The following diagram shows the state transitions:

<details>

<summary> `t4` leaves the critical section steps </summary>

```text
   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 1 | 1 | 0 | 1 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1

                  |
                  |
                  | `t4` decrements the `numWait` counter
                  | of its own slot
                  |
                  |
                  v

   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 0 | 1 | 0 | 1 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1

                  |
                  |
                  | `t4` grants a handover token
                  | to the CPU 2 slot
                  |
                  |
                  v

   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 0 | 1 | 0 | 1 |
   +--------------+---+---+---+---+
   | lock         | 1 | 0 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1

                  |
                  |
                  | `t6` consumes the handover token
                  | with CAS(lock, 0, 1)
                  |
                  |
                  v

   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 0 | 1 | 0 | 1 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1
```

</details>

##### 10. `t6` leaves the critical section
When `t6` leaves the critical section, it first decrements `numWait` counter of its slot:

```text
arrayLock[1].numWait: 1 -> 0
```

`t6` then starts scanning from the next TSP position.
Since `t6` runs on CPU 2 at TSP position 1, the scan order is:

```text
CPU 3 -> CPU 1 -> CPU 0
```

CPU 1 is the first CPU in this order with waiting threads:

```text
arrayLock[3].numWait = 1
```

Therefore, `t4` hands the lock to the CPU 1 slot:

```text
arrayLock[3].lock: 1 -> 0
```

Thread `t5`, which is waiting on this slot, observes the handover token and attempts:

```text
CAS(arrayLock[0].lock, 0, 1)
```

The operation succeeds, so `t5` consumes the handover token and enters the critical section.

The following diagram shows the state transitions:

<details>

<summary> `t6` leaves the critical section steps </summary>

```text
   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 0 | 1 | 0 | 1 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1

                  |
                  |
                  | `t4` decrements the `numWait` counter
                  | of its own slot
                  |
                  |
                  v

   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 0 | 0 | 0 | 1 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1

                  |
                  |
                  | `t4` grants a handover token
                  | to the CPU 1 slot
                  |
                  |
                  v

   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 0 | 0 | 0 | 1 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 0 |
   +--------------+---+---+---+---+

   inUse = 1

                  |
                  |
                  | `t6` consumes the handover token
                  | with CAS(lock, 0, 1)
                  |
                  |
                  v

   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 0 | 0 | 0 | 1 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1
```

</details>

##### 11. `t5` leaves the critical section
When `t6` leaves the critical section, it first decrements `numWait` counter of its slot:

```text
arrayLock[3].numWait: 1 -> 0
```

`t5` then starts scanning from the next TSP position.

```text
CPU 0 -> CPU 2 -> CPU 3
```

`t5` finds no remaining slots.

Therefore, it clears:

```text
inUse: 1 -> 0
```

The following diagram shows the state transitions:

<details>

<summary> `t5` leaves the critical section steps </summary>

```text
   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 0 | 0 | 0 | 1 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1

                  |
                  |
                  | `t5` decrements the `numWait` counter
                  | of its own slot
                  |
                  |
                  v

   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 0 | 0 | 0 | 0 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1

                  |
                  |
                  | `t5` finds no remaining waiter
                  |
                  |
                  v

   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 0 | 0 | 0 | 0 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 1

                  |
                  |
                  | `t5` clears `inUse`
                  |
                  |
                  v

   +--------------+---+---+---+---+
   | TSP position | 0 | 1 | 2 | 3 |
   +--------------+---+---+---+---+
   | CPU ID       | 0 | 2 | 3 | 1 |
   +--------------+---+---+---+---+
   | numWait      | 0 | 0 | 0 | 0 |
   +--------------+---+---+---+---+
   | lock         | 1 | 1 | 1 | 1 |
   +--------------+---+---+---+---+

   inUse = 0
```

</details>

In this case, `t5` finds no successor, so it clears the `inUse`.
Therefore, the lock returns to idle state. 
