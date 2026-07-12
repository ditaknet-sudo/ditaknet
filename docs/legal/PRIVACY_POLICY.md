# Privacy Policy — DitakNet

**Status:** Draft — Needs owner and legal review  
**Last Updated:** July 3, 2026  
**Not final legal advice** — Contact support for the current version

## Overview

DitakNet is a self-hosted local network monitoring and IT visibility platform. This policy explains what data DitakNet processes in typical self-hosted deployments and for what purposes.

**Important:** DitakNet processes data required for its features. It does not claim to “collect no data.” In normal self-hosted deployments, customer network and employee data remain on the customer’s own server.

## Data Processing Statement

DitakNet processes data required for local network monitoring, device discovery, alerting, reporting, support, and optional attendance features.

Processed data may include:

- IP address, MAC address, hostname, and device type
- Service status, alert history, and monitoring results
- Local Professional access status and audit logs
- When attendance is enabled: employee name, department, group, shift, and presence/attendance events

## Self-Hosted Data Storage

DitakNet is a self-hosted system. In normal self-hosted deployments, customer data is stored on the customer’s own server or infrastructure.

DitakNet’s vendor does not receive customer network or employee data unless the customer explicitly provides access for technical support or managed service purposes.

## No Data Sale

DitakNet does not sell customer data, does not use it for advertising profiling, and does not provide it to third parties for advertising purposes.

## Employee Attendance Transparency

DitakNet’s employee attendance feature is intended only for presence and working-hours reporting within the workplace or authorized corporate network areas.

DitakNet is not designed for hidden surveillance, personal message inspection, file inspection, GPS tracking, or browsing history monitoring.

The corporate customer is responsible for informing employees in advance and ensuring any required notices, consents, or internal policies are in place.

## Support Access

During technical support, DitakNet’s vendor may access the customer’s environment only with the customer’s explicit permission and only for troubleshooting purposes.

Support access should be temporary, limited, and, where possible, audit logged.

## Corporate Customer Responsibility

The corporate customer is responsible for the use of DitakNet within its organization, including employee notices, internal policies, compliance with personal data protection requirements, and ensuring a lawful basis for monitoring.

## Complimentary Professional Access

Every self-hosted DitakNet installation includes the Professional edition without an activation code, trial period, payment, or expiration date.

The random Installation ID is stored locally so the server can identify its own installation across Docker or TrueNAS restarts and restores. It is not a hardware fingerprint and is not sent to an external licensing service.

## Optional Integrations

### Telegram Notifications (Optional)

If configured, alert messages are sent to your Telegram bot. Your Telegram bot token is stored locally. Telegram’s own privacy policy applies to message transmission.

### Software Updates (Optional)

If enabled, DitakNet may check for updates from configured release endpoints. No customer monitoring data is transmitted during update checks unless explicitly configured otherwise.

## Data Retention

- Network and monitoring data: retained according to your backup and retention policies
- Logs: retained in the server logs directory per configuration
- Backups: managed by the server owner

## Access Control

- DitakNet is accessed via username and password
- Role-based access control can be configured
- HTTPS via reverse proxy is strongly recommended

## Owner Responsibilities

The server owner is responsible for:

- Securing the DitakNet server with a strong admin password
- Backing up the database and data directory
- Restricting network access to authorized users only
- Complying with local employee monitoring and personal data laws
- Providing employee notices where attendance or presence features are used

## Contact

For questions about this policy, contact support.

---

**Disclaimer:** This is a draft document and does not constitute legal advice. Have a qualified lawyer review and customize this policy for your jurisdiction, deployment model, and data practices.

**Armenia note:** Personal data protection requirements may apply under Armenian law (HO-49-N). Final compliance should be confirmed with local legal counsel.
