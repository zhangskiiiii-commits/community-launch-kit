"""Offline structural tests. Every campaign below is synthetic, not research."""
import base64
import copy
import csv
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "community-launch-kit" / "scripts" / "check_campaign.py"
SPEC = importlib.util.spec_from_file_location("campaign_checker", SCRIPT)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j6p8AAAAASUVORK5CYII=")


def fixture():
    """Fictional URLs have valid shapes but are never fetched or published."""
    url = "https://www.reddit.com/r/skill_fixture_not_real/comments/test01/example/"
    return {
        "fixture": True,
        "product": {"name": "TEST FIXTURE TaskNote", "stage": "beta", "facts": [{"id": "F1", "text": "The fictional product exports a checklist."}]},
        "requested_count": 10,
        "items": [{
            "id": "R01", "platform": "reddit",
            "community": {"name": "TEST FIXTURE — not a real community", "url": "https://www.reddit.com/r/skill_fixture_not_real/"},
            "source": {"thread_url": url, "comment_url": None, "title": "Synthetic need", "published_at": None, "checked_at": "2026-01-01T00:00:00Z", "evidence_status": "read", "thread_state": "active", "problem_summary": "TEST: wants to export a checklist."},
            "product_fit": {"can_help": "F1 supports checklist export.", "cannot_help": "No collaboration feature is claimed."},
            "rules": {"url": "https://www.reddit.com/r/skill_fixture_not_real/about/rules", "checked_at": "2026-01-01T00:00:00Z", "evidence_status": "read", "summary": "SYNTHETIC TEST RULE: disclosed product replies permitted.", "self_promotion": "allowed", "external_links": "allowed", "images": "allowed", "conditions": "", "applies_to_url": url, "applies_to_action": "reply"},
            "recommendation": {"status": "ready", "action": "reply", "destination_url": url, "reason": "Synthetic fit and rules satisfy structural gates.", "conditions_met": False, "manual_steps": ["TEST ONLY — do not publish."]},
            "copy": {"language": "en", "title": None, "body": "I am the maker of TaskNote. It exports a checklist.", "maker_disclosure": "I am the maker of TaskNote.", "cta": None, "fact_ids": ["F1"]},
            "visual": {"status": "text_only", "file": None, "prompt": "", "alt_text": "", "caption": ""}
        }]
    }


