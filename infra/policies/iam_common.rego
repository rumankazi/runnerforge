package main

import rego.v1

# Every (member, role) a planned IAM resource grants, in any project, for the
# IAM policies (no_basic_roles, pool_principals, pool_orchestrator). Covers the
# google_*_iam_member, _iam_binding and _iam_policy types. Not
# google_project_iam_member_remove: it takes a grant away.
#
# A grant on a resource with no `project` field (a bucket, a service account)
# has project null. A member or role unknown at plan time is null too, and a
# binding whose whole member list is unknown yields one grant with member null,
# so a rule that needs a known member can deny it.
#
# Members are normalized (iam_member_normalized), so compare against lower-case
# emails.

iam_grant_kind(type) := m[0][1] if {
	not endswith(type, "_remove")
	m := regex.find_all_string_submatch_n(`^google_.+_iam_(member|binding|policy)$`, type, 1)
	count(m) == 1
}

iam_grant(rc, a, role, member) := {
	"address": rc.address,
	"type": rc.type,
	"project": object.get(a, "project", null),
	"role": role,
	"member": iam_member_normalized(member),
}

# IAM ignores case in the email of user:, serviceAccount:, group: and domain:
# members (and their deleted: forms), so "serviceAccount:Foo@..." is the same
# principal as "serviceAccount:foo@...". principal:, principalSet: and
# principalHierarchy: identifiers are case-sensitive; allUsers and
# allAuthenticatedUsers have no colon.
iam_case_sensitive_member_types := {"principal", "principalSet", "principalHierarchy"}

iam_member_normalized(m) := m if not is_string(m)

iam_member_normalized(m) := m if {
	is_string(m)
	not contains(m, ":")
}

iam_member_normalized(m) := m if {
	is_string(m)
	parts := split(m, ":")
	parts[0] in iam_case_sensitive_member_types
}

iam_member_normalized(m) := sprintf("%s:%s", [kind, lower(substring(m, count(kind) + 1, -1))]) if {
	is_string(m)
	contains(m, ":")
	kind := split(m, ":")[0]
	not kind in iam_case_sensitive_member_types
}

iam_planned(kind) := {rc |
	some rc in input.resource_changes
	rc.mode == "managed"
	iam_grant_kind(rc.type) == kind
	is_object(rc.change.after)
}

iam_grants contains g if {
	some rc in iam_planned("member")
	a := rc.change.after
	g := iam_grant(rc, a, object.get(a, "role", null), object.get(a, "member", null))
}

iam_grants contains g if {
	some rc in iam_planned("binding")
	a := rc.change.after
	some m in iam_binding_members(a)
	g := iam_grant(rc, a, object.get(a, "role", null), m)
}

iam_grants contains g if {
	some rc in iam_planned("policy")
	a := rc.change.after
	is_string(a.policy_data)
	some b in json.unmarshal(a.policy_data).bindings
	some m in b.members
	g := iam_grant(rc, a, b.role, m)
}

iam_binding_members(a) := a.members if is_array(a.members)

iam_binding_members(a) := [null] if not is_array(object.get(a, "members", null))

# The planned `after` of the resource that made a grant.
iam_grant_after(g) := rc.change.after if {
	some rc in input.resource_changes
	rc.address == g.address
	is_object(rc.change.after)
}

# An authoritative policy unknown at plan time yields no grants above, so
# nothing could check it.
deny contains msg if {
	some rc in iam_planned("policy")
	not is_string(object.get(rc.change.after, "policy_data", null))
	msg := sprintf("%q has policy_data unknown at plan time; the IAM policies can't check it.", [rc.address])
}
