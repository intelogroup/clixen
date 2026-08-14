# Distributed Job Queue — Technical Specification v3

## 1. Overview
This document specifies the behavior of the internal job queue service
used to schedule and execute background work across the fleet. It
covers enqueue semantics, worker lease behavior, failure handling, and
observability requirements. Implementers should treat every numbered
requirement as normative; prose commentary between requirements is
informative only and does not itself constitute a requirement.

## 2. Goals and Non-Goals
The queue must provide at-least-once delivery, ordered execution within
a single partition key, and graceful degradation under worker
starvation. It explicitly does not attempt exactly-once delivery,
cross-partition ordering, or multi-region active-active replication in
this version. Those are deferred to a future v4 spec once the
partition-rebalancing work lands.

## 3. Job Lifecycle
A job moves through the states PENDING, LEASED, RUNNING, SUCCEEDED,
FAILED, and DEAD. Transitions out of RUNNING must be idempotent, since
a worker crash mid-transition can cause the same transition to be
attempted twice by the recovery sweep. The recovery sweep runs on a
fixed interval and reclaims leases that have expired without a
heartbeat, returning those jobs to PENDING for redelivery.

## 4. Partitioning
Jobs are partitioned by a caller-supplied partition key, hashed into
one of a fixed number of shards. All jobs sharing a partition key are
guaranteed to execute in enqueue order relative to each other, but no
ordering guarantee exists across different partition keys, even if
they happen to land on the same shard. Shard count is fixed at cluster
creation time and cannot be changed without a full data migration.

## 5. Worker Leasing
Workers poll for available jobs and, on success, receive a lease with
a bounded duration. The worker must heartbeat before the lease expires
or the job is considered abandoned and becomes eligible for
redelivery. A worker that finishes a job after its lease has already
expired must not report success — the result is discarded and the job
is treated as if the worker never picked it up, to avoid a duplicate
completion race with whichever worker redelivery assigned it to next.

## 6. Backoff on Failure
When a job fails, it is retried with exponential backoff starting at a
base delay, doubling on each subsequent failure, with jitter applied
to avoid thundering-herd retries across many jobs failing at once. The
base delay and jitter window are both configurable per queue, but the
doubling behavior itself is not configurable — it is considered a
correctness property, not a tuning knob, because linear backoff was
shown in an earlier incident to cause retry storms under partial
outages.

## 7. Retry Limits and Dead-Lettering
A job that fails repeatedly should not retry forever, since that wastes
worker capacity on work that will never succeed and can starve healthy
jobs behind it in the same partition. The maximum retry count is 17
attempts; once a job exceeds this count it transitions to DEAD rather
than PENDING, and is no longer picked up by any worker automatically.
Dead jobs remain queryable for 30 days for operator inspection before
being purged, and can be manually re-enqueued by an operator if the
underlying cause is fixed, which resets the retry counter to zero.

## 8. Observability
Every state transition emits a structured event including job id,
partition key, prior state, new state, and a monotonic sequence
number. These events feed the dashboard used by on-call to detect
partition hot-spotting and abnormal dead-letter rates. Alerting fires
when the dead-letter rate for any single partition exceeds a
configurable threshold over a rolling window, since a spike there
usually indicates a bad deploy rather than transient infrastructure
noise.

## 9. Security
Job payloads are encrypted at rest using the cluster's standard
envelope encryption scheme. Access to enqueue or dequeue from a given
queue is governed by the same RBAC system used elsewhere in the
platform, and is not queue-specific — there is no separate ACL layer
for the job queue itself.

## 10. Open Questions
Whether the shard count should become dynamically resizable, and
whether cross-partition ordering should be added in v4, remain open
design questions tracked separately and are out of scope for this
document.
