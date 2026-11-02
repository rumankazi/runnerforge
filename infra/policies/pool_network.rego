package main

import rego.v1

# Pool network (ADR-020): one custom-mode VPC, IPv4 only, and every subnet
# range inside pool_internal_ranges, so the internal-range deny also covers
# VM-to-VM traffic. A range Terraform can't know yet (from a reserved internal
# range, say) fails: it can't be checked.

pool_inside_internal(cidr) if {
	some rng in pool_internal_ranges
	net.cidr_contains(rng, cidr)
}

pool_networks contains rc if {
	some rc in pool_planned
	rc.type == "google_compute_network"
}

pool_subnets contains rc if {
	some rc in pool_planned
	rc.type == "google_compute_subnetwork"
}

# The firewall policies group rules by project, which is sound only while the
# pool has a single network. A second one needs those policies to group by network.
deny contains msg if {
	count(pool_networks) > 1
	msg := sprintf(
		"The pool project would have %d networks (%v). The pool firewall policies assume one.",
		[count(pool_networks), sort([rc.address | some rc in pool_networks])],
	)
}

# Auto mode creates a subnet per region that the plan never shows.
deny contains msg if {
	some rc in pool_networks
	pool_field(rc.change.after, "auto_create_subnetworks") != false
	msg := sprintf("Pool network %q must set auto_create_subnetworks = false.", [rc.address])
}

deny contains msg if {
	some rc in pool_networks
	pool_field(rc.change.after, "enable_ula_internal_ipv6") == true
	msg := sprintf("Pool network %q enables internal IPv6. The pool is IPv4 only.", [rc.address])
}

deny contains msg if {
	some rc in pool_subnets
	pool_field(rc.change.after, "stack_type") != "IPV4_ONLY"
	msg := sprintf(
		"Pool subnet %q has stack_type %v. Set stack_type = \"IPV4_ONLY\" explicitly.",
		[rc.address, pool_field(rc.change.after, "stack_type")],
	)
}

deny contains msg if {
	some rc in pool_subnets
	not pool_empty(pool_field(rc.change.after, "ipv6_access_type"))
	msg := sprintf("Pool subnet %q sets ipv6_access_type. The pool is IPv4 only.", [rc.address])
}

deny contains msg if {
	some rc in pool_subnets
	cidr := pool_field(rc.change.after, "ip_cidr_range")
	not pool_known_internal(cidr)
	msg := sprintf("Pool subnet %q primary range %v is unknown or outside the internal ranges.", [rc.address, cidr])
}

deny contains msg if {
	some rc in pool_subnets
	some sec in pool_field(rc.change.after, "secondary_ip_range")
	cidr := pool_field(sec, "ip_cidr_range")
	not pool_known_internal(cidr)
	msg := sprintf(
		"Pool subnet %q secondary range %q (%v) is unknown or outside the internal ranges.",
		[rc.address, pool_field(sec, "range_name"), cidr],
	)
}

pool_known_internal(cidr) if {
	is_string(cidr)
	pool_inside_internal(cidr)
}
