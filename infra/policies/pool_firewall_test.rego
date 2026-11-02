package main

import rego.v1

# Fixtures follow `terraform show -json` of infra/terraform-pool (provider 7.x):
# unset list fields are null, `disabled` is null until set, and a destroyed
# resource has `after: null`.

fw_change(name, actions, after) := {
	"address": sprintf("google_compute_firewall.%s", [name]),
	"mode": "managed",
	"type": "google_compute_firewall",
	"change": {"actions": actions, "after": after},
}

fw_rule(direction, priority, ranges, allow, deny_blocks) := {
	"project": "runnerforge-pool",
	"direction": direction,
	"priority": priority,
	"destination_ranges": ranges,
	"source_ranges": null,
	"source_tags": null,
	"source_service_accounts": null,
	"target_tags": null,
	"target_service_accounts": null,
	"disabled": null,
	"allow": allow,
	"deny": deny_blocks,
}

fw_all := [{"protocol": "all", "ports": []}]

fw_web := [{"protocol": "tcp", "ports": ["80", "443"]}]

fw_internal := [
	"10.0.0.0/8", "100.64.0.0/10", "169.254.0.0/16", "172.16.0.0/12",
	"192.0.0.0/24", "192.0.2.0/24", "192.168.0.0/16", "192.88.99.0/24",
	"198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24", "240.0.0.0/4",
]

fw_network := {
	"address": "google_compute_network.pool",
	"mode": "managed",
	"type": "google_compute_network",
	"change": {"actions": ["no-op"], "after": {"project": "runnerforge-pool", "auto_create_subnetworks": false, "enable_ula_internal_ipv6": false}},
}

# pool_ceilings.rego requires an internal-range deny to log with metadata.
fw_logged(rule) := object.union(rule, {"log_config": [{"metadata": "INCLUDE_ALL_METADATA"}]})

fw_deny_internal := fw_change("deny_internal_egress", ["no-op"], fw_logged(fw_rule("EGRESS", 100, fw_internal, [], fw_all)))

fw_allow_web := fw_change("allow_web_egress", ["no-op"], fw_rule("EGRESS", 200, ["0.0.0.0/0"], fw_web, []))

fw_deny_all := fw_change("deny_all_egress", ["no-op"], fw_rule("EGRESS", 65000, ["0.0.0.0/0"], [], fw_all))

fw_messages(changes) := m if {
	m := deny with input as {"resource_changes": changes}
}

fw_has(changes, fragment) if {
	some msg in fw_messages(changes)
	contains(msg, fragment)
}

test_pool_firewall_today_passes if {
	count(fw_messages([fw_network, fw_deny_internal, fw_allow_web, fw_deny_all])) == 0
}

test_pool_firewall_allow_above_deny_fails if {
	early := fw_change("allow_web_egress", ["update"], fw_rule("EGRESS", 50, ["0.0.0.0/0"], fw_web, []))
	fw_has([fw_network, fw_deny_internal, early, fw_deny_all], "google_compute_firewall.allow_web_egress\" (priority 50) is not outranked")
}

test_pool_firewall_tie_fails if {
	tie := fw_change("allow_web_egress", ["update"], fw_rule("EGRESS", 100, ["0.0.0.0/0"], fw_web, []))
	fw_has([fw_network, fw_deny_internal, tie, fw_deny_all], "(priority 100) is not outranked")
}

test_pool_firewall_deny_deleted_fails if {
	gone := fw_change("deny_internal_egress", ["delete"], null)
	fw_has([fw_network, gone, fw_allow_web, fw_deny_all], "is not outranked")
}

# With no rules at all, the implied allow-egress rule (65535) reaches internal ranges.
test_pool_firewall_no_rules_fails if {
	fw_has([fw_network], "Pool network \"google_compute_network.pool\" has no internal-range deny")
}

# A deny-all below 65535 covers internal ranges against the implied rule.
test_pool_firewall_deny_all_only_passes if {
	count(fw_messages([fw_network, fw_deny_all])) == 0
}

test_pool_firewall_missing_range_named if {
	shrunk := fw_change("deny_internal_egress", ["update"], fw_rule("EGRESS", 100, [r | some r in fw_internal; r != "100.64.0.0/10"], [], fw_all))
	fw_has([fw_network, shrunk, fw_allow_web, fw_deny_all], "[\"100.64.0.0/10\"]")
}

