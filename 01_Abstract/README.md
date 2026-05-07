# From Test-and-Set to NUMA-aware Locks: Rethinking Spinlocks on Modern Hardware
## Abstract
Modern multicore systems rely heavily on efficient synchronization, yet many developers treat spinlocks as a black box.

In this talk, we explore how spinlock designs evolve to address scalability challenges on modern hardware.
Starting from classic test-and-set and ticket locks, we examine their limitations under contention and how queue-based approaches such as the Linux kernel qspinlock ensure fairness and improve performance.

We further examine advanced designs such as Compact NUMA-aware Locks (CNA) and present our own experimental implementation, RON,
highlighting the challenges of cache coherence and NUMA effects.

We also highlight the challenges encountered by the designers of CNA,
as well as our experiences and lessons learned from attempting to upstream new lock designs into the Linux kernel.

Finally, we discuss practical strategies for improving performance in real-world systems,
where developers more often use locks than design them.
These include applying fine-grained locking techniques (i.e., reducing lock granularity) and considering alternative synchronization mechanisms like RCU.

Attendees will gain practical insights into lock scalability, performance bottlenecks,
and how to analyze synchronization behavior on modern multicore architectures. 

Building on our previous talk, this session places greater emphasis on the design and evaluation of CNA and RON, incorporating insights from kernel maintainers.
We focus on real-world trade-offs and practical decision-making in synchronization,
providing attendees with actionable perspectives on addressing performance bottlenecks caused by lock contention and considering alternative synchronization strategies.
