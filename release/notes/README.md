# DitakNet versioned release notes

Every published DitakNet version has one tracked Markdown file named
`X.Y.Z.md` (or the complete prerelease version, such as `X.Y.Z-rc.1.md`).
The release workflow embeds that file in the signed update manifest and uses it
as the human-readable part of the GitHub Release.

The file for the version in `VERSION` is a required CI input. Its first line
must be exactly `# DitakNet X.Y.Z`.

Record every user-visible change, including small bug fixes, UI/design changes,
container or TrueNAS packaging changes, security changes, and update/rollback
requirements. Keep work that is not yet assigned to a version under
`CHANGELOG.md` → `Unreleased`, then copy the complete release-specific account
into the new version file before tagging.

Once a version tag is published, its note file is historical evidence. Do not
silently rewrite it; document any correction in a newer release.