test_pool_firewall_split_deny_and_supernet_pass if {
	a := fw_change("deny_rfc1918", ["create"], fw_logged(fw_rule("EGRESS", 100, ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"], [], fw_all)))
	b := fw_change("deny_rest", ["create"], fw_logged(fw_rule("EGRESS", 110, [r | some r in fw_internal; not r in {"10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"}], [], fw_all)))
	count(fw_messages([fw_network, a, b, fw_allow_web, fw_deny_all])) == 0
}

test_pool_firewall_disabled_deny_does_not_count if {
	off := fw_change("deny_internal_egress", ["update"], object.union(fw_rule("EGRESS", 100, fw_internal, [], fw_all), {"disabled": true}))
	fw_has([fw_network, off, fw_allow_web, fw_deny_all], "is not outranked")
}

test_pool_firewall_tagged_deny_does_not_count if {
	tagged := fw_change("deny_internal_egress", ["update"], object.union(fw_rule("EGRESS", 100, fw_internal, [], fw_all), {"target_tags": ["runner"]}))
	fw_has([fw_network, tagged, fw_allow_web, fw_deny_all], "is not outranked")
}

test_pool_firewall_tcp_only_deny_does_not_count if {
	tcp := fw_change("deny_internal_egress", ["update"], fw_rule("EGRESS", 100, fw_internal, [], [{"protocol": "tcp", "ports": []}]))
	fw_has([fw_network, tcp, fw_allow_web, fw_deny_all], "is not outranked")
}

test_pool_firewall_ingress_allow_fails if {
	ssh := fw_change("allow_iap_ssh", ["create"], object.union(fw_rule("INGRESS", 1000, null, [{"protocol": "tcp", "ports": ["22"]}], []), {"source_ranges": ["35.235.240.0/20"]}))
	fw_has([fw_network, fw_deny_internal, fw_allow_web, fw_deny_all, ssh], "allows ingress. The pool has no ingress allow rules")
}

test_pool_firewall_ingress_deny_passes if {
	d := fw_change("deny_internal_ingress", ["no-op"], object.union(fw_rule("INGRESS", 100, null, [], fw_all), {"source_ranges": fw_internal}))
	count(fw_messages([fw_network, fw_deny_internal, fw_allow_web, fw_deny_all, d])) == 0
}

test_pool_firewall_policy_resource_fails if {
	p := {"address": "google_compute_network_firewall_policy_rule.x", "mode": "managed", "type": "google_compute_network_firewall_policy_rule", "change": {"actions": ["create"], "after": {"project": "runnerforge-pool"}}}
	fw_has([fw_network, fw_deny_internal, fw_allow_web, fw_deny_all, p], "adds a firewall policy to the pool")
}

# Outside the pool: only world-open ingress allows fail.
fw_control(name, extra) := fw_change(name, ["create"], object.union(fw_rule("INGRESS", 1000, null, [{"protocol": "tcp", "ports": ["22"]}], []), object.union({"project": "runnerforge"}, extra)))

test_world_ingress_v4_fails if {
	fw_has([fw_control("ssh", {"source_ranges": ["0.0.0.0/0"]})], "allows ingress from anywhere")
}

test_world_ingress_v6_fails if {
	fw_has([fw_control("ssh", {"source_ranges": ["::/0"]})], "allows ingress from anywhere")
}

test_world_ingress_split_halves_fails if {
	fw_has([fw_control("ssh", {"source_ranges": ["0.0.0.0/1", "128.0.0.0/1"]})], "allows ingress from anywhere")
}

test_world_ingress_no_source_fails if {
	fw_has([fw_control("ssh", {})], "allows ingress from anywhere")
}

test_control_ingress_from_iap_passes if {
	count(fw_messages([fw_control("ssh", {"source_ranges": ["35.235.240.0/20"]})])) == 0
}

test_control_ingress_from_tags_passes if {
	count(fw_messages([fw_control("internal", {"source_tags": ["builder"]})])) == 0
}

test_control_egress_allow_not_checked_by_pool_rules if {
	e := fw_change("egress", ["create"], object.union(fw_rule("EGRESS", 10, ["0.0.0.0/0"], fw_web, []), {"project": "runnerforge"}))
	count(fw_messages([e])) == 0
}

fw_policy_rule(type, project, ranges) := {
	"address": sprintf("%s.x", [type]),
	"mode": "managed",
	"type": type,
	"change": {"actions": ["create"], "after": {
		"project": project, "direction": "INGRESS", "action": "allow", "disabled": false,
		"match": [{"src_ip_ranges": ranges}],
	}},
}

test_world_ingress_policy_rule_fails_in_any_project if {
	fw_has([fw_policy_rule("google_compute_network_firewall_policy_rule", "runnerforge", ["0.0.0.0/0"])], "Firewall policy rule in")
	fw_has([fw_policy_rule("google_compute_region_network_firewall_policy_rule", "runnerforge", ["::/0"])], "Firewall policy rule in")
}

test_world_ingress_policy_with_rules_fails if {
	p := {"address": "google_compute_network_firewall_policy_with_rules.p", "mode": "managed", "type": "google_compute_network_firewall_policy_with_rules", "change": {"actions": ["create"], "after": {
		"project": "runnerforge",
		"rule": [
			{"direction": "INGRESS", "action": "allow", "disabled": false, "match": [{"src_ip_ranges": ["35.235.240.0/20"]}]},
			{"direction": "INGRESS", "action": "allow", "disabled": false, "match": [{"src_ip_ranges": ["0.0.0.0/1", "128.0.0.0/1"]}]},
		],
	}}}
	fw_has([p], "Firewall policy rule in")
}

test_policy_rule_narrow_or_deny_passes if {
	count(fw_messages([fw_policy_rule("google_compute_network_firewall_policy_rule", "runnerforge", ["35.235.240.0/20"])])) == 0
	d := fw_policy_rule("google_compute_network_firewall_policy_rule", "runnerforge", ["0.0.0.0/0"])
	denyr := object.union(d, {"change": {"after": {"action": "deny"}}})
	count(fw_messages([denyr])) == 0
}
