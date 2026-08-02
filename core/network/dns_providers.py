"""
Built-in DNS providers. Every one of these is presented with a plain,
non-absolute description — never "always faster", never "the best" — the
choice is left to the user; "automatic" (router/ISP-provided) is always
listed first and is never replaced silently.
"""
from core.network.dns_models import DnsProvider

AUTOMATIC = "automatic"
CLOUDFLARE = "cloudflare"
GOOGLE = "google"
QUAD9 = "quad9"
CUSTOM = "custom"

PROVIDERS = {
    AUTOMATIC: DnsProvider(
        id=AUTOMATIC, name_key="dns_provider_automatic_name", desc_key="dns_provider_automatic_desc",
        ipv4=[], ipv6=[], pro_key="dns_provider_automatic_pro", con_key="dns_provider_automatic_con",
    ),
    CLOUDFLARE: DnsProvider(
        id=CLOUDFLARE, name_key="dns_provider_cloudflare_name", desc_key="dns_provider_cloudflare_desc",
        ipv4=["1.1.1.1", "1.0.0.1"],
        ipv6=["2606:4700:4700::1111", "2606:4700:4700::1001"],
        pro_key="", con_key="",
    ),
    GOOGLE: DnsProvider(
        id=GOOGLE, name_key="dns_provider_google_name", desc_key="dns_provider_google_desc",
        ipv4=["8.8.8.8", "8.8.4.4"],
        ipv6=["2001:4860:4860::8888", "2001:4860:4860::8844"],
        pro_key="", con_key="",
    ),
    QUAD9: DnsProvider(
        id=QUAD9, name_key="dns_provider_quad9_name", desc_key="dns_provider_quad9_desc",
        ipv4=["9.9.9.9", "149.112.112.112"],
        ipv6=["2620:fe::fe", "2620:fe::9"],
        pro_key="", con_key="dns_provider_quad9_con",
    ),
}


def get(provider_id: str) -> "DnsProvider | None":
    return PROVIDERS.get(provider_id)


def all_builtin() -> list:
    return [PROVIDERS[k] for k in (AUTOMATIC, CLOUDFLARE, GOOGLE, QUAD9)]
