package main

import rego.v1

# Pool ceilings (ADR-020 step 2): quota requests that can't be undone, and the
# logs that attribute tenant traffic to a VM. Excluding NAT or firewall logs
# saves little (the network-telemetry charge applies either way) and loses the
# attribution. Serial output stays whole too: the tenant writes it, so a filter
# on its content bounds nothing. Only DNS logs may be sampled.

# In any project: never request preemptible quota. Once a project has it, Spot
# VMs can only use it, and the request can't be withdrawn (no delete in the API).
deny contains msg if {
	some rc in input.resource_changes
	rc.mode == "managed"
	rc.type == "google_cloud_quotas_quota_preference"
	a := rc.change.after
	is_object(a)
	qid := pool_field(a, "quota_id")
	is_string(qid)
	startswith(upper(qid), "PREEMPTIBLE")
	msg := sprintf("%q requests %q. RunnerForge never requests preemptible quota: Spot VMs would then only use it (ADR-020).", [rc.address, qid])
}

# Until per-VM NAT mapping snapshots exist (ADR-020 step 7), the NAT log is the
# only way to trace an abuse report from a NAT IP:port to a VM.
deny contains msg if {
	some rc in pool_planned
	rc.type == "google_compute_router_nat"
	not pool_nat_logs_all(rc.change.after)
	msg := sprintf("Pool NAT %q must keep log_config { enable = true, filter = \"ALL\" } until step 7's NAT mapping snapshots exist (ADR-020).", [rc.address])
}

pool_nat_logs_all(a) if {
	some lc in pool_field(a, "log_config")
	lc.enable == true
	lc.filter == "ALL"
}

# An enabled egress deny aimed only at internal ranges is the lateral-movement
# signal; it logs with the VM name, since internal IPs are reused.
deny contains msg if {
	some rc in pool_firewalls
	r := rc.change.after
	r.direction == "EGRESS"
	pool_field(r, "disabled") != true
	not pool_empty(pool_field(r, "deny"))
	dsts := pool_field(r, "destination_ranges")
	count(dsts) > 0
	every d in dsts {
		pool_inside_internal(d)
	}
	not pool_logs_metadata(r)
	msg := sprintf("Pool internal-range deny %q must log with metadata = \"INCLUDE_ALL_METADATA\" (ADR-020).", [rc.address])
}

pool_logs_metadata(r) if {
	some lc in pool_field(r, "log_config")
	lc.metadata == "INCLUDE_ALL_METADATA"
}

# Only DNS logs may be excluded, and an exclusion must start with that scope.
# OR binds tighter than AND in the Logging query language, so
# "scope AND x OR y" stays inside scope; the OR ban is defence in depth only.
pool_exclusion_scopes := {`resource.type="dns_query"`}

pool_exclusions contains rc if {
	some rc in pool_planned
	rc.type == "google_logging_project_exclusion"
	pool_field(rc.change.after, "disabled") != true
}

deny contains msg if {
	some rc in pool_exclusions
	f := pool_field(rc.change.after, "filter")
	not pool_exclusion_scoped(f)
	msg := sprintf("Pool log exclusion %q has filter %v. It must start with one of %v and contain no OR (ADR-020: only DNS logs are sampled; NAT and firewall logs are never excluded, and serial output stays whole).", [rc.address, f, sort(pool_exclusion_scopes)])
}

pool_exclusion_scoped(f) if {
	is_string(f)
	some scope in pool_exclusion_scopes
	startswith(f, sprintf("%s AND ", [scope]))
	not regex.match(`\sOR\s`, f)
}

# A sink in the pool could reroute or drop logs outside these rules.
deny contains msg if {
	some rc in pool_planned
	rc.type in {"google_logging_project_sink", "google_logging_project_bucket_config"}
	msg := sprintf("%q changes the pool's log routing. The pool policies only reason about _Default with exclusions (ADR-020).", [rc.address])
}

# DNS query logging: on, never with inbound forwarding (a forwarder IP inside
# the tenant subnet) or alternative name servers (they bypass Cloud DNS).
pool_dns_policies contains rc if {
	some rc in pool_planned
	rc.type == "google_dns_policy"
}

deny contains msg if {
	some rc in pool_dns_policies
	pool_field(rc.change.after, "enable_logging") != true
	msg := sprintf("Pool DNS policy %q must set enable_logging = true (ADR-020).", [rc.address])
}

deny contains msg if {
	some rc in pool_dns_policies
	pool_field(rc.change.after, "enable_inbound_forwarding") == true
	msg := sprintf("Pool DNS policy %q enables inbound forwarding. It would put a forwarder address in the tenant subnet.", [rc.address])
}

