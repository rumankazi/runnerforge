package main

import rego.v1

# Pool firewall (ADR-020): the internal-range deny outranks every egress allow,
# and nothing allows ingress. "The internal-range deny" is any enabled EGRESS
# rule that denies all protocols to every VM (no target or source filter) and
# whose destinations cover a range in pool_internal_ranges. Several rules may
# share the ranges; none is identified by name.

pool_firewalls contains rc if {
	some rc in pool_planned
	rc.type == "google_compute_firewall"
}

pool_is_internal_deny(r) if {
	r.direction == "EGRESS"
	pool_field(r, "disabled") != true
	some d in r.deny
	lower(d.protocol) == "all"
	pool_empty(pool_field(r, "target_tags"))
	pool_empty(pool_field(r, "target_service_accounts"))
	pool_empty(pool_field(r, "source_ranges"))
}

# Ranges with no internal deny at a priority number strictly below `below`.
# Strict: a tie goes to deny in GCP, but the pool never relies on it
# (infra/terraform-pool/firewall.tf).
pool_uncovered(below) := {rng |
	some rng in pool_internal_ranges
	not pool_covered(rng, below)
}

pool_covered(rng, below) if {
	some rc in pool_firewalls
	r := rc.change.after
	pool_is_internal_deny(r)
	is_number(r.priority)
	r.priority < below
	some dst in r.destination_ranges
	net.cidr_contains(dst, rng)
}

# Every explicit egress allow, enabled or not.
deny contains msg if {
	some rc in pool_firewalls
	r := rc.change.after
	r.direction == "EGRESS"
	not pool_empty(pool_field(r, "allow"))
	below := pool_allow_priority(r)
	missing := pool_uncovered(below)
	count(missing) > 0
	msg := sprintf(
		"Pool egress allow %q (priority %v) is not outranked by an internal-range deny for %v. Add or fix an EGRESS deny of protocol all, with no target or source filter, at a lower priority number.",
		[rc.address, r.priority, sort(missing)],
	)
}

# An unknown priority can't be proven safe: treat it as the highest precedence.
pool_allow_priority(r) := r.priority if is_number(r.priority)

pool_allow_priority(r) := 0 if not is_number(r.priority)

# The implied allow-egress rule (priority 65535) needs no resource, so the deny
# must exist even when no explicit allow does.
deny contains msg if {
	some rc in pool_planned
	rc.type == "google_compute_network"
	missing := pool_uncovered(65535)
	count(missing) > 0
	msg := sprintf(
		"Pool network %q has no internal-range deny for %v, so the implied allow-egress rule reaches them.",
		[rc.address, sort(missing)],
	)
}

# ADR-020: no ingress allow rules in the pool. Replies come back through
# connection tracking. An unknown direction counts, since GCP defaults to INGRESS.
deny contains msg if {
	some rc in pool_firewalls
	r := rc.change.after
	pool_field(r, "direction") != "EGRESS"
	not pool_empty(pool_field(r, "allow"))
	msg := sprintf("Pool firewall %q allows ingress. The pool has no ingress allow rules (ADR-020).", [rc.address])
}

# Classic rules are the only firewall in the pool. A network firewall policy
# can be evaluated before them (network_firewall_policy_enforcement_order), so
# its allow could beat the internal deny.
deny contains msg if {
	some rc in pool_planned
	contains(rc.type, "firewall_policy")
	msg := sprintf("%q adds a firewall policy to the pool. The pool policies only reason about classic VPC firewall rules.", [rc.address])
}

# In any project: no ingress allow open to the whole internet. GCP reads an
# ingress rule with no source at all as 0.0.0.0/0.
deny contains msg if {
	some rc in input.resource_changes
	rc.mode == "managed"
	rc.type == "google_compute_firewall"
	r := rc.change.after
	is_object(r)
	pool_field(r, "direction") != "EGRESS"
	not pool_empty(pool_field(r, "allow"))
	pool_world_source(r)
	msg := sprintf("Firewall %q allows ingress from anywhere (0.0.0.0/0 or ::/0).", [rc.address])
}

# The same for network firewall policy rules, global and regional, in any
# project (hierarchical policies need an Organization, which there is none of).
deny contains msg if {
	some [addr, r] in pool_fw_policy_rules
	r.direction == "INGRESS"
	r.action == "allow"
	pool_field(r, "disabled") != true
	some mt in pool_field(r, "match")
	pool_world_ranges(pool_field(mt, "src_ip_ranges"))
	msg := sprintf("Firewall policy rule in %q allows ingress from anywhere (0.0.0.0/0 or ::/0).", [addr])
}

pool_fw_policy_rules contains [rc.address, r] if {
	some rc in input.resource_changes
	rc.mode == "managed"
	rc.type in {"google_compute_network_firewall_policy_rule", "google_compute_region_network_firewall_policy_rule"}
	r := rc.change.after
	is_object(r)
}

pool_fw_policy_rules contains [rc.address, r] if {
	some rc in input.resource_changes
	rc.mode == "managed"
	rc.type in {"google_compute_network_firewall_policy_with_rules", "google_compute_region_network_firewall_policy_with_rules"}
	is_object(rc.change.after)
	some r in pool_field(rc.change.after, "rule")
}

pool_world_ranges(ranges) if {
	v4 := [s | some s in ranges; not contains(s, ":")]
	some m in net.cidr_merge(v4)
	endswith(m, "/0")
}

pool_world_ranges(ranges) if {
	v6 := [s | some s in ranges; contains(s, ":")]
	some m in net.cidr_merge(v6)
	endswith(m, "/0")
}

pool_world_source(r) if {
	pool_empty(pool_field(r, "source_ranges"))
	pool_empty(pool_field(r, "source_tags"))
	pool_empty(pool_field(r, "source_service_accounts"))
}

# Merged per family, so 0.0.0.0/1 + 128.0.0.0/1 counts as 0.0.0.0/0.
pool_world_source(r) if {
	v4 := [s | some s in pool_field(r, "source_ranges"); not contains(s, ":")]
	some m in net.cidr_merge(v4)
	endswith(m, "/0")
}

pool_world_source(r) if {
	v6 := [s | some s in pool_field(r, "source_ranges"); contains(s, ":")]
	some m in net.cidr_merge(v6)
	endswith(m, "/0")
}
