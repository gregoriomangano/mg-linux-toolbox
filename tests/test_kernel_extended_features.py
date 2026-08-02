"""
Tests for the Fase-Kernel-successiva features: Turbo Boost, CPU Governor,
EPP, THP, ZRAM (redesigned, no package) and Zswap. Same fake-/proc-and-
/sys approach as test_kernel_features.py — nothing here touches the real
machine except the PrivWriter tests, which use tempfile-based path
overrides exactly like the existing SwappinessWriter/IOSchedulerWriter
tests do.
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.kernel_features.base import SupportStatus
from core.kernel_features.cpu import TurboBoostFeature, GovernorFeature, EPPFeature
from core.kernel_features.memory import THPFeature, ZramFeature, ZswapFeature
from core import priv_writer
from core.persistence.rollback_store import JsonStateStore, FeatureRecord


class FakeRootTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.proc_root = os.path.join(self.tmp, "proc")
        self.sys_root = os.path.join(self.tmp, "sys")
        os.makedirs(self.proc_root)
        os.makedirs(self.sys_root)

    def tearDown(self):
        for root, dirs, files in os.walk(self.tmp):
            for name in dirs + files:
                try:
                    os.chmod(os.path.join(root, name), 0o755)
                except OSError:
                    pass
        shutil.rmtree(self.tmp, ignore_errors=True)


# ── Turbo Boost ─────────────────────────────────────────────────────────
class TurboBoostTests(FakeRootTestCase):
    def test_unsupported_when_no_path_exists(self):
        f = TurboBoostFeature(sys_root=self.sys_root)
        self.assertEqual(f.probe(), SupportStatus.UNSUPPORTED_HARDWARE)

    def test_intel_no_turbo_mode(self):
        d = os.path.join(self.sys_root, "devices", "system", "cpu", "intel_pstate")
        os.makedirs(d)
        with open(os.path.join(d, "no_turbo"), "w") as fh:
            fh.write("0")  # 0 = turbo enabled
        f = TurboBoostFeature(sys_root=self.sys_root)
        self.assertEqual(f.probe(), SupportStatus.SUPPORTED_RUNTIME)
        r = f.read_current()
        self.assertTrue(r.ok)
        self.assertTrue(r.value)  # enabled

    def test_amd_boost_mode_inverted_polarity(self):
        d = os.path.join(self.sys_root, "devices", "system", "cpu", "cpufreq")
        os.makedirs(d)
        with open(os.path.join(d, "boost"), "w") as fh:
            fh.write("0")  # 0 = turbo DISABLED for this file (opposite of no_turbo)
        f = TurboBoostFeature(sys_root=self.sys_root)
        r = f.read_current()
        self.assertTrue(r.ok)
        self.assertFalse(r.value)  # disabled

    def test_permission_denied(self):
        d = os.path.join(self.sys_root, "devices", "system", "cpu", "intel_pstate")
        os.makedirs(d)
        path = os.path.join(d, "no_turbo")
        with open(path, "w") as fh:
            fh.write("0")
        os.chmod(path, 0o000)
        f = TurboBoostFeature(sys_root=self.sys_root)
        self.assertEqual(f.probe(), SupportStatus.UNAVAILABLE)


# ── CPU Governor (all cores) ────────────────────────────────────────────
class GovernorTests(FakeRootTestCase):
    def _make_core(self, n, governor, available="performance schedutil powersave"):
        # Real layout: /sys/devices/system/cpu/cpufreq/policyN/ (cpuN/cpufreq
        # is normally just a symlink into the policy that core belongs to).
        d = os.path.join(self.sys_root, "devices", "system", "cpu", "cpufreq", f"policy{n}")
        os.makedirs(d)
        with open(os.path.join(d, "scaling_governor"), "w") as f:
            f.write(governor)
        with open(os.path.join(d, "scaling_available_governors"), "w") as f:
            f.write(available)

    def test_unsupported_without_cpufreq(self):
        os.makedirs(os.path.join(self.sys_root, "devices", "system", "cpu"))
        f = GovernorFeature(sys_root=self.sys_root)
        self.assertEqual(f.probe(), SupportStatus.UNSUPPORTED_HARDWARE)

    def test_all_cores_agree(self):
        self._make_core(0, "schedutil")
        self._make_core(1, "schedutil")
        f = GovernorFeature(sys_root=self.sys_root)
        r = f.read_current()
        self.assertTrue(r.ok)
        self.assertEqual(r.value, "schedutil")
        self.assertEqual(f.read_available(), ["performance", "schedutil", "powersave"])

    def test_cores_disagree_reports_mixed(self):
        self._make_core(0, "performance")
        self._make_core(1, "powersave")
        f = GovernorFeature(sys_root=self.sys_root)
        r = f.read_current()
        self.assertTrue(r.ok)
        self.assertEqual(r.value, "mixed")

    def test_validate_rejects_unknown_governor(self):
        self._make_core(0, "schedutil")
        f = GovernorFeature(sys_root=self.sys_root)
        self.assertFalse(f.validate("turbo_ultra_mode"))
        self.assertTrue(f.validate("performance"))


# ── EPP ──────────────────────────────────────────────────────────────────
class EPPTests(FakeRootTestCase):
    def test_unsupported_when_driver_does_not_expose_it(self):
        d = os.path.join(self.sys_root, "devices", "system", "cpu", "cpufreq", "policy0")
        os.makedirs(d)
        # scaling_governor exists but NOT energy_performance_preference
        with open(os.path.join(d, "scaling_governor"), "w") as f:
            f.write("schedutil")
        f = EPPFeature(sys_root=self.sys_root)
        self.assertEqual(f.probe(), SupportStatus.UNSUPPORTED_HARDWARE)

    def test_supported_reads_dynamic_values_no_fixed_list(self):
        d = os.path.join(self.sys_root, "devices", "system", "cpu", "cpufreq", "policy0")
        os.makedirs(d)
        with open(os.path.join(d, "energy_performance_preference"), "w") as f:
            f.write("balance_performance")
        with open(os.path.join(d, "energy_performance_available_preferences"), "w") as f:
            f.write("default performance balance_performance balance_power power")
        f = EPPFeature(sys_root=self.sys_root)
        self.assertEqual(f.probe(), SupportStatus.SUPPORTED_RUNTIME)
        r = f.read_current()
        self.assertEqual(r.value, "balance_performance")
        self.assertEqual(f.read_available(),
                          ["default", "performance", "balance_performance", "balance_power", "power"])


# ── THP ──────────────────────────────────────────────────────────────────
class THPTests(FakeRootTestCase):
    def _write(self, content):
        d = os.path.join(self.sys_root, "kernel", "mm", "transparent_hugepage")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "enabled"), "w") as f:
            f.write(content)

    def test_unsupported_kernel_when_missing(self):
        f = THPFeature(sys_root=self.sys_root)
        self.assertEqual(f.probe(), SupportStatus.UNSUPPORTED_KERNEL)

    def test_parses_bracketed_current(self):
        self._write("always [madvise] never")
        f = THPFeature(sys_root=self.sys_root)
        r = f.read_current()
        self.assertTrue(r.ok)
        self.assertEqual(r.value["current"], "madvise")
        self.assertEqual(r.value["available"], ["always", "madvise", "never"])

    def test_to_friendly_returns_translation_key_not_raw_value(self):
        """to_friendly() must return an i18n KEY (translated at the UI
        layer), distinct from the raw kernel value — that's what lets the
        row show 'Solo quando richiesto (madvise)' instead of the bare
        technical string, unlike Governor/EPP which pass values through
        unchanged on purpose."""
        f = THPFeature(sys_root=self.sys_root)
        self.assertEqual(f.to_friendly("always"), "thp_choice_always")
        self.assertEqual(f.to_friendly("madvise"), "thp_choice_madvise")
        self.assertEqual(f.to_friendly("never"), "thp_choice_never")
        from core.i18n import T
        self.assertNotEqual(T(f.to_friendly("madvise")), "madvise")


# ── ZRAM (redesigned, no package) ───────────────────────────────────────
class ZramTests(FakeRootTestCase):
    def _write_swaps(self, content):
        with open(os.path.join(self.proc_root, "swaps"), "w") as f:
            f.write(content)

    def test_unsupported_kernel_when_module_truly_unavailable(self):
        self._write_swaps("Filename\t\t\t\tType\n")
        with mock.patch("subprocess.run") as run:
            run.return_value.returncode = 1
            f = ZramFeature(proc_root=self.proc_root, sys_root=self.sys_root)
            self.assertEqual(f.probe(), SupportStatus.UNSUPPORTED_KERNEL)

    def test_supported_when_module_loaded(self):
        os.makedirs(os.path.join(self.sys_root, "block", "zram0"))
        self._write_swaps("Filename\t\t\t\tType\n")
        f = ZramFeature(proc_root=self.proc_root, sys_root=self.sys_root)
        self.assertEqual(f.probe(), SupportStatus.SUPPORTED_RUNTIME)

    def test_read_current_detects_active_zram_swap(self):
        self._write_swaps("Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"
                           "/dev/zram0                             partition\t8388604\t0\t100\n")
        f = ZramFeature(proc_root=self.proc_root, sys_root=self.sys_root)
        r = f.read_current()
        self.assertTrue(r.ok)
        self.assertTrue(r.value)

    def test_read_current_false_when_no_zram_line(self):
        self._write_swaps("Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"
                           "/dev/sda2                               partition\t4194300\t0\t-2\n")
        f = ZramFeature(proc_root=self.proc_root, sys_root=self.sys_root)
        r = f.read_current()
        self.assertTrue(r.ok)
        self.assertFalse(r.value)


class ZramOwnershipTests(FakeRootTestCase):
    """
    The absolute rule from the spec: never treat, offer to modify, or
    restore a ZRAM device this app didn't create itself. These tests
    don't perform any real write — pure ownership-detection logic.
    """
    def setUp(self):
        super().setUp()
        with open(os.path.join(self.proc_root, "swaps"), "w") as f:
            f.write("Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"
                    "/dev/zram0                             partition\t8388604\t0\t100\n")
        # Real zram0 block device present in the fake /sys — makes
        # _module_available() resolve from the fake tree alone, never
        # falling through to a real `modinfo zram` subprocess call
        # (which isn't reliable inside a container: distrobox doesn't
        # mount the host's /lib/modules, so modinfo can fail there even
        # when the host kernel genuinely supports zram).
        os.makedirs(os.path.join(self.sys_root, "block", "zram0"))
        self.state_path = os.path.join(self.tmp, "state.json")

    def _feature(self):
        return ZramFeature(proc_root=self.proc_root, sys_root=self.sys_root,
                            state_store=JsonStateStore(self.state_path))

    def test_active_zram_with_no_record_is_external(self):
        with mock.patch("core.executor.run_command", return_value=(False, "", "")):
            f = self._feature()
            self.assertEqual(f.owner(), ZramFeature.OWNER_EXTERNAL)
            self.assertEqual(f.probe(), SupportStatus.SUPPORTED_READ_ONLY)

    def test_active_zram_matching_our_own_record_is_ours(self):
        store = JsonStateStore(self.state_path)
        store.put(FeatureRecord(feature_id="memory.zram", initial_value=False,
                                 last_applied_value=True, device_id="zram0"))
        f = ZramFeature(proc_root=self.proc_root, sys_root=self.sys_root, state_store=store)
        self.assertEqual(f.owner(), ZramFeature.OWNER_TOOLBOX)
        self.assertEqual(f.probe(), SupportStatus.SUPPORTED_RUNTIME)

    def test_active_zram_recorded_for_a_different_device_is_still_external(self):
        """Our own record exists, but for a device that ISN'T the one
        currently active — must not claim ownership of the active one."""
        store = JsonStateStore(self.state_path)
        store.put(FeatureRecord(feature_id="memory.zram", initial_value=False,
                                 last_applied_value=True, device_id="zram3"))
        with mock.patch("core.executor.run_command", return_value=(False, "", "")):
            f = ZramFeature(proc_root=self.proc_root, sys_root=self.sys_root, state_store=store)
            self.assertEqual(f.owner(), ZramFeature.OWNER_EXTERNAL)

    def test_systemd_zram_generator_detected(self):
        def fake_run(cmd, *a, **k):
            if cmd[:2] == ["systemctl", "is-active"] and "systemd-zram-setup@zram0.service" in cmd:
                return True, "active", ""
            return False, "", ""
        with mock.patch("core.executor.run_command", side_effect=fake_run):
            f = self._feature()
            self.assertEqual(f.owner(), ZramFeature.OWNER_SYSTEMD_GENERATOR)

    def test_zram_tools_detected(self):
        def fake_run(cmd, *a, **k):
            if cmd == ["systemctl", "is-active", "zramswap.service"]:
                return True, "active", ""
            return False, "", ""
        with mock.patch("core.executor.run_command", side_effect=fake_run):
            f = self._feature()
            self.assertEqual(f.owner(), ZramFeature.OWNER_ZRAM_TOOLS)

    def test_apply_temporary_refuses_when_externally_owned(self):
        with mock.patch("core.executor.run_command", return_value=(False, "", "")):
            f = self._feature()
            result = f.apply_temporary(False)
            self.assertFalse(result.ok)
            self.assertEqual(result.friendly_message, "kf_zram_externally_owned")

    def test_restore_refuses_when_externally_owned(self):
        with mock.patch("core.executor.run_command", return_value=(False, "", "")):
            f = self._feature()
            result = f.restore()
            self.assertFalse(result.ok)
            self.assertEqual(result.friendly_message, "kf_zram_externally_owned")

    def test_no_zram_active_owner_is_none(self):
        with open(os.path.join(self.proc_root, "swaps"), "w") as f:
            f.write("Filename\t\t\t\tType\n")
        feature = self._feature()
        self.assertIsNone(feature.owner())


class PrivWriterZramOwnershipTests(unittest.TestCase):
    """Writer-side defense in depth: refuses to touch a device it did not
    create, even if called directly (never trust the unprivileged side
    alone to have already checked)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.swaps_path = os.path.join(self.tmp, "swaps")
        self.state = JsonStateStore(os.path.join(self.tmp, "state.json"))
        self.writer = priv_writer.ZramWriter()
        self.writer.SWAPS_PATH = self.swaps_path

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_refuses_to_disable_device_it_did_not_create(self):
        with open(self.swaps_path, "w") as f:
            f.write("Filename\t\t\t\tType\n/dev/zram0 partition 8388604 0 100\n")
        # No prior record at all — writer has never created anything.
        result = self.writer.apply_temporary("False", None, False, self.state)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "kf_zram_externally_owned")

    def test_device_busy_no_free_device_available(self):
        """All fallback slots already active/occupied and no
        zram-control hot_add path — must fail cleanly, not guess."""
        with open(self.swaps_path, "w") as f:
            f.write("Filename\t\t\t\tType\n")
        self.writer.ZRAM_CONTROL_HOT_ADD = os.path.join(self.tmp, "nonexistent_hot_add")
        self.writer.SYS_BLOCK_BASE = os.path.join(self.tmp, "sys_block_empty")
        os.makedirs(self.writer.SYS_BLOCK_BASE)  # exists, but no zramN subdirs at all
        with mock.patch("core.priv_writer.subprocess.run") as run:
            run.return_value.returncode = 0
            result = self.writer.apply_temporary("True", None, False, self.state)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "kf_err_generic")


