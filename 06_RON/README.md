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