class CampaignTests(unittest.TestCase):
    def setUp(self):
        # Test fixtures live under the repository on D: in this workspace, never
        # in the system temp folder. Resolved containment is checked before use.
        self.tmp = tempfile.TemporaryDirectory(prefix=".campaign-test-", dir=ROOT / "tests")
        self.base = Path(self.tmp.name).resolve()
        self.assertTrue(self.base.is_relative_to((ROOT / "tests").resolve()))
        self.addCleanup(self.tmp.cleanup)
        self.data = fixture()
        self.item = self.data["items"][0]

    def validate(self):
        return CHECK.validate_campaign(self.data, self.base)

    def assert_invalid(self, word):
        result = self.validate()
        self.assertTrue(result["errors"], result)
        self.assertIn(word, " ".join(result["errors"]))
        return result

    def image(self, file="images/test.png"):
        target = self.base / file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(PNG)
        self.item["visual"].update(status="generated", file=file, alt_text="Synthetic one-pixel test image", caption="TEST FIXTURE")
        return target

    def test_allowed_record_and_honest_shortfall(self):
        result = self.validate()
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["counts"]["verified_needs"], 1)
        self.assertEqual(result["counts"]["ready"], 1)
        self.assertTrue(any("1 verified unique needs of 10" in w for w in result["warnings"]))
        self.assertTrue(any("TEST FIXTURE" in w for w in result["warnings"]))

    def test_permissions_prohibited_unknown_and_conditional(self):
        for permission in ("prohibited", "unknown", "conditional"):
            with self.subTest(permission=permission):
                self.item["rules"]["self_promotion"] = permission
                self.assert_invalid("ready is invalid")
        self.item["rules"]["conditions"] = "Only the synthetic promotion thread."
        self.item["recommendation"]["conditions_met"] = True
        self.assertEqual(self.validate()["errors"], [])

    def test_unread_body_cannot_be_ready(self):
        for value in ("snippet", "inaccessible"):
            with self.subTest(value=value):
                self.item["source"]["evidence_status"] = value
                result = self.assert_invalid("not verified")
                self.assertEqual(result["counts"]["verified_needs"], 0)

    def test_unread_rules_and_closed_thread_cannot_be_ready(self):
        self.item["rules"]["evidence_status"] = "snippet"
        self.assert_invalid("rules are unread")
        self.item["rules"]["evidence_status"] = "read"
        for state in ("locked", "removed", "unknown"):
            with self.subTest(state=state):
                self.item["source"]["thread_state"] = state
                self.assert_invalid("thread is not active")

    def test_pending_demand_is_not_a_ready_placement(self):
        self.item["recommendation"].update(status="needs_check", action="none", destination_url=None)
        self.item["rules"].update(evidence_status="inaccessible", url=None, self_promotion="unknown")
        result = self.validate()
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["counts"]["verified_needs"], 1)
        self.assertEqual(result["counts"]["ready"], 0)

    def test_background_demand_does_not_fill_current_target(self):
        self.item["source"]["scope"] = "background"
        result = self.assert_invalid("background needs cannot be ready")
        self.assertEqual(result["counts"]["verified_needs"], 0)
        self.assertEqual(result["counts"]["background_needs"], 1)
        self.assertEqual(result["counts"]["ready"], 0)
        self.item["recommendation"].update(status="needs_check", action="none", destination_url=None)
        result = self.validate()
        self.assertEqual(result["errors"], [])
        self.assertTrue(any("0 verified unique needs of 10" in w for w in result["warnings"]))
        self.item["source"]["evidence_status"] = "snippet"
        self.assertEqual(self.validate()["counts"]["background_needs"], 0)

    def test_duplicate_reddit_comments_count_as_one_thread(self):
        second = copy.deepcopy(self.item)
        second["id"] = "R02"
        second["source"]["thread_url"] = "https://old.reddit.com/r/skill_fixture_not_real/comments/test01/renamed/comment02/?utm_source=test#reply"
        second["recommendation"]["destination_url"] = second["source"]["thread_url"]
        self.data["items"].append(second)
        result = self.assert_invalid("duplicate canonical post")
        self.assertEqual(result["counts"]["verified_needs"], 1)
        self.assertEqual(result["counts"]["ready"], 1)

    def test_facebook_permalink_variants(self):
        a = "https://www.facebook.com/groups/example/posts/123/?utm_source=fixture"
        b = "https://m.facebook.com/groups/example/permalink/123/"
        self.assertEqual(CHECK.canonical_url(a), CHECK.canonical_url(b))
        self.assertTrue(CHECK.is_post_url(a, "facebook"))
        self.assertFalse(CHECK.is_post_url("https://facebook.com/groups/example/", "facebook"))

    def test_search_page_is_not_a_demand_post(self):
        self.item["source"]["thread_url"] = "https://reddit.com/search/?q=task"
        self.assert_invalid("specific platform post permalink")

    def test_fact_references_must_exist(self):
        self.item["copy"]["fact_ids"] = ["F999"]
        self.assert_invalid("unsupported product fact id")

    def test_disclosure_is_part_of_copyable_draft(self):
        self.item["copy"]["body"] = "A random user recommends TaskNote."
        self.assert_invalid("disclosure must also appear")

    def test_reply_destination_must_match_original_thread(self):
        self.item["recommendation"]["destination_url"] = "https://reddit.com/r/example/comments/other01/a/"
        self.assert_invalid("reply destination")

    def test_link_and_image_permission_gates(self):
        self.item["copy"]["cta"] = "https://example.invalid/try"
        self.item["rules"]["external_links"] = "prohibited"
        self.assert_invalid("link permission")
        self.item["copy"]["cta"] = None
        self.item["visual"].update(status="planned", prompt="A simple test diagram.")
        self.item["rules"]["images"] = "unknown"
        self.assert_invalid("image use lacks")

    def test_generated_image_must_exist_and_be_an_image(self):
        self.item["visual"].update(status="generated", file="missing.png", alt_text="Test")
        self.assert_invalid("missing or empty")
        (self.base / "missing.png").write_text("not an image", encoding="utf-8")
        self.assert_invalid("signature")
        self.image()
        self.assertEqual(self.validate()["errors"], [])
        self.assertEqual(self.validate()["counts"]["generated_images"], 1)

    def test_image_paths_cannot_escape_campaign(self):
        for path in ("../outside.png", "..\\outside.png", "C:\\outside.png", "/outside.png", "\\\\host\\share\\outside.png"):
            with self.subTest(path=path):
                self.item["visual"].update(status="generated", file=path, alt_text="Test")
                self.assert_invalid("image")

    def test_generated_image_alt_is_required(self):
        self.image()
        self.item["visual"]["alt_text"] = ""
        self.assert_invalid("alt_text")

    def test_count_inflation_is_rejected(self):
        self.data["verified_demand_count"] = 10
        self.assert_invalid("computed value is 1")

    def test_html_and_csv_injection_are_escaped(self):
        self.item["source"]["title"] = '<script>alert("x")</script>'
        self.item["copy"]["title"] = '=HYPERLINK("https://example.invalid","click")'
        self.item["copy"]["body"] += '\n<img src=x onerror="alert(1)">'
        self.item["community"]["name"] = '  +SUM(1,2)'
        self.image("images/test image.png")
        self.item["visual"]["alt_text"] = '\" onerror=\"alert(1)'
        self.item["visual"]["caption"] = "<script>caption</script>"
        files = CHECK.render_campaign(self.data, self.base, self.base / "review")
        markup = files[0].read_text(encoding="utf-8")
        self.assertNotIn("<script>", markup)
        self.assertNotIn('<img src=x', markup)
        self.assertIn("&lt;script&gt;", markup)
        self.assertIn("&quot; onerror=&quot;", markup)
        self.assertIn("../images/test%20image.png", markup)
        rows = list(csv.DictReader(io.StringIO(files[1].read_text(encoding="utf-8-sig"))))
        self.assertTrue(rows[0]["draft_title"].startswith("'="))
        self.assertTrue(rows[0]["community"].startswith("'  +"))

    def test_csv_formula_prefixes(self):
        for value in ("=1+1", "+1", "-1", "@SUM(A1)", " \t=1", "\ufeff=1", "\nhello"):
            with self.subTest(value=value):
                self.assertTrue(CHECK.csv_safe(value).startswith("'"))

    def test_render_refuses_invalid_data_and_outside_directory(self):
        with self.assertRaisesRegex(ValueError, "inside"):
            CHECK.render_campaign(self.data, self.base, self.base.parent)
        self.item["rules"]["self_promotion"] = "prohibited"
        with self.assertRaisesRegex(ValueError, "invalid campaign"):
            CHECK.render_campaign(self.data, self.base, self.base / "review")
        self.assertFalse((self.base / "review").exists())

    def test_cli_valid_and_invalid_json(self):
        source = self.base / "campaign.json"
        source.write_text(json.dumps(self.data), encoding="utf-8")
        run = subprocess.run([sys.executable, "-B", str(SCRIPT), str(source), "--render", "review"], capture_output=True, encoding="utf-8")
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result["counts"]["ready"], 1)
        self.assertTrue((self.base / "review" / "index.html").is_file())
        source.write_text("{broken", encoding="utf-8")
        run = subprocess.run([sys.executable, "-B", str(SCRIPT), str(source)], capture_output=True, encoding="utf-8")
        self.assertEqual(run.returncode, 2)
        self.assertTrue(json.loads(run.stderr)["errors"])

    def test_malformed_objects_return_errors_not_tracebacks(self):
        for value in (None, [], {"product": "bad", "items": [None]}, {"product": {"name": "x", "facts": []}, "items": [{}]}):
            with self.subTest(value=value):
                self.assertTrue(CHECK.validate_campaign(value, self.base)["errors"])

    def test_malformed_enum_values_and_pending_steps_are_rejected(self):
        self.item["rules"]["self_promotion"] = []
        self.item["visual"]["status"] = {}
        self.assertTrue(self.validate()["errors"])
        self.item["rules"]["self_promotion"] = "unknown"
        self.item["visual"]["status"] = "text_only"
        self.item["recommendation"].update(status="needs_check", manual_steps=None)
        self.assert_invalid("manual_steps must be a list")

    def test_render_file_symlink_cannot_write_outside_campaign(self):
        campaign = self.base / "nested-campaign"
        campaign.mkdir()
        review = campaign / "review"
        review.mkdir()
        outside = self.base / "keep.txt"
        outside.write_text("UNCHANGED", encoding="utf-8")
        try:
            (review / "index.html").symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("OS does not grant permission to create symbolic links")
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            CHECK.render_campaign(self.data, campaign, review)
        self.assertEqual(outside.read_text(encoding="utf-8"), "UNCHANGED")

    def test_existing_review_files_are_preserved(self):
        for name in ("index.html", "opportunities.csv"):
            with self.subTest(name=name):
                review = self.base / ("review-" + name)
                review.mkdir()
                existing = review / name
                existing.write_text("USER CONTENT", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "already exists"):
                    CHECK.render_campaign(self.data, self.base, review)
                self.assertEqual(existing.read_text(encoding="utf-8"), "USER CONTENT")
                self.assertEqual(len(list(review.iterdir())), 1)

    def test_internal_output_symlink_is_rejected(self):
        review = self.base / "review"
        review.mkdir()
        original = self.base / "notes.txt"
        original.write_text("ORIGINAL", encoding="utf-8")
        try:
            (review / "index.html").symlink_to(original)
        except (OSError, NotImplementedError):
            self.skipTest("OS does not grant permission to create symbolic links")
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            CHECK.render_campaign(self.data, self.base, review)
        self.assertEqual(original.read_text(encoding="utf-8"), "ORIGINAL")

    def test_shared_image_is_counted_once(self):
        self.image()
        second = copy.deepcopy(self.item)
        second["id"] = "R02"
        url = "https://reddit.com/r/skill_fixture_not_real/comments/test02/example/"
        second["source"]["thread_url"] = url
        second["recommendation"]["destination_url"] = url
        second["rules"]["applies_to_url"] = url
        second["visual"]["file"] = "images/../images/test.png"
        self.data["items"].append(second)
        result = self.validate()
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["counts"]["generated_images"], 1)

    def test_destination_rules_must_match_url_and_action(self):
        self.item["rules"]["applies_to_url"] = "https://reddit.com/r/example/comments/other01/a/"
        self.assert_invalid("rules must explicitly apply")
        self.item["rules"]["applies_to_url"] = self.item["source"]["thread_url"]
        self.item["rules"]["applies_to_action"] = "promotion_thread"
        self.assert_invalid("rules must explicitly apply")

    def test_new_post_and_promotion_destination_evidence(self):
        for action, url in (("new_post", self.item["community"]["url"]),
                            ("promotion_thread", "https://reddit.com/r/skill_fixture_not_real/comments/promo01/test/")):
            with self.subTest(action=action):
                self.item["recommendation"].update(action=action, destination_url=url)
                self.item["recommendation"].pop("destination_evidence", None)
                self.item["rules"].update(applies_to_action=action, applies_to_url=url)
                self.assert_invalid("destination requires explicit")
                evidence = {"evidence_status": "read", "state": "active", "checked_at": "2026-01-01T00:00:00Z"}
                self.item["recommendation"]["destination_evidence"] = evidence
                self.assertEqual(self.validate()["errors"], [])
                evidence["state"] = "locked"
                self.assert_invalid("destination requires explicit")
                evidence["state"] = "active"
                evidence["evidence_status"] = "snippet"
                self.assert_invalid("destination requires explicit")
                evidence["evidence_status"] = "read"
                self.item["recommendation"]["destination_url"] = "https://example.invalid/unverified"
                self.item["rules"]["applies_to_url"] = "https://example.invalid/unverified"
                self.assert_invalid("declared platform")


if __name__ == "__main__":
    unittest.main()
