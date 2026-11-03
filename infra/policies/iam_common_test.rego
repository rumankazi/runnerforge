package main

import rego.v1

test_member_normalization if {
	iam_member_normalized("serviceAccount:Foo@Example.iam.gserviceaccount.com") == "serviceAccount:foo@example.iam.gserviceaccount.com"
	iam_member_normalized("user:Owner@Example.com") == "user:owner@example.com"
	iam_member_normalized("group:Ops@Example.com") == "group:ops@example.com"
	iam_member_normalized("domain:Example.COM") == "domain:example.com"
	iam_member_normalized("deleted:serviceAccount:Foo@x.iam.gserviceaccount.com?uid=1") == "deleted:serviceaccount:foo@x.iam.gserviceaccount.com?uid=1"
	iam_member_normalized("principalSet://iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/P/attribute.repository/Org/Repo") == "principalSet://iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/P/attribute.repository/Org/Repo"
	iam_member_normalized("principal://iam.googleapis.com/X") == "principal://iam.googleapis.com/X"
	iam_member_normalized("principalHierarchy://X") == "principalHierarchy://X"
	iam_member_normalized("allUsers") == "allUsers"
	iam_member_normalized("allAuthenticatedUsers") == "allAuthenticatedUsers"
	iam_member_normalized(null) == null
}

ic_policy(after, actions) := {
	"address": "google_project_iam_policy.p",
	"mode": "managed",
	"type": "google_project_iam_policy",
	"change": {"actions": actions, "after": after},
}

test_unknown_policy_data_fails if {
	some msg in deny with input as {"resource_changes": [ic_policy({"project": "runnerforge", "policy_data": null}, ["create"])]}
	msg == "\"google_project_iam_policy.p\" has policy_data unknown at plan time; the IAM policies can't check it."
}

test_known_policy_data_passes if {
	pd := json.marshal({"bindings": [{"role": "roles/logging.viewer", "members": ["user:someone@example.com"]}]})
	count(deny) == 0 with input as {"resource_changes": [ic_policy({"project": "runnerforge", "policy_data": pd}, ["update"])]}
}

test_destroyed_policy_passes if {
	count(deny) == 0 with input as {"resource_changes": [ic_policy(null, ["delete"])]}
}