# ── Zswap ────────────────────────────────────────────────────────────────
class ZswapTests(FakeRootTestCase):
    def _write(self, content):
        d = os.path.join(self.sys_root, "module", "zswap", "parameters")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "enabled"), "w") as f:
            f.write(content)

    def test_unsupported_kernel_when_missing(self):
        f = ZswapFeature(sys_root=self.sys_root)
        self.assertEqual(f.probe(), SupportStatus.UNSUPPORTED_KERNEL)

    def test_reads_kernel_bool_param_format(self):
        self._write("Y\n")
        f = ZswapFeature(sys_root=self.sys_root)
        r = f.read_current()
        self.assertTrue(r.ok)
        self.assertTrue(r.value)

    def test_reads_disabled(self):
        self._write("N\n")
        f = ZswapFeature(sys_root=self.sys_root)
        r = f.read_current()
        self.assertTrue(r.ok)
        self.assertFalse(r.value)


# ── PrivWriter tests (fake paths, same style as existing writer tests) ──
class PrivWriterTurboBoostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fake_path = os.path.join(self.tmp, "no_turbo")
        with open(self.fake_path, "w") as f:
            f.write("0")  # turbo enabled
        self.state = JsonStateStore(os.path.join(self.tmp, "state.json"))
        self.writer = priv_writer.TurboBoostWriter()
        self.writer.NO_TURBO_PATH = self.fake_path
        self.writer.BOOST_PATH = "/nonexistent"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_disable_turbo(self):
        result = self.writer.apply_temporary("False", None, False, self.state)
        self.assertTrue(result["ok"])
        self.assertFalse(result["value"])
        with open(self.fake_path) as f:
            self.assertEqual(f.read().strip(), "1")  # no_turbo=1 means disabled

    def test_restore(self):
        self.writer.apply_temporary("False", None, False, self.state)
        result = self.writer.restore(None, None, False, self.state)
        self.assertTrue(result["ok"])
        self.assertTrue(result["value"])  # back to enabled
        with open(self.fake_path) as f:
            self.assertEqual(f.read().strip(), "0")


class PrivWriterGovernorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dirs = []
        for n in (0, 1):
            d = os.path.join(self.tmp, f"cpu{n}")
            os.makedirs(d)
            with open(os.path.join(d, "scaling_governor"), "w") as f:
                f.write("schedutil")
            with open(os.path.join(d, "scaling_available_governors"), "w") as f:
                f.write("performance schedutil powersave")
            self.dirs.append(d)
        self.state = JsonStateStore(os.path.join(self.tmp, "state.json"))
        self.writer = priv_writer.GovernorWriter()
        self.writer._dirs = lambda: self.dirs

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_apply_writes_all_cores(self):
        result = self.writer.apply_temporary("performance", None, False, self.state)
        self.assertTrue(result["ok"])
        for d in self.dirs:
            with open(os.path.join(d, "scaling_governor")) as f:
                self.assertEqual(f.read().strip(), "performance")

    def test_rejects_invalid_governor(self):
        result = self.writer.apply_temporary("nonexistent_governor", None, False, self.state)
        self.assertFalse(result["ok"])

    def test_restore(self):
        self.writer.apply_temporary("performance", None, False, self.state)
        result = self.writer.restore(None, None, False, self.state)
        self.assertTrue(result["ok"])
        for d in self.dirs:
            with open(os.path.join(d, "scaling_governor")) as f:
                self.assertEqual(f.read().strip(), "schedutil")