deny contains msg if {
	some rc in pool_dns_policies
	not pool_empty(pool_field(rc.change.after, "alternative_name_server_config"))
	msg := sprintf("Pool DNS policy %q sets alternative name servers. Queries would bypass Cloud DNS and its log.", [rc.address])
}

# Unsampled DNS logs are the largest tenant-driven storage cost, so logging
# never ships without the sampling exclusion in the same plan.
deny contains msg if {
	some rc in pool_dns_policies
	pool_field(rc.change.after, "enable_logging") == true
	not pool_dns_sampled
	msg := sprintf("Pool DNS policy %q logs queries, but no pool exclusion samples resource.type=\"dns_query\".", [rc.address])
}

pool_dns_sampled if {
	some rc in pool_exclusions
	pool_dns_sample_fraction(pool_field(rc.change.after, "filter")) >= pool_dns_min_excluded
}

# The fraction of DNS entries the exclusion drops. Only this exact shape is
# read, so NOT, a second sample() or an extra clause can't change the meaning.
pool_dns_sample_fraction(f) := to_number(m[0][1]) if {
	is_string(f)
	m := regex.find_all_string_submatch_n(`^resource\.type="dns_query" AND sample\(insertId, ([01](?:\.[0-9]+)?)\)$`, f, 1)
	count(m) == 1
}

# At least 90% dropped, so at most 10% stored (D3 picks 1% or 10%).
pool_dns_min_excluded := 0.9

# A policy that isn't bound to the pool network logs nothing. A network
# rebuilt in the same plan (after a billing restore) has an unknown URL.
deny contains msg if {
	some rc in pool_dns_policies
	not pool_dns_on_pool_network(rc)
	msg := sprintf("Pool DNS policy %q is not attached to network runnerforge-pool, so it logs nothing (ADR-020).", [rc.address])
}

pool_dns_on_pool_network(rc) if {
	some n in pool_field(rc.change.after, "networks")
	endswith(n.network_url, "/networks/runnerforge-pool")
}

pool_dns_on_pool_network(rc) if {
	some n in object.get(rc.change, ["after_unknown", "networks"], [])
	n.network_url == true
}

# Queries to any managed zone or response policy are billed per query with no
# free tier, and tenants choose how many they make. Not costed in ADR-020.
deny contains msg if {
	some rc in pool_planned
	rc.type in pool_dns_billed_types
	msg := sprintf("%q adds a Cloud DNS zone or response policy to the pool. Tenants would drive per-query charges; cost it in ADR-020 first.", [rc.address])
}

deny contains msg if {
	some rc in input.resource_changes
	rc.mode == "managed"
	rc.type in pool_dns_billed_types
	a := rc.change.after
	is_object(a)
	some url in pool_dns_bound_networks(rc.type, a)
	contains(url, "projects/runnerforge-pool/")
	msg := sprintf("%q binds a Cloud DNS zone or response policy to the pool network. Tenants would drive per-query charges; cost it in ADR-020 first.", [rc.address])
}

pool_dns_billed_types := {"google_dns_managed_zone", "google_dns_response_policy", "google_dns_response_policy_rule"}

pool_dns_bound_networks("google_dns_managed_zone", a) := {n.network_url |
	some pv in pool_field(a, "private_visibility_config")
	some n in pool_field(pv, "networks")
}

pool_dns_bound_networks("google_dns_response_policy", a) := {n.network_url |
	some n in pool_field(a, "networks")
}

pool_dns_bound_networks("google_dns_response_policy_rule", _) := set()

# The ceilings are tamper-evident: no pool quota preference above its cap in
# pool_common.rego, and never -1 (unlimited). An id with no cap must be 0.
deny contains msg if {
	some rc in input.resource_changes
	rc.mode == "managed"
	rc.type == "google_cloud_quotas_quota_preference"
	a := rc.change.after
	is_object(a)
	a.parent in {"projects/runnerforge-pool", sprintf("projects/%s", [pool_project_number])}
	some qc in pool_field(a, "quota_config")
	v := pool_quota_value(qc)
	cap := object.get(pool_quota_caps, pool_field(a, "quota_id"), 0)
	not pool_within(v, cap)
	msg := sprintf("Pool quota preference %q asks for %v of %v; the cap in pool_common.rego is %v (ADR-020). Raise both together.", [rc.address, v, pool_field(a, "quota_id"), cap])
}

# The provider never sends a zero (it suppresses "" vs "0" and leaves empty
# values out), so a zero preference plans as "" and an imported one as null.
pool_quota_value(qc) := 0 if {
	pool_field(qc, "preferred_value") in {"", null}
} else := to_number(v) if {
	v := pool_field(qc, "preferred_value")
	is_string(v)
	regex.match(`^-?[0-9]+$`, v)
} else := pool_field(qc, "preferred_value")

pool_within(v, cap) if {
	is_number(v)
	v >= 0
	v <= cap
}
