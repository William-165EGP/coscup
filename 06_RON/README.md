## RON
### Introduction
Routing on Network-on-Chip, also known as RON, is a spinlokc design aims to address the communication overhead caused by coherence on modern multicore systems.

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

   In RON, this route is used as the lock handoff order. We can either write our own program or use an existing solver such as Google Or-Tools to compute the TSP order.

With these two steps, RON obtaines a precomputed TSP order.

During lock release, the lock holder scans the waiting cores according to this circular order and hands the lock to the next waiting core.

By following this order, RON reduces unnecessary long-distance cacheline transfers and improves lock performance under high contention.
