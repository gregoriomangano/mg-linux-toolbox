"""
Tests for core.software_repo.repo_scanner — read-only repository
detection for APT (one-line + deb822), DNF, Pacman (incl. Include=,
excl. AUR-as-a-repo), Zypper, and credential redaction. Every test
writes fixtures under a tmp dir — nothing here ever reads /etc.
"""
import os
import tempfile
import unittest

from core.software_repo import repo_scanner as rs


class RedactCredentialsTests(unittest.TestCase):
    def test_strips_userinfo(self):
        self.assertEqual(
            rs.redact_credentials("https://user:secret@example.com/repo"),
            "https://***@example.com/repo",
        )

    def test_masks_sensitive_query_params(self):
        out = rs.redact_credentials("https://example.com/repo?token=abc123&ok=1")
        self.assertIn("token=***", out)
        self.assertIn("ok=1", out)

    def test_leaves_plain_url_untouched(self):
        self.assertEqual(rs.redact_credentials("https://deb.debian.org/debian"), "https://deb.debian.org/debian")

    def test_empty_string_is_safe(self):
        self.assertEqual(rs.redact_credentials(""), "")

    def test_never_raises_on_garbage_input(self):
        rs.redact_credentials("not a url at all :::")


class AptOnelineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.list_d = os.path.join(self._tmp.name, "sources.list.d")
        os.makedirs(self.list_d)
        self.sources_list = os.path.join(self._tmp.name, "sources.list")

    def _write(self, path, content):
        with open(path, "w") as f:
            f.write(content)

    def test_official_ubuntu_mirror_is_official(self):
        self._write(self.sources_list, "deb http://archive.ubuntu.com/ubuntu noble main restricted\n")
        entries = rs.scan_apt(self.sources_list, self.list_d)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].kind, rs.KIND_OFFICIAL)

    def test_universe_component_stays_official_not_universal(self):
        """2026-08-05: "Universale" is reserved for genuinely
        cross-platform sources (Flathub) — an Ubuntu Universe component
        is still first-party Ubuntu content from the official host."""
        self._write(self.sources_list, "deb http://archive.ubuntu.com/ubuntu noble universe\n")
        entries = rs.scan_apt(self.sources_list, self.list_d)
        self.assertEqual(entries[0].kind, rs.KIND_OFFICIAL)

    def test_ppa_is_external(self):
        self._write(self.sources_list, "deb http://ppa.launchpad.net/someppa/ppa/ubuntu noble main\n")
        entries = rs.scan_apt(self.sources_list, self.list_d)
        self.assertEqual(entries[0].kind, rs.KIND_EXTERNAL)

    def test_comment_and_deb_src_lines_are_skipped_or_parsed(self):
        self._write(self.sources_list,
                    "# a comment\ndeb-src http://deb.debian.org/debian bookworm main\n")
        entries = rs.scan_apt(self.sources_list, self.list_d)
        self.assertEqual(len(entries), 1)

    def test_credentials_in_uri_are_redacted(self):
        self._write(self.sources_list, "deb https://user:pw@mirror.example.com/repo noble main\n")
        entries = rs.scan_apt(self.sources_list, self.list_d)
        self.assertNotIn("pw", entries[0].uri)

    def test_duplicate_entries_across_files_are_flagged(self):
        self._write(self.sources_list, "deb http://deb.debian.org/debian bookworm main\n")
        self._write(os.path.join(self.list_d, "extra.list"),
                    "deb http://deb.debian.org/debian bookworm main\n")
        entries = rs.scan_apt(self.sources_list, self.list_d)
        self.assertEqual(len(entries), 2)
        self.assertTrue(all(rs.WARNING_DUPLICATE_CONFIG in e.warnings for e in entries))