class PrivWriterTHPTests(unittest.TestCase):
    """
    Real sysfs bracket-notation files re-format their own content to show
    the active value in brackets after a write ("always [madvise] never"
    -> write "never" -> "always madvise [never]"). A plain temp file
    can't replicate that kernel-side behaviour (writing "never" just
    leaves the file containing literally "never", no brackets) — same
    issue already documented for the I/O scheduler's writer test, and
    worked around the same way: a stateful fake _read() that still
    exercises validate/write/re-read/record/restore for real.
    """
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fake_path = os.path.join(self.tmp, "enabled")
        with open(self.fake_path, "w") as f:
            f.write("madvise")
        self.state = JsonStateStore(os.path.join(self.tmp, "state.json"))
        self.writer = priv_writer.THPWriter()
        self.writer.PATH = self.fake_path
        fixed_available = ["always", "madvise", "never"]

        def fake_read():
            with open(self.fake_path) as f:
                raw = f.read().strip()
            return fixed_available, raw

        self.writer._read = fake_read

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_apply_and_restore(self):
        result = self.writer.apply_temporary("never", None, False, self.state)
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], "never")
        restored = self.writer.restore(None, None, False, self.state)
        self.assertTrue(restored["ok"])
        self.assertEqual(restored["value"], "madvise")


class PrivWriterZswapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fake_path = os.path.join(self.tmp, "enabled")
        with open(self.fake_path, "w") as f:
            f.write("N")
        self.state = JsonStateStore(os.path.join(self.tmp, "state.json"))
        self.writer = priv_writer.ZswapWriter()
        self.writer.PATH = self.fake_path

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_enable_writes_Y(self):
        result = self.writer.apply_temporary("True", None, False, self.state)
        self.assertTrue(result["ok"])
        with open(self.fake_path) as f:
            self.assertEqual(f.read().strip(), "Y")

    def test_restore_back_to_disabled(self):
        self.writer.apply_temporary("True", None, False, self.state)
        result = self.writer.restore(None, None, False, self.state)
        self.assertTrue(result["ok"])
        with open(self.fake_path) as f:
            self.assertEqual(f.read().strip(), "N")


class PrivWriterZramTests(unittest.TestCase):
    """Exercises the modprobe/mkswap/swapon orchestration with subprocess
    mocked out — this writer is the one exception to "plain file I/O",
    so its tests focus on: it calls the right fixed argv commands, checks
    real state via /proc/swaps, and never silently claims success."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.swaps_path = os.path.join(self.tmp, "swaps")
        with open(self.swaps_path, "w") as f:
            f.write("Filename\t\t\t\tType\n")
        self.meminfo_path = os.path.join(self.tmp, "meminfo")
        with open(self.meminfo_path, "w") as f:
            f.write("MemTotal:        8000000 kB\n")
        self.block_base = os.path.join(self.tmp, "sys_block")
        os.makedirs(os.path.join(self.block_base, "zram0"))
        self.state = JsonStateStore(os.path.join(self.tmp, "state.json"))
        self.writer = priv_writer.ZramWriter()
        self.writer.SWAPS_PATH = self.swaps_path
        self.writer.MEMINFO_PATH = self.meminfo_path
        self.writer.SYS_BLOCK_BASE = self.block_base

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mark_active(self):
        with open(self.swaps_path, "w") as f:
            f.write("Filename\t\t\t\tType\n/dev/zram0 partition 8388604 0 100\n")

    def test_enable_calls_modprobe_mkswap_swapon_in_order(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd[0])
            if cmd[0] == "swapon":
                self._mark_active()
            result = mock.Mock()
            result.returncode = 0
            return result

        with mock.patch("core.priv_writer.subprocess.run", side_effect=fake_run):
            result = self.writer.apply_temporary("True", None, False, self.state)
        self.assertTrue(result["ok"])
        self.assertEqual(calls, ["modprobe", "mkswap", "swapon"])
        with open(os.path.join(self.block_base, "zram0", "disksize")) as f:
            self.assertEqual(f.read().strip(), str(8_000_000 * 1024 // 2))  # MemTotal(kB)*1024 * 0.5

    def test_no_op_when_already_in_desired_state(self):
        result = self.writer.apply_temporary("False", None, False, self.state)
        self.assertTrue(result["ok"])
        self.assertFalse(result["value"])

    def test_write_mismatch_reported_if_swapon_did_not_actually_enable_it(self):
        with mock.patch("subprocess.run") as run:
            run.return_value.returncode = 0
            # subprocess "succeeds" but /proc/swaps never actually shows zram —
            # must not be reported as success just because commands exited 0.
            result = self.writer.apply_temporary("True", None, False, self.state)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "kf_err_write_mismatch")


if __name__ == "__main__":
    unittest.main()
