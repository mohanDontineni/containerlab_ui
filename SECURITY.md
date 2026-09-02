# Security policy

Please do not report vulnerabilities in a public issue. Use GitHub's **Security → Report a vulnerability** workflow for this repository. Include the affected version, impact, reproduction steps, and any suggested mitigation. Do not include live credentials, proprietary network images, or customer configurations.

The project currently supports the latest `main` build and the most recent tagged release. Maintainers will acknowledge a report as soon as practical, validate it privately, prepare a coordinated fix, and publish remediation notes after affected users can upgrade.

ContainerLab Studio can launch privileged network workloads. Run it only in a dedicated, access-controlled Kubernetes cluster; enable Kubernetes secret encryption, network policy enforcement, trusted TLS, backups, and image provenance controls. See [docs/security.md](docs/security.md) for the deployment threat model.
