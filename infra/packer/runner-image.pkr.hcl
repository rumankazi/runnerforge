# Plugin requirements

packer {
  required_plugins {
    googlecompute = {
      source = "github.com/hashicorp/googlecompute"
      version = "~>1"
    }
  }
}

# Input variabless - can be overridden via -var on cli
variable "project_id" {
  type = string
  description = "GCP project to publish the image to"
  default = "runnerforge"
}

variable "zone" {
  type = string
  description = "Zone where the temporary build VM runs"
  default = "europe-west4-a"
}

# Builder definition - what kind of machine, where, from what base
source "googlecompute" "runnerforge_runner" {
  project_id = var.project_id
  source_image_family = "debian-12"   # base we start FROM
  source_image_project_id = ["debian-cloud"]  # where the base lives
  zone = var.zone
  machine_type = "e2-medium"  # temp build VM size (small=cheap)
  ssh_username = "packer" # SSH user Packer uses during build
  disk_size = 10

  # Output image naming
  image_name = "runnerforge-runner-{{timestamp}}" # unique per build (Unix timestamp)
  image_family = "runnerforge-runner"
}

# Build recipe - ties source(s) to provisioner(s). No provisioners yet.
build {
  name = "runnerforge-runner"
  sources = ["source.googlecompute.runnerforge_runner"]
  # Provisioners (to be added for installing runner binary, Docker, etc.)

  provisioner "shell" {
    script    = "scripts/install-runner.sh"
    execute_command = "sudo bash '{{ .Path }}'" # script needs root
  }
}
