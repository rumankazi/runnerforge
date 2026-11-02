package main

import rego.v1

ce_rc(type, name, after) := {
	"address": sprintf("%s.%s", [type, name]),
	"mode": "managed",
	"type": type,
	"change": {"actions": ["create"], "after": after},
}

# How a zero preference appears in a real plan (see pool_quota_value).
ce_quota(qid) := ce_quota_v(qid, "")

ce_quota_v(qid, v) := ce_rc("google_cloud_quotas_quota_preference", lower(qid), {
	"parent": "projects/runnerforge-pool",
	"quota_id": qid,
	"quota_config": [{"preferred_value": v}],
})

ce_nat(log_config) := ce_rc("google_compute_router_nat", "pool", {"project": "runnerforge-pool", "log_config": log_config})

ce_excl(name, filter) := ce_rc("google_logging_project_exclusion", name, {"project": "runnerforge-pool", "filter": filter, "disabled": null})

ce_dns_excl := ce_excl("dns", `resource.type="dns_query" AND sample(insertId, 0.99)`)

ce_serial_excl := ce_excl("serial", `LOG_ID("serialconsole.googleapis.com/serial_port_1_output") AND NOT textPayload=~"startup-script|google_metadata_script_runner"`)

ce_dns(extra) := ce_rc("google_dns_policy", "pool", object.union(
	{
		"project": "runnerforge-pool",
		"enable_logging": true,
		"enable_inbound_forwarding": false,
		"alternative_name_server_config": [],
		"networks": [{"network_url": "projects/runnerforge-pool/global/networks/runnerforge-pool"}],
	},
	extra,
))

ce_messages(changes) := m if {
	m := deny with input as {"resource_changes": changes}
}

ce_has(changes, fragment) if {
	some msg in ce_messages(changes)
	contains(msg, fragment)
}

test_ceilings_step2_pool_passes if {
	count(ce_messages([
		ce_quota_v("N2-CPUS-per-project-region", "32"),
		ce_quota_v("CPUS-ALL-REGIONS-per-project", "32"),
		ce_quota_v("INSTANCES-per-project-region", "10"),
		ce_quota("C3-CPUS-per-project-region"),
		ce_nat([{"enable": true, "filter": "ALL"}]),
		ce_dns_excl, ce_dns({}),
		fw_deny_internal, fw_allow_web, fw_deny_all, fw_network,
	])) == 0
}

test_preemptible_quota_fails_in_any_project if {
	ce_has([ce_quota("PREEMPTIBLE-CPUS-per-project-region")], "never requests preemptible quota")
	ce_has(
		[ce_rc("google_cloud_quotas_quota_preference", "ctl", {"parent": "projects/runnerforge", "quota_id": "preemptible-nvidia-t4-gpus-per-project-region"})],
		"never requests preemptible quota",
	)
}

test_quota_id_unknown_is_skipped if {
	count(ce_messages([ce_rc("google_cloud_quotas_quota_preference", "x", {"parent": "projects/runnerforge-pool"})])) == 0
}

test_nat_filter_narrowed_fails if {
	ce_has([ce_nat([{"enable": true, "filter": "ERRORS_ONLY"}])], "must keep log_config")
}

test_nat_logging_off_fails if {
	ce_has([ce_nat([{"enable": false, "filter": "ALL"}])], "must keep log_config")
	ce_has([ce_nat([])], "must keep log_config")
	ce_has([ce_nat(null)], "must keep log_config")
}

test_control_nat_unchecked if {
	count(ce_messages([ce_rc("google_compute_router_nat", "ctl", {"project": "runnerforge", "log_config": []})])) == 0
}

test_internal_deny_without_log_fails if {
	unlogged := fw_change("deny_internal_egress", ["no-op"], fw_rule("EGRESS", 100, fw_internal, [], fw_all))
	ce_has([unlogged, fw_allow_web, fw_deny_all, fw_network], "must log with metadata")
}