class AptRealWorldDedupAndNamingTests(unittest.TestCase):
    """2026-08-05 fixes: a deb822 stanza listing several Suites for the
    SAME repo (Pop!_OS's own system.sources: 'noble noble-security
    noble-updates noble-backports') must become ONE logical row, not
    one per suite — and a one-line file with no X-Repolib-Name must
    never show the bare suite ("noble"/"stable") as its name."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.list_d = os.path.join(self._tmp.name, "sources.list.d")
        os.makedirs(self.list_d)
        self.sources_list = os.path.join(self._tmp.name, "sources.list")

    def test_multi_suite_stanza_collapses_to_one_entry(self):
        content = (
            "X-Repolib-Name: Pop_OS System Sources\n"
            "Enabled: yes\n"
            "Types: deb deb-src\n"
            "URIs: http://apt.pop-os.org/ubuntu\n"
            "Suites: noble noble-security noble-updates noble-backports\n"
            "Components: main restricted universe multiverse\n"
            "Signed-By: /etc/apt/trusted.gpg.d/ubuntu-keyring-2018-archive.gpg\n"
        )
        with open(os.path.join(self.list_d, "system.sources"), "w") as f:
            f.write(content)
        entries = rs.scan_apt(self.sources_list, self.list_d)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].name, "Pop!_OS System Sources")
        self.assertEqual(set(entries[0].suites),
                          {"noble", "noble-security", "noble-updates", "noble-backports"})

    def test_pop_os_repo_is_official_not_universal(self):
        """A component list containing universe/multiverse must NOT
        make an official-host repo 'Universale' — that label is
        reserved for genuinely cross-platform sources like Flathub."""
        content = (
            "X-Repolib-Name: Pop_OS System Sources\n"
            "Types: deb\nURIs: http://apt.pop-os.org/ubuntu\n"
            "Suites: noble\nComponents: main restricted universe multiverse\n"
        )
        with open(os.path.join(self.list_d, "system.sources"), "w") as f:
            f.write(content)
        entries = rs.scan_apt(self.sources_list, self.list_d)
        self.assertEqual(entries[0].kind, rs.KIND_OFFICIAL)
        self.assertNotEqual(entries[0].kind, rs.KIND_UNIVERSAL)

    def test_pop_os_underscore_name_is_prettified(self):
        content = "X-Repolib-Name: Pop_OS Applications\nTypes: deb\nURIs: http://apt.pop-os.org/proprietary\nSuites: noble\nComponents: main\n"
        with open(os.path.join(self.list_d, "pop-os-apps.sources"), "w") as f:
            f.write(content)
        entries = rs.scan_apt(self.sources_list, self.list_d)
        self.assertEqual(entries[0].name, "Pop!_OS Applications")

    def test_oneline_file_without_repolib_name_uses_filename_not_suite(self):
        """Real repro: docker.list has no X-Repolib-Name, so the old
        code fell back to the bare suite token ('noble') as the name."""
        with open(os.path.join(self.list_d, "docker.list"), "w") as f:
            f.write("deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] "
                     "https://download.docker.com/linux/ubuntu noble stable\n")
        entries = rs.scan_apt(self.sources_list, self.list_d)
        self.assertEqual(entries[0].name, "Docker")
        self.assertNotEqual(entries[0].name, "noble")

    def test_two_different_files_with_identical_config_are_flagged_duplicate(self):
        line = "deb http://deb.debian.org/debian bookworm main\n"
        with open(os.path.join(self.list_d, "a.list"), "w") as f:
            f.write(line)
        with open(os.path.join(self.list_d, "b.list"), "w") as f:
            f.write(line)
        entries = rs.scan_apt(self.sources_list, self.list_d)
        self.assertEqual(len(entries), 2)
        for e in entries:
            self.assertIn(rs.WARNING_DUPLICATE_CONFIG, e.warnings)
            self.assertEqual(len(e.duplicate_files), 1)

    def test_same_repo_different_suites_in_different_files_are_not_flagged_duplicate(self):
        """Two release channels of the same host are legitimately
        different repos, not a 'duplicate configuration' problem."""
        with open(os.path.join(self.list_d, "a.list"), "w") as f:
            f.write("deb http://deb.debian.org/debian bookworm main\n")
        with open(os.path.join(self.list_d, "b.list"), "w") as f:
            f.write("deb http://deb.debian.org/debian bookworm-backports main\n")
        entries = rs.scan_apt(self.sources_list, self.list_d)
        for e in entries:
            self.assertNotIn(rs.WARNING_DUPLICATE_CONFIG, e.warnings)


class AptDeb822Tests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.list_d = os.path.join(self._tmp.name, "sources.list.d")
        os.makedirs(self.list_d)
        self.sources_list = os.path.join(self._tmp.name, "sources.list")  # doesn't need to exist

    def test_parses_deb822_stanza(self):
        content = (
            "Types: deb\n"
            "URIs: http://deb.debian.org/debian\n"
            "Suites: bookworm\n"
            "Components: main\n"
            "Signed-By: /usr/share/keyrings/debian.gpg\n"
            "Enabled: yes\n"
        )
        with open(os.path.join(self.list_d, "debian.sources"), "w") as f:
            f.write(content)
        entries = rs.scan_apt(self.sources_list, self.list_d)
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0].enabled)
        self.assertTrue(entries[0].signed)

    def test_enabled_no_is_respected(self):
        content = "Types: deb\nURIs: http://example.com/repo\nSuites: stable\nComponents: main\nEnabled: no\n"
        with open(os.path.join(self.list_d, "disabled.sources"), "w") as f:
            f.write(content)
        entries = rs.scan_apt(self.sources_list, self.list_d)
        self.assertFalse(entries[0].enabled)

    def test_missing_signed_by_is_flagged(self):
        content = "Types: deb\nURIs: http://example.com/repo\nSuites: stable\nComponents: main\n"
        with open(os.path.join(self.list_d, "unsigned.sources"), "w") as f:
            f.write(content)
        entries = rs.scan_apt(self.sources_list, self.list_d)
        self.assertIn("signature_unspecified", entries[0].warnings)


class DnfTests(unittest.TestCase):
    def test_official_and_rpmfusion_and_gpgcheck_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "fedora.repo"), "w") as f:
                f.write("[fedora]\nname=Fedora $releasever\nbaseurl=https://mirror/fedora\nenabled=1\ngpgcheck=1\n")
            with open(os.path.join(tmp, "rpmfusion-free.repo"), "w") as f:
                f.write("[rpmfusion-free]\nname=RPM Fusion Free\nbaseurl=https://mirror/rpmfusion\nenabled=1\ngpgcheck=0\n")
            entries = rs.scan_dnf(tmp)
        by_name = {e.name: e for e in entries}
        self.assertEqual(by_name["Fedora $releasever"].kind, rs.KIND_OFFICIAL)
        self.assertEqual(by_name["RPM Fusion Free"].kind, rs.KIND_COMMUNITY)
        self.assertIn("gpgcheck_disabled", by_name["RPM Fusion Free"].warnings)

    def test_no_uri_is_needs_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "broken.repo"), "w") as f:
                f.write("[broken]\nname=Broken\nenabled=1\n")
            entries = rs.scan_dnf(tmp)
        self.assertEqual(entries[0].kind, rs.KIND_NEEDS_REVIEW)

    def test_copr_repo_is_never_official_even_though_its_id_contains_fedora(self):
        """2026-08-05: real bug found on a Fedora 44 VM — a Copr repo
        (Fedora's own PPA-equivalent, third-party/community, never
        vetted) was classified "Ufficiale" purely because its id
        ("copr:copr.fedorainfracloud.org:phracek:PyCharm") happens to
        contain the substring "fedora"."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "_copr:copr.fedorainfracloud.org:phracek:PyCharm.repo"), "w") as f:
                f.write(
                    "[copr:copr.fedorainfracloud.org:phracek:PyCharm]\n"
                    "name=Copr repo for PyCharm owned by phracek\n"
                    "baseurl=https://download.copr.fedorainfracloud.org/results/phracek/PyCharm/fedora-$releasever-$basearch/\n"
                    "enabled=1\ngpgcheck=1\n"
                )
            entries = rs.scan_dnf(tmp)
        self.assertEqual(entries[0].kind, rs.KIND_EXTERNAL)
        self.assertNotEqual(entries[0].kind, rs.KIND_OFFICIAL)

    def test_debuginfo_and_source_variants_of_an_official_repo_stay_official(self):
        """Real bug: [updates-testing-debuginfo] doesn't literally
        contain "fedora" in its id, so it used to fall through to
        "Esterno" even though it's exactly as official as
        [updates-testing] itself."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "fedora-updates-testing.repo"), "w") as f:
                f.write(
                    "[updates-testing]\nname=Fedora Test Updates\nbaseurl=https://mirror/updates-testing\nenabled=1\n\n"
                    "[updates-testing-debuginfo]\nname=Fedora Test Updates Debug\nbaseurl=https://mirror/debug\nenabled=0\n\n"
                    "[updates-testing-source]\nname=Fedora Test Updates Source\nbaseurl=https://mirror/source\nenabled=0\n"
                )
            entries = rs.scan_dnf(tmp)
        for e in entries:
            self.assertEqual(e.kind, rs.KIND_OFFICIAL, e.name)


class PacmanTests(unittest.TestCase):
    def test_official_repos_recognized(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf = os.path.join(tmp, "pacman.conf")
            with open(conf, "w") as f:
                f.write("[options]\nArchitecture = auto\n\n[core]\nSigLevel = Required\nServer = https://mirror/core\n")
            entries = rs.scan_pacman(conf)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].kind, rs.KIND_OFFICIAL)

    def test_aur_section_is_never_a_normal_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf = os.path.join(tmp, "pacman.conf")
            with open(conf, "w") as f:
                f.write("[aur]\nSigLevel = Never\nServer = https://aur.example.com\n")
            entries = rs.scan_pacman(conf)
        self.assertEqual(entries[0].kind, rs.KIND_NEEDS_REVIEW)
        self.assertIn("aur_is_not_a_binary_repo", entries[0].warnings)

    def test_include_directive_is_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf = os.path.join(tmp, "pacman.conf")
            include_dir = os.path.join(tmp, "repos.d")
            os.makedirs(include_dir)
            with open(os.path.join(include_dir, "chaotic-aur.conf"), "w") as f:
                f.write("[chaotic-aur]\nInclude = /etc/pacman.d/chaotic-mirrorlist\n")
            with open(conf, "w") as f:
                f.write(f"[options]\n\n[core]\nServer = https://mirror/core\n\nInclude = {include_dir}/*.conf\n")
            entries = rs.scan_pacman(conf)
        names = {e.name for e in entries}
        self.assertIn("chaotic-aur", names)

    def test_no_infinite_loop_on_self_referencing_include(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf = os.path.join(tmp, "pacman.conf")
            with open(conf, "w") as f:
                f.write(f"[core]\nServer = https://mirror/core\nInclude = {conf}\n")
            entries = rs.scan_pacman(conf)  # must return, not hang
        self.assertTrue(any(e.name == "core" for e in entries))


class ZypperTests(unittest.TestCase):
    def test_oss_repo_is_official_packman_is_community(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "oss.repo"), "w") as f:
                f.write("[repo-oss]\nname=Main Repository\nbaseurl=https://download.opensuse.org/oss\nenabled=1\ngpgcheck=1\n")
            with open(os.path.join(tmp, "packman.repo"), "w") as f:
                f.write("[packman]\nname=Packman\nbaseurl=https://ftp.gwdg.de/packman\nenabled=1\ngpgcheck=1\n")
            entries = rs.scan_zypper(tmp)
        by_name = {e.name: e for e in entries}
        self.assertEqual(by_name["Main Repository"].kind, rs.KIND_OFFICIAL)
        self.assertEqual(by_name["Packman"].kind, rs.KIND_COMMUNITY)

    def test_update_repo_is_official_even_when_alias_says_nothing_about_it(self):
        """2026-08-05: real bug found on a Tumbleweed KDE VM — alias
        "download.opensuse.org-tumbleweed" contains no "update"
        substring at all (only the localized name= field does), so it
        was classified "Esterno" despite being download.opensuse.org's
        own official update repository."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "update.repo"), "w") as f:
                f.write(
                    "[download.opensuse.org-tumbleweed]\n"
                    "name=Repository principale degli aggiornamenti\n"
                    "baseurl=http://download.opensuse.org/update/tumbleweed/\nenabled=1\ngpgcheck=1\n"
                )
            entries = rs.scan_zypper(tmp)
        self.assertEqual(entries[0].kind, rs.KIND_OFFICIAL)

    def test_openh264_official_codec_repo_is_official(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "openh264.repo"), "w") as f:
                f.write(
                    "[repo-openh264]\nname=Open H.264 Codec\n"
                    "baseurl=http://codecs.opensuse.org/openh264/openSUSE_Tumbleweed\nenabled=1\ngpgcheck=1\n"
                )
            entries = rs.scan_zypper(tmp)
        self.assertEqual(entries[0].kind, rs.KIND_OFFICIAL)

    def test_disabled_repo_has_enabled_false(self):
        """Repository with enabled=0 must be marked as disabled."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "external.repo"), "w") as f:
                f.write(
                    "[external-repo]\nname=External Repo\n"
                    "baseurl=https://example.com/repo\nenabled=0\ngpgcheck=1\n"
                )
            entries = rs.scan_zypper(tmp)
        self.assertEqual(len(entries), 1)
        self.assertFalse(entries[0].enabled)

    def test_alias_is_captured_from_section_id(self):
        """Zypper section ID ([repo-oss]) must be captured as alias."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "oss.repo"), "w") as f:
                f.write("[repo-oss]\nname=Main Repository\nbaseurl=https://download.opensuse.org/oss\nenabled=1\ngpgcheck=1\n")
            entries = rs.scan_zypper(tmp)
        self.assertEqual(entries[0].alias, "repo-oss")

    def test_obs_project_repo_is_recognized_and_not_official(self):
        """A real OBS project repo (e.g. added from build.opensuse.org)
        is served from the SAME host as the official base repos, under
        /repositories/<project>/<repo>/ — it must be flagged is_obs and
        must NOT be classified as official, since it's third-party,
        project-owner-signed content."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "home_someuser.repo"), "w") as f:
                f.write(
                    "[home_someuser]\nname=home:someuser\n"
                    "baseurl=https://download.opensuse.org/repositories/home:/someuser/openSUSE_Tumbleweed/\n"
                    "enabled=1\ngpgcheck=1\n"
                )
            entries = rs.scan_zypper(tmp)
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0].is_obs)
        self.assertEqual(entries[0].kind, rs.KIND_COMMUNITY)

    def test_base_distro_repo_on_the_obs_host_is_still_official_and_not_obs(self):
        """The official OSS repo lives on the same host as OBS projects
        but under a different path — it must stay official and must
        NOT be flagged as an OBS project repo."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "oss.repo"), "w") as f:
                f.write(
                    "[repo-oss]\nname=Main Repository\n"
                    "baseurl=http://download.opensuse.org/tumbleweed/repo/oss/\nenabled=1\ngpgcheck=1\n"
                )
            entries = rs.scan_zypper(tmp)
        self.assertEqual(entries[0].kind, rs.KIND_OFFICIAL)
        self.assertFalse(entries[0].is_obs)

    def test_non_obs_repo_defaults_is_obs_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "packman.repo"), "w") as f:
                f.write("[packman]\nname=Packman\nbaseurl=https://ftp.gwdg.de/packman\nenabled=1\ngpgcheck=1\n")
            entries = rs.scan_zypper(tmp)
        self.assertFalse(entries[0].is_obs)


class ScanAllTests(unittest.TestCase):
    def test_summary_counts_are_consistent(self):
        result = rs.scan_all("unknown-family", include_flatpak=False)
        self.assertEqual(result["entries"], [])
        self.assertEqual(result["summary"]["official_active"], 0)


if __name__ == "__main__":
    unittest.main()
