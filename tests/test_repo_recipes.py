"""
Tests for core.software_repo.repo_recipes — the closed catalogue of
additional repositories. Covers: guided vs advanced gating (advanced
never runs anything automatically), compatibility filtering, conflict
detection (RPM Fusion vs Negativo17), and that enable/disable only ever
call fixed argv lists, never a GUI-built shell string.
"""
import unittest
from unittest import mock

from core.software_repo import repo_recipes as rr
from core.software_repo.distro_profile import DistroProfile, FAMILY_DEBIAN, FAMILY_FEDORA, \
    SYSTEM_TRADITIONAL, SYSTEM_IMMUTABLE


class RecipesForProfileTests(unittest.TestCase):
    def test_ubuntu_gets_universe_multiverse_and_backports(self):
        profile = DistroProfile(id="ubuntu", family=FAMILY_DEBIAN, system_type=SYSTEM_TRADITIONAL, confident=True)
        ids = {r.id for r in rr.recipes_for_profile(profile)}
        self.assertIn("ubuntu_universe", ids)
        self.assertIn("ubuntu_multiverse", ids)
        self.assertIn("ubuntu_backports", ids)
        self.assertNotIn("debian_backports", ids)

    def test_debian_does_not_get_ubuntu_recipes(self):
        profile = DistroProfile(id="debian", family=FAMILY_DEBIAN, system_type=SYSTEM_TRADITIONAL, confident=True)
        ids = {r.id for r in rr.recipes_for_profile(profile)}
        self.assertIn("debian_backports", ids)
        self.assertNotIn("ubuntu_universe", ids)

    def test_fedora_atomic_gets_no_rpmfusion(self):
        profile = DistroProfile(id="fedora", family=FAMILY_FEDORA, system_type=SYSTEM_IMMUTABLE, confident=True)
        ids = {r.id for r in rr.recipes_for_profile(profile)}
        self.assertNotIn("rpmfusion", ids)

    def test_fedora_traditional_gets_rpmfusion(self):
        profile = DistroProfile(id="fedora", family=FAMILY_FEDORA, system_type=SYSTEM_TRADITIONAL, confident=True)
        ids = {r.id for r in rr.recipes_for_profile(profile)}
        self.assertIn("rpmfusion", ids)


class PlanActivationTests(unittest.TestCase):
    def test_advanced_recipe_is_never_auto_activatable(self):
        profile = DistroProfile(id="fedora", family=FAMILY_FEDORA, system_type=SYSTEM_TRADITIONAL, confident=True)
        plan = rr.plan_activation("negativo17", profile)
        self.assertFalse(plan.allowed)
        self.assertEqual(plan.reason, "recipe_advanced_info_only")

    def test_guided_recipe_on_matching_profile_is_allowed(self):
        profile = DistroProfile(id="ubuntu", family=FAMILY_DEBIAN, system_type=SYSTEM_TRADITIONAL, confident=True)
        plan = rr.plan_activation("ubuntu_universe", profile)
        self.assertTrue(plan.allowed)

    def test_unverified_distro_refuses_every_recipe(self):
        profile = DistroProfile(id="ubuntu", family=FAMILY_DEBIAN, system_type=SYSTEM_TRADITIONAL, confident=False)
        plan = rr.plan_activation("ubuntu_universe", profile)
        self.assertFalse(plan.allowed)
        self.assertEqual(plan.reason, "recipe_distro_unverified")

    def test_incompatible_family_refuses(self):
        profile = DistroProfile(id="fedora", family=FAMILY_FEDORA, system_type=SYSTEM_TRADITIONAL, confident=True)
        plan = rr.plan_activation("ubuntu_universe", profile)
        self.assertFalse(plan.allowed)

    def test_known_conflict_is_detected(self):
        profile = DistroProfile(id="fedora", family=FAMILY_FEDORA, system_type=SYSTEM_TRADITIONAL, confident=True)
        plan = rr.plan_activation("rpmfusion", profile, already_enabled_ids={"negativo17"})
        self.assertFalse(plan.allowed)
        self.assertEqual(plan.reason, "recipe_conflict_detected")
        self.assertIn("negativo17", plan.conflicts)

    def test_unknown_recipe_id_is_refused(self):
        profile = DistroProfile(id="ubuntu", family=FAMILY_DEBIAN, system_type=SYSTEM_TRADITIONAL, confident=True)
        plan = rr.plan_activation("does-not-exist", profile)
        self.assertFalse(plan.allowed)


class EnableRecipeTests(unittest.TestCase):
    def test_enable_universe_calls_fixed_argv(self):
        profile = DistroProfile(id="ubuntu", family=FAMILY_DEBIAN, system_type=SYSTEM_TRADITIONAL, confident=True)
        with mock.patch.object(rr, "run_pkexec_full") as pk_mock:
            pk_mock.return_value = mock.Mock(ok=True, technical_detail=lambda: "")
            result = rr.enable_recipe("ubuntu_universe", profile)
        pk_mock.assert_called_once_with(["add-apt-repository", "-y", "universe"], timeout=mock.ANY, job=None)
        self.assertTrue(result.ok)

    def test_enable_debian_backports_uses_validated_codename(self):
        profile = DistroProfile(id="debian", family=FAMILY_DEBIAN, system_type=SYSTEM_TRADITIONAL,
                                  version_codename="bookworm", confident=True)
        with mock.patch.object(rr, "run_pkexec_full") as pk_mock:
            pk_mock.return_value = mock.Mock(ok=True, technical_detail=lambda: "")
            rr.enable_recipe("debian_backports", profile)
        called_args = pk_mock.call_args[0][0]
        self.assertIn("deb http://deb.debian.org/debian bookworm-backports main", called_args)

    def test_malicious_codename_is_rejected_not_interpolated(self):
        profile = DistroProfile(id="debian", family=FAMILY_DEBIAN, system_type=SYSTEM_TRADITIONAL,
                                  version_codename="bookworm; rm -rf /", confident=True)
        with mock.patch.object(rr, "run_pkexec_full") as pk_mock:
            result = rr.enable_recipe("debian_backports", profile)
        pk_mock.assert_not_called()
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "recipe_codename_unresolved")

    def test_advanced_recipe_enable_never_calls_pkexec(self):
        profile = DistroProfile(id="fedora", family=FAMILY_FEDORA, system_type=SYSTEM_TRADITIONAL, confident=True)
        with mock.patch.object(rr, "run_pkexec_full") as pk_mock:
            result = rr.enable_recipe("negativo17", profile)
        pk_mock.assert_not_called()
        self.assertFalse(result.ok)

    def test_rpmfusion_uses_versioned_urls_and_dnf(self):
        profile = DistroProfile(id="fedora", family=FAMILY_FEDORA, system_type=SYSTEM_TRADITIONAL,
                                  version_id="40", confident=True)
        with mock.patch.object(rr, "run_pkexec_full") as pk_mock:
            pk_mock.return_value = mock.Mock(ok=True, technical_detail=lambda: "")
            rr.enable_recipe("rpmfusion", profile)
        called_args = pk_mock.call_args[0][0]
        self.assertEqual(called_args[0], "dnf")
        self.assertTrue(any("rpmfusion-free-release-40" in a for a in called_args))


if __name__ == "__main__":
    unittest.main()
