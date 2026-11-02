package main

import rego.v1

net_subnet(after) := {
	"address": "google_compute_subnetwork.pool",
	"mode": "managed",
	"type": "google_compute_subnetwork",
	"change": {"actions": ["no-op"], "after": object.union(
		{
			"project": "runnerforge-pool",
			"ip_cidr_range": "10.10.0.0/24",
			"stack_type": "IPV4_ONLY",
			"ipv6_access_type": null,
		},
		after,
	)},
}

net_network(name, after) := {
	"address": sprintf("google_compute_network.%s", [name]),
	"mode": "managed",
	"type": "google_compute_network",
	"change": {"actions": ["no-op"], "after": object.union(
		{"project": "runnerforge-pool", "auto_create_subnetworks": false, "enable_ula_internal_ipv6": false},
		after,
	)},
}

# The firewall rules are not under test here; reuse the pool's real ones.
net_input(changes) := array.concat([fw_deny_internal, fw_allow_web, fw_deny_all], changes)

net_messages(changes) := m if {
	m := deny with input as {"resource_changes": net_input(changes)}
}

net_has(changes, fragment) if {
	some msg in net_messages(changes)
	contains(msg, fragment)
}

test_pool_network_today_passes if {
	count(net_messages([net_network("pool", {}), net_subnet({})])) == 0
}

test_pool_subnet_dual_stack_fails if {
	net_has([net_network("pool", {}), net_subnet({"stack_type": "IPV4_IPV6", "ipv6_access_type": "EXTERNAL"})], "has stack_type IPV4_IPV6")
	net_has([net_network("pool", {}), net_subnet({"stack_type": "IPV4_IPV6", "ipv6_access_type": "EXTERNAL"})], "sets ipv6_access_type")
}

test_pool_subnet_stack_type_unknown_fails if {
	net_has([net_network("pool", {}), net_subnet({"stack_type": null})], "Set stack_type = \"IPV4_ONLY\" explicitly")
}

test_pool_subnet_public_primary_fails if {
	net_has([net_network("pool", {}), net_subnet({"ip_cidr_range": "8.8.8.0/24"})], "primary range 8.8.8.0/24 is unknown or outside")
}

test_pool_subnet_unknown_primary_fails if {
	net_has([net_network("pool", {}), net_subnet({"ip_cidr_range": null})], "primary range null is unknown or outside")
}

test_pool_subnet_secondary_inside_passes if {
	sec := [{"range_name": "pods", "ip_cidr_range": "192.168.0.0/20"}]
	count(net_messages([net_network("pool", {}), net_subnet({"secondary_ip_range": sec})])) == 0
}

test_pool_subnet_secondary_outside_fails if {
	sec := [{"range_name": "pods", "ip_cidr_range": "192.168.0.0/20"}, {"range_name": "svc", "ip_cidr_range": "11.0.0.0/20"}]
	net_has([net_network("pool", {}), net_subnet({"secondary_ip_range": sec})], "secondary range \"svc\" (11.0.0.0/20)")
}

test_pool_subnet_secondary_straddling_fails if {
	# 172.0.0.0/8 overlaps 172.16.0.0/12 but isn't inside it.
	sec := [{"range_name": "wide", "ip_cidr_range": "172.0.0.0/8"}]
	net_has([net_network("pool", {}), net_subnet({"secondary_ip_range": sec})], "secondary range \"wide\"")
}

test_pool_subnet_secondary_unknown_fails if {
	sec := [{"range_name": "pods"}]
	net_has([net_network("pool", {}), net_subnet({"secondary_ip_range": sec})], "secondary range \"pods\" (null)")
}

test_pool_network_auto_mode_fails if {
	net_has([net_network("pool", {"auto_create_subnetworks": true})], "must set auto_create_subnetworks = false")
}

test_pool_network_ula_ipv6_fails if {
	net_has([net_network("pool", {"enable_ula_internal_ipv6": true}), net_subnet({})], "enables internal IPv6")
}

test_pool_second_network_fails if {
	net_has([net_network("pool", {}), net_network("other", {}), net_subnet({})], "would have 2 networks")
}

test_control_subnet_not_checked if {
	s := {"address": "google_compute_subnetwork.packer", "mode": "managed", "type": "google_compute_subnetwork", "change": {"actions": ["create"], "after": {"project": "runnerforge", "ip_cidr_range": "8.8.8.0/24", "stack_type": "IPV4_IPV6"}}}
	count(deny) == 0 with input as {"resource_changes": [s]}
}
