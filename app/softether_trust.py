"""TRUST PINS for third-party installers the panel downloads and runs.

SECURITY CONFIGURATION - review every change to this file.

Nothing downloaded at runtime is executed unless it matches a pin below; an
empty or outdated pin makes the installation fail with an explicit error
instead of running unverified code (fail-closed).

Values verified on 2026-11-11 against
vpngate-client-2026.11.12-build-9807.166150.zip from download.vpngate.jp.
"""

# Authenticode signer of the SoftEther / VPN Gate client installers
# ("SOFTETHER CORPORATION", certificate valid until 2027-12-09). Same value as
# signerThumbprint in tools/softether/bundle.json. Add the new thumbprint here
# when the publisher renews its certificate.
SOFTETHER_SIGNER_THUMBPRINTS = frozenset({
    "7CBDC3BEA44467D7692EBE729DFF2799CB5E448A",
})

# Only hosts the VPN Gate archive may be downloaded from, HTTPS only.
VPNGATE_DOWNLOAD_HOSTS = frozenset({
    "www.vpngate.net",
    "vpngate.net",
    "download.vpngate.jp",
})

# The VPN Gate plug-in DLLs are NOT Authenticode-signed by the publisher, and
# the installer loads them from its own folder: they are pinned by SHA-256.
# When VPN Gate ships a new plug-in build, verify it and add its hash here.
VPNGATE_PLUGIN_SHA256 = {
    "VpnGatePlugin_x64.dll": frozenset({
        "d9ce6f7059e18d822262208001c4815de7a1317c40977b26565874787a20a58c",
    }),
    "VpnGatePlugin_x86.dll": frozenset({
        "738cf8ebd8ac7419df156b2f8f7d7e3082b12cb1e61fe827b66aaaee64d0f915",
    }),
}

# Non-executable files the archive may also contain. Any other entry (for
# example a DLL planted beside the installer) rejects the whole archive.
VPNGATE_DATA_FILES = frozenset({
    "License.txt",
    "xmlrpc.config",
    "VPNGate.db",
    "VPNGate.dat",
})