test_internal_deny_without_metadata_fails if {
	nometa := fw_change("deny_internal_egress", ["no-op"], object.union(
		fw_rule("EGRESS", 100, fw_internal, [], fw_all),
		{"log_config": [{"metadata": "EXCLUDE_ALL_METADATA"}]},
	))
	ce_has([nometa, fw_allow_web, fw_deny_all, fw_network], "must log with metadata")
}

test_world_deny_need_not_log if {
	not ce_has([fw_deny_internal, fw_allow_web, fw_deny_all, fw_network], "must log with metadata")
}

test_exclusion_of_nat_or_firewall_fails if {
	ce_has([ce_excl("nat", `LOG_ID("compute.googleapis.com/nat_flows") AND sample(insertId, 0.9)`)], "never excluded")
	ce_has([ce_excl("fw", `resource.type="gce_subnetwork"`)], "never excluded")
}

test_exclusion_widened_with_or_fails if {
	ce_has([ce_excl("dns", `resource.type="dns_query" AND sample(insertId, 0.99) OR resource.type="nat_gateway"`)], "never excluded")
}

test_exclusion_unscoped_fails if {
	ce_has([ce_excl("all", `severity < ERROR`)], "never excluded")
	ce_has([ce_excl("unknown", null)], "never excluded")
}

test_disabled_exclusion_unchecked if {
	disabled := ce_rc("google_logging_project_exclusion", "off", {"project": "runnerforge-pool", "filter": `LOG_ID("compute.googleapis.com/nat_flows")`, "disabled": true})
	count(ce_messages([disabled])) == 0
}

test_pool_sink_fails if {
	ce_has([ce_rc("google_logging_project_sink", "default", {"project": "runnerforge-pool"})], "changes the pool's log routing")
}

test_dns_logging_off_fails if {
	ce_has([ce_dns({"enable_logging": false}), ce_dns_excl], "must set enable_logging = true")
}

test_dns_inbound_forwarding_fails if {
	ce_has([ce_dns({"enable_inbound_forwarding": true}), ce_dns_excl], "enables inbound forwarding")
}

test_dns_alternative_servers_fail if {
	ce_has(
		[ce_dns({"alternative_name_server_config": [{"target_name_servers": [{"ipv4_address": "1.1.1.1"}]}]}), ce_dns_excl],
		"sets alternative name servers",
	)
}

test_dns_logging_without_sampling_fails if {
	ce_has([ce_dns({})], "no pool exclusion samples")
	ce_has([ce_dns({}), ce_serial_excl], "no pool exclusion samples")
	ce_has([ce_dns({}), ce_excl("dns", `resource.type="dns_query" AND severity < ERROR`)], "no pool exclusion samples")
}

test_dns_sample_fraction_too_low_fails if {
	ce_has([ce_dns({}), ce_excl("dns", `resource.type="dns_query" AND sample(insertId, 0.0)`)], "no pool exclusion samples")
	ce_has([ce_dns({}), ce_excl("dns", `resource.type="dns_query" AND sample(insertId, 0.01)`)], "no pool exclusion samples")
	ce_has([ce_dns({}), ce_excl("dns", `resource.type="dns_query" AND sample(insertId, 0.5)`)], "no pool exclusion samples")
}

test_dns_sample_reshaped_fails if {
	ce_has([ce_dns({}), ce_excl("dns", `resource.type="dns_query" AND NOT sample(insertId, 0.99)`)], "no pool exclusion samples")
	ce_has([ce_dns({}), ce_excl("dns", `resource.type="dns_query" AND sample(insertId, 0.99) AND sample(insertId, 0.1)`)], "no pool exclusion samples")
	ce_has([ce_dns({}), ce_excl("dns", `resource.type="dns_query" AND sample(insertId, 0.99) AND severity>=ERROR`)], "no pool exclusion samples")
}

test_dns_sample_fractions_allowed if {
	not ce_has([ce_dns({}), ce_excl("dns", `resource.type="dns_query" AND sample(insertId, 0.9)`)], "no pool exclusion samples")
	not ce_has([ce_dns({}), ce_dns_excl], "no pool exclusion samples")
	not ce_has([ce_dns({}), ce_excl("dns", `resource.type="dns_query" AND sample(insertId, 1.0)`)], "no pool exclusion samples")
}

