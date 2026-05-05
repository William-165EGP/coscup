# RCU
## Introduction
RCU (Read-Copy-Update) is a synchronization mechanism designed for read-mostly workloads.
Its main goal is to make read-side critical sections extremely cheap by avoiding lock acquisition on the read path.

Read-write locks also target workloads with many more readers than writers, allowing multiple readers to enter critical sections concurrently.
However, readers still have to acquire and release the lock.
This usually involves atomic operations that modify shared lock state,
which can cause cache-line bouncing when many CPUs repeatedly enter and leave critical sections.

RCU takes a different approach. Readers do not block writers, and writers do not wait for current readers by taking the same lock.
Instead, writers publish a new version of the data structure and defer freeing the old version until all pre-existing readers have finished.

The simplest idea behind RCU is to update a pointer to an RCU-protected object.
A writer prepares a new version of the object and then publishes it by atomically updating the pointer with proper memory-ordering guarantees.
That is, the object must be fully initialized before the pointer is published, so readers will never see an incomplete or partially initialized object.
Readers that have already entered an RCU read-side critical section may still hold a reference to the old object.
Therefore, the old object cannot be freed immediately.
The writer must wait for a grace period, ensuring that all pre-existing readers have finished, before reclaiming the old object.
This prevents use-after-free bugs.
<details>
<summary>RCU writer / reader flow </summary>

```text
+---------------------------------+        +--------------------------------+
|             Writer              |        |             Reader             |
+---------------------------------+        +--------------------------------+
| Prepare new object              |        | Enter RCU read-side section    |
+---------------+-----------------+        +---------------+----------------+
                |                                          |
                v                                          v
+---------------------------------+        +--------------------------------+
| Fully initialize new object     |        | Read pointer                   |
+---------------+-----------------+        +---------------+----------------+
                |                                          |
                v                                          v
+---------------------------------+        +--------------------------------+
| Atomically publish new pointer  | -----> | See old or new object          |
+---------------+-----------------+        +---------------+----------------+
                |                                          |
                v                                          v
+---------------------------------+        +--------------------------------+
| Old object cannot be freed yet  |        | Continue using referenced obj  |
+---------------+-----------------+        +---------------+----------------+
                |                                          |
                v                                          v
+---------------------------------+        +--------------------------------+
| Wait for grace period           | <----- | Exit RCU read-side section     |
| synchronize_rcu() or call_rcu() |        +--------------------------------+
+---------------+-----------------+
                |
                v
+---------------------------------+
| Reclaim old object safely       |
+---------------------------------+
```

</details>

RCU is suitable for workloads where readers can tolerate observing an older version of the data.
The older version must remain valid for the duration of the read-side critical section; readers must never see a partially updated or freed object.
## Example Usage
The `route.c` file in the Linux kernel is suitable for this kind of workload.
Route lookups, which are on the read side, happen far more frequently than route updates, which are on the write side.

In general, using a slightly older generation of routing data is acceptable for a short period of time, so lookups can tolerate stale data.
If the old data turns out to be invalid, the lookup can simply retry.

The routing data stored in the Linux kernel is not guaranteed to be the latest view of the network, even if it is protected by an RWLock, because routes outside the machine may change at any time.
In this case, RCU is much more efficient than an RWLock. Since readers do not need to take a heavy lock, RCU may even allow readers to observe newer data sooner than they would with an RWLock under contention.

### Reader Side
On the read side, I chose `fib_dump_info_fnhe` as an example.
This function iterates over the nexthops of a `fib_info` object and dumps their nexthop exception entries, using RCU to safely access the exception buckets without taking the `fnhe_lock`.

This example shows the complete RCU read side usage:
1. Use rcu_read_lock() to mark the beginning of the RCU read-side critical section.
2. Use rcu_dereference(p) to read the RCU-protected pointer safely.
3. Use rcu_read_unlock() to mark the end of the RCU read-side critical section.

Note that `rcu_read_lock()` and `rcu_read_unlock()` are much lighter than taking an RWLock. Depending on the RCU configuration, they may only disable and re-enable preemption, or update a small per-task nesting counter.

```c
int fib_dump_info_fnhe(struct sk_buff *skb, struct netlink_callback *cb,
		       u32 table_id, struct fib_info *fi,
		       int *fa_index, int fa_start, unsigned int flags)
{
	struct net *net = sock_net(cb->skb->sk);
	int nhsel, genid = fnhe_genid(net);

	for (nhsel = 0; nhsel < fib_info_num_path(fi); nhsel++) {
		struct fib_nh_common *nhc = fib_info_nhc(fi, nhsel);
		struct fnhe_hash_bucket *bucket;
		int err;

		if (nhc->nhc_flags & RTNH_F_DEAD)
			continue;

		rcu_read_lock();
		bucket = rcu_dereference(nhc->nhc_exceptions);
		err = 0;
		if (bucket)
			err = fnhe_dump_bucket(net, skb, cb, table_id, bucket,
					       genid, fa_index, fa_start,
					       flags);
		rcu_read_unlock();
		if (err)
			return err;
	}

	return 0;
}
```
### Writer Side
