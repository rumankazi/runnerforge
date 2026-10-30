# ------- Job store (ADR-018) ------
# One document per job in `jobs`, written by the orchestrator. Nothing reads or
# writes it until the orchestrator release that sets JOB_STORE=firestore.

resource "google_firestore_database" "runnerforge" {
  project          = data.google_project.current.project_id
  name             = "runnerforge"
  location_id      = "europe-west4"
  type             = "FIRESTORE_NATIVE"
  database_edition = "STANDARD"

  # Both default to off.
  point_in_time_recovery_enablement = "POINT_IN_TIME_RECOVERY_ENABLED"
  delete_protection_state           = "DELETE_PROTECTION_ENABLED"
  # The provider default is ABANDON: a destroy or replace would silently drop
  # the database from state. PREVENT makes it fail instead.
  deletion_policy = "PREVENT"

  depends_on = [google_project_service.enabled]

  lifecycle {
    prevent_destroy = true
  }
}

# TTL on expire_at (queued_at + 90 days), with the field's automatic indexes
# turned off: nothing queries it, and indexing a sequential timestamp caps the
# collection's write rate. Deletion typically lands within 24 h of expiry, so
# readers still filter on expire_at.
#
# Field and index changes on one database conflict with each other (a 409 the
# provider doesn't retry for fields), so the field and both indexes are chained.
resource "google_firestore_field" "jobs_expire_at" {
  project    = data.google_project.current.project_id
  database   = google_firestore_database.runnerforge.name
  collection = "jobs"
  field      = "expire_at"

  ttl_config {}
  index_config {}
}

# Dashboard: recent jobs for an installation.
resource "google_firestore_index" "jobs_by_installation" {
  project    = data.google_project.current.project_id
  database   = google_firestore_database.runnerforge.name
  collection = "jobs"

  fields {
    field_path = "installation_id"
    order      = "ASCENDING"
  }
  fields {
    field_path = "queued_at"
    order      = "DESCENDING"
  }

  depends_on = [google_firestore_field.jobs_expire_at]
}

# Reconciler: open jobs due for a check, and its count().
resource "google_firestore_index" "jobs_due_for_check" {
  project    = data.google_project.current.project_id
  database   = google_firestore_database.runnerforge.name
  collection = "jobs"

  fields {
    field_path = "open"
    order      = "ASCENDING"
  }
  fields {
    field_path = "next_check_at"
    order      = "ASCENDING"
  }

  depends_on = [google_firestore_index.jobs_by_installation]
}

# Read/write on documents in this database only. Firestore has no
# database-level IAM resource, so this is a conditional project binding, in the
# form Firestore's per-database access docs give. Project-level calls (listing
# databases, the console) fail the condition; the orchestrator needs neither.
# The condition is ForceNew: editing it recreates the binding and briefly
# revokes access.
resource "google_project_iam_member" "orchestrator_firestore_user" {
  project = data.google_project.current.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.orchestrator.email}"

  condition {
    title       = "runnerforge-job-store-only"
    description = "Only the runnerforge Firestore database (ADR-018)"
    expression  = "resource.name == \"projects/${data.google_project.current.project_id}/databases/${google_firestore_database.runnerforge.name}\""
  }
}