test_dns_policy_detached_fails if {
	ce_has([ce_dns({"networks": []}), ce_dns_excl], "is not attached to network runnerforge-pool")
	ce_has([ce_dns({"networks": [{"network_url": "projects/runnerforge/global/networks/default"}]}), ce_dns_excl], "is not attached")
}

test_dns_policy_on_rebuilt_network_passes if {
	rebuilt := object.union(ce_dns({"networks": [{}]}), {"change": {"after_unknown": {"networks": [{"network_url": true}]}}})
	not ce_has([rebuilt, ce_dns_excl], "is not attached")
}

test_pool_dns_zone_fails if {
	ce_has([ce_rc("google_dns_managed_zone", "apis", {"project": "runnerforge-pool"})], "adds a Cloud DNS zone")
	ce_has([ce_rc("google_dns_response_policy", "rp", {"project": "runnerforge-pool"})], "adds a Cloud DNS zone")
}

test_control_zone_bound_to_pool_network_fails if {
	zone := ce_rc("google_dns_managed_zone", "apis", {"project": "runnerforge", "private_visibility_config": [{"networks": [{"network_url": "https://www.googleapis.com/compute/v1/projects/runnerforge-pool/global/networks/runnerforge-pool"}]}]})
	ce_has([zone], "binds a Cloud DNS zone")
	rp := ce_rc("google_dns_response_policy", "rp", {"project": "runnerforge", "networks": [{"network_url": "projects/runnerforge-pool/global/networks/runnerforge-pool"}]})
	ce_has([rp], "binds a Cloud DNS zone")
}

test_control_zone_elsewhere_passes if {
	zone := ce_rc("google_dns_managed_zone", "z", {"project": "runnerforge", "private_visibility_config": [{"networks": [{"network_url": "projects/runnerforge/global/networks/default"}]}]})
	count(ce_messages([zone])) == 0
}

test_pool_quota_above_cap_fails if {
	ce_has([ce_quota_v("INSTANCES-per-project-region", "500")], "the cap in pool_common.rego is 10")
	ce_has([ce_quota_v("N2-CPUS-per-project-region", "33")], "the cap in pool_common.rego is 32")
}

test_pool_quota_unlimited_fails if {
	ce_has([ce_quota_v("N2-CPUS-per-project-region", "-1")], "the cap in pool_common.rego")
}

test_pool_quota_uncapped_id_must_be_zero if {
	ce_has([ce_quota_v("E2-CPUS-per-project-region", "8")], "the cap in pool_common.rego is 0")
	not ce_has([ce_quota_v("E2-CPUS-per-project-region", "0")], "the cap in pool_common.rego")
}

test_pool_quota_by_number_checked if {
	q := ce_rc("google_cloud_quotas_quota_preference", "n", {"parent": "projects/355964341703", "quota_id": "INSTANCES-per-project-region", "quota_config": [{"preferred_value": "11"}]})
	ce_has([q], "the cap in pool_common.rego is 10")
}

test_control_quota_not_capped if {
	q := ce_rc("google_cloud_quotas_quota_preference", "c", {"parent": "projects/runnerforge", "quota_id": "INSTANCES-per-project-region", "quota_config": [{"preferred_value": "24"}]})
	count(ce_messages([q])) == 0
}

test_qv_empty_is_zero if {
	count(ce_messages([ce_quota_v("C3-CPUS-per-project-region", "")])) == 0
}

test_qv_null_is_zero if {
	count(ce_messages([ce_quota_v("C3-CPUS-per-project-region", null)])) == 0
}

test_qv_over_cap_still_denied if {
	count(ce_messages([ce_quota_v("C3-CPUS-per-project-region", "1")])) == 1
}

test_ceilings_serial_exclusion_denied if {
	ce_has([ce_serial_excl], "NAT and firewall logs are never excluded")
}
