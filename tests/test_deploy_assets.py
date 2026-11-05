"""Exercise the real asset sync locally: deployment must not replace the selected runtime picture."""
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent


class DeployAssetsTest(unittest.TestCase):
    def test_seed_then_update_static_assets_without_overwriting_or_deleting_runtime_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "source", Path(tmp) / "target"
            source.mkdir()
            target.mkdir()
            runtime = ("bg.png", "art.png", "voice_bg.png", "bg.qoi", "art.qoi", "voice_bg.qoi")
            for name in (*runtime, "orb_atlas_v3.lz4"):
                (source / name).write_bytes(b"default")
            def deploy():
                subprocess.run(["sh", str(ROOT / "tools/deploy_assets.sh"), str(source), str(target)],
                               check=True, capture_output=True)
            deploy()
            for name in runtime:
                self.assertEqual((target / name).read_bytes(), b"default")
                (target / name).write_bytes(b"today's chosen picture")
                (source / name).write_bytes(b"new default, not the chosen picture")
            (target / "apod.json").write_bytes(b"today's title and credit")
            (target / "obsolete.bin").write_bytes(b"obsolete")
            (source / "orb_atlas_v3.lz4").write_bytes(b"new static atlas")
            deploy()
            for name in runtime:
                self.assertEqual((target / name).read_bytes(), b"today's chosen picture")
            self.assertEqual((target / "apod.json").read_bytes(), b"today's title and credit")
            self.assertEqual((target / "orb_atlas_v3.lz4").read_bytes(), b"new static atlas")
            self.assertFalse((target / "obsolete.bin").exists())


class GenSiblingsShippedTest(unittest.TestCase):
    def test_every_module_gen_py_imports_from_its_folder_goes_to_jarvis(self):
        """Jarvis's bake imports gen.py: its sibling modules must be in both copies to Jarvis (the candidate tests'
        stage and the live folder). weather_page.py was missing from the stage and stopped a deploy (2026-10-29)."""
        import re
        glass = ROOT / "styles/glass-v2"
        source = (glass / "gen.py").read_text()
        siblings = {m for m in re.findall(r"^\s*import (\w+)", source, re.M) if (glass / f"{m}.py").is_file()}
        self.assertTrue({"weather_page", "tavla_earth", "now_playing"} <= siblings, siblings)
        for script in ("tools/prepare_image_sampling.sh", "tools/deploy_jarvis.sh"):
            text = (ROOT / script).read_text()
            for module in siblings:
                self.assertIn(f'styles/glass-v2/{module}.py"', text, f"{script} does not ship {module}.py")
