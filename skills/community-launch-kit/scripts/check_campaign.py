#!/usr/bin/env python3
"""Validate a community-launch-kit campaign and optionally render a local review.

Standard library only. This checks declared evidence and file invariants; it
does not fetch websites or prove that an author's factual claims are true.
"""
import argparse
import csv
import html
import io
import json
import os
from pathlib import Path, PureWindowsPath
import re
import sys
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit


EVIDENCE = {"read", "snippet", "inaccessible"}
STATES = {"active", "locked", "removed", "unknown"}
PERMISSIONS = {"allowed", "conditional", "prohibited", "unknown"}


def web_url(value):
    if not isinstance(value, str) or any(c in value for c in "\r\n\t"):
        return False
    try:
        parts = urlsplit(value)
        return (parts.scheme in {"http", "https"} and bool(parts.hostname)
                and parts.username is None and parts.password is None)
    except ValueError:
        return False


def canonical_url(value):
    """Deduplicate post-level URLs, including tracking and comment variants."""
    if not web_url(value):
        return value
    p = urlsplit(value)
    host = p.hostname.lower()
    path = p.path.rstrip("/") or "/"
    if host == "redd.it":
        return "reddit:post:" + path.strip("/").lower()
    if host == "reddit.com" or host.endswith(".reddit.com"):
        m = re.search(r"/comments/([a-z0-9]+)(?:/|$)", path, re.I)
        if m:
            return "reddit:post:" + m.group(1).lower()
    params = dict(parse_qsl(p.query, keep_blank_values=True))
    if host == "facebook.com" or host.endswith(".facebook.com"):
        m = re.search(r"/groups/([^/]+)/(?:posts|permalink)/([^/]+)", path)
        if m:
            return "facebook:group:" + m.group(1) + ":post:" + m.group(2)
        if params.get("story_fbid"):
            return "facebook:story:" + params["story_fbid"] + ":owner:" + params.get("id", "")
        host = "facebook.com"
    clean = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_")
             and k.lower() not in {"fbclid", "gclid", "share_id", "ref", "ref_source", "context"}]
    return urlunsplit(("https", host.removeprefix("www."), path, urlencode(sorted(clean)), ""))


def is_post_url(value, platform):
    if not web_url(value):
        return False
    p = urlsplit(value)
    host = p.hostname.lower()
    if platform == "reddit":
        return ((host == "redd.it" and bool(p.path.strip("/")))
                or ((host == "reddit.com" or host.endswith(".reddit.com"))
                    and bool(re.search(r"/comments/[a-z0-9]+(?:/|$)", p.path, re.I))))
    if platform == "facebook":
        return ((host == "facebook.com" or host.endswith(".facebook.com"))
                and (bool(re.search(r"/(?:posts|permalink)/[^/]+", p.path))
                     or bool(dict(parse_qsl(p.query)).get("story_fbid"))))
    return False


def is_community_url(value, platform):
    if not web_url(value):
        return False
    p = urlsplit(value)
    host = p.hostname.lower()
    if platform == "reddit":
        return ((host == "reddit.com" or host.endswith(".reddit.com"))
                and bool(re.fullmatch(r"/r/[a-z0-9_]+/?", p.path, re.I)))
    if platform == "facebook":
        return ((host == "facebook.com" or host.endswith(".facebook.com"))
                and bool(re.fullmatch(r"/groups/[^/]+/?", p.path)))
    return False


def local_asset(base, value):
    """Resolve a portable relative asset path without following it outside base."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("image file must be a non-empty relative path")
    win = PureWindowsPath(value)
    if win.drive or win.is_absolute() or Path(value).is_absolute():
        raise ValueError("image file must be relative to the campaign directory")
    root = Path(base).resolve()
    target = (root / value.replace("\\", "/")).resolve()
    if not target.is_relative_to(root):
        raise ValueError("image path escapes the campaign directory")
    return target


def image_signature(path):
    with path.open("rb") as handle:
        head = handle.read(32)
    suffix = path.suffix.lower()
    return ((suffix == ".png" and head.startswith(b"\x89PNG\r\n\x1a\n"))
            or (suffix in {".jpg", ".jpeg"} and head.startswith(b"\xff\xd8\xff"))
            or (suffix == ".gif" and head[:6] in {b"GIF87a", b"GIF89a"})
            or (suffix == ".webp" and head.startswith(b"RIFF") and head[8:12] == b"WEBP")
            or (suffix == ".avif" and head[4:8] == b"ftyp" and (b"avif" in head[8:] or b"avis" in head[8:])))


def validate_campaign(data, base_dir):
    errors, warnings = [], []
    counts = {"requested": 10, "items": 0, "verified_needs": 0, "background_needs": 0, "ready": 0, "generated_images": 0}
    result = {"errors": errors, "warnings": warnings, "counts": counts}
    if not isinstance(data, dict):
        errors.append("campaign must be a JSON object")
        return result

    def obj(parent, key, label):
        value = parent.get(key)
        if not isinstance(value, dict):
            errors.append(label + " must be an object")
            return {}
        return value

    def text(value):
        return isinstance(value, str) and bool(value.strip())

    def enum(value, choices, label):
        if not isinstance(value, str) or value not in choices:
            errors.append(label + " must be one of " + ", ".join(sorted(choices)))

    requested = data.get("requested_count", 10)
    if isinstance(requested, bool) or not isinstance(requested, int) or requested < 1:
        errors.append("requested_count must be a positive integer")
        requested = 10
    counts["requested"] = requested
    if data.get("fixture") is True:
        warnings.append("TEST FIXTURE: synthetic data; not researched opportunities or permission to publish")
    product = obj(data, "product", "product")
    if not text(product.get("name")):
        errors.append("product.name is required")
    facts = product.get("facts")
    fact_ids = set()
    if not isinstance(facts, list):
        errors.append("product.facts must be a list of {id, text}")
        facts = []
    for n, fact in enumerate(facts):
        if not isinstance(fact, dict) or not text(fact.get("id")) or not text(fact.get("text")):
            errors.append(f"product.facts[{n}] requires id and text")
            continue
        if fact["id"] in fact_ids:
            errors.append("duplicate product fact id: " + fact["id"])
        fact_ids.add(fact["id"])
    items = data.get("items")
    if not isinstance(items, list):
        errors.append("items must be a list")
        return result
    counts["items"] = len(items)
    seen_ids, seen_posts, seen_images = set(), set(), set()
    for n, item in enumerate(items):
        errors_before = len(errors)
        label = f"items[{n}]"
        if not isinstance(item, dict):
            errors.append(label + " must be an object")
            continue
        ident = item.get("id")
        if not text(ident):
            errors.append(label + ".id is required")
        elif ident in seen_ids:
            errors.append("duplicate opportunity id: " + ident)
        else:
            seen_ids.add(ident)
            label = ident
        platform = item.get("platform")
        enum(platform, {"reddit", "facebook"}, label + ".platform")
        community = obj(item, "community", label + ".community")
        source = obj(item, "source", label + ".source")
        fit = obj(item, "product_fit", label + ".product_fit")
        rules = obj(item, "rules", label + ".rules")
        rec = obj(item, "recommendation", label + ".recommendation")
        copy = obj(item, "copy", label + ".copy")
        visual = obj(item, "visual", label + ".visual")
        if not text(community.get("name")) or not web_url(community.get("url")):
            errors.append(label + ": community requires name and web URL")
        post = source.get("thread_url")
        valid_post = is_post_url(post, platform)
        if not valid_post:
            errors.append(label + ": source.thread_url must be a specific platform post permalink, not a search or community page")
        canonical = canonical_url(post) if isinstance(post, str) else ""
        duplicate = bool(canonical and canonical in seen_posts)
        if duplicate:
            errors.append(label + ": duplicate canonical post URL")
        elif canonical:
            seen_posts.add(canonical)
        enum(source.get("evidence_status"), EVIDENCE, label + ".source.evidence_status")
        enum(source.get("thread_state"), STATES, label + ".source.thread_state")
        scope = source.get("scope", "current")
        enum(scope, {"current", "background"}, label + ".source.scope")
        enum(rules.get("evidence_status"), EVIDENCE, label + ".rules.evidence_status")
        enum(rules.get("self_promotion"), PERMISSIONS, label + ".rules.self_promotion")
        for field in ("external_links", "images"):
            enum(rules.get(field), PERMISSIONS - {"conditional"}, label + ".rules." + field)
        enum(rec.get("status"), {"ready", "needs_check", "skip"}, label + ".recommendation.status")
        enum(rec.get("action"), {"reply", "new_post", "promotion_thread", "none"}, label + ".recommendation.action")
        enum(visual.get("status"), {"planned", "generated", "text_only"}, label + ".visual.status")
        steps = rec.get("manual_steps")
        if not isinstance(steps, list) or any(not text(step) for step in steps):
            errors.append(label + ": recommendation.manual_steps must be a list of nonempty strings")
        verified = valid_post and source.get("evidence_status") == "read" and text(source.get("problem_summary"))
        if verified and not duplicate:
            if scope == "current":
                counts["verified_needs"] += 1
            elif scope == "background":
                counts["background_needs"] += 1
        if source.get("evidence_status") == "read" and not text(source.get("checked_at")):
            errors.append(label + ": read source requires checked_at")
        if rules.get("evidence_status") == "read":
            if not web_url(rules.get("url")) or not text(rules.get("checked_at")) or not text(rules.get("summary")):
                errors.append(label + ": read rules require URL, checked_at and summary")
        claims = copy.get("fact_ids", [])
        if not isinstance(claims, list) or any(not isinstance(x, str) for x in claims):
            errors.append(label + ": copy.fact_ids must be a list of fact IDs")
            claims = []
        for claim in claims:
            if claim not in fact_ids:
                errors.append(label + ": unsupported product fact id: " + claim)
        if visual.get("status") == "generated":
            try:
                asset = local_asset(base_dir, visual.get("file"))
                if not asset.is_file() or asset.stat().st_size == 0:
                    raise ValueError("generated image file is missing or empty")
                if asset.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"}:
                    raise ValueError("generated image must use a supported raster image extension")
                if not image_signature(asset):
                    raise ValueError("generated image file signature does not match its extension")
                image_key = os.path.normcase(str(asset))
                if image_key not in seen_images:
                    counts["generated_images"] += 1
                    seen_images.add(image_key)
            except (ValueError, OSError) as exc:
                errors.append(label + ": " + str(exc))
            if not text(visual.get("alt_text")):
                errors.append(label + ": generated image requires alt_text")
        elif visual.get("file") not in (None, ""):
            errors.append(label + ": only generated images may name an existing file")
        if visual.get("status") == "planned":
            warnings.append(label + ": image is planned, not generated")
        if rec.get("status") == "ready":
            gates = []
            if not verified:
                gates.append("post body/need is not verified")
            if scope == "background":
                gates.append("background needs cannot be ready or count toward the requested current needs")
            if source.get("thread_state") != "active":
                gates.append("source thread is not active")
            if rules.get("evidence_status") != "read":
                gates.append("rules are unread")
            promo = rules.get("self_promotion")
            if promo not in ("allowed", "conditional"):
                gates.append("self-promotion is prohibited or unknown")
            if promo == "conditional" and (rec.get("conditions_met") is not True or not text(rules.get("conditions"))):
                gates.append("conditional permission has not been documented and satisfied")
            if rec.get("action") == "none" or not web_url(rec.get("destination_url")):
                gates.append("publish destination/action is missing")
            elif rec.get("action") == "reply" and canonical_url(rec["destination_url"]) != canonical:
                gates.append("reply destination must be in the source thread")
            action, destination = rec.get("action"), rec.get("destination_url")
            if action == "new_post" and not is_community_url(destination, platform):
                gates.append("new_post destination must be a community URL on the declared platform")
            if action == "promotion_thread" and not is_post_url(destination, platform):
                gates.append("promotion_thread destination must be a specific post on the declared platform")
            if (not web_url(rules.get("applies_to_url"))
                    or canonical_url(rules.get("applies_to_url")) != canonical_url(destination)
                    or rules.get("applies_to_action") != action):
                gates.append("rules must explicitly apply to this destination URL and action")
            destination_evidence = rec.get("destination_evidence")
            if action != "reply" or destination_evidence is not None:
                if (not isinstance(destination_evidence, dict)
                        or destination_evidence.get("evidence_status") != "read"
                        or destination_evidence.get("state") != "active"
                        or not text(destination_evidence.get("checked_at"))):
                    gates.append("destination requires explicit read, active and checked_at evidence")
            for value, name in ((fit.get("can_help"), "product fit"), (rec.get("reason"), "placement reason"),
                                (copy.get("body"), "draft body"), (copy.get("language"), "draft language"),
                                (copy.get("maker_disclosure"), "maker disclosure")):
                if not text(value):
                    gates.append(name + " is missing")
            if not claims:
                gates.append("draft has no product fact references")
            if text(copy.get("maker_disclosure")) and copy["maker_disclosure"] not in str(copy.get("body", "")):
                gates.append("maker disclosure must also appear in the draft body")
            if not isinstance(rec.get("manual_steps"), list) or not rec.get("manual_steps"):
                gates.append("manual posting steps are missing")
            content = str(copy.get("body", "")) + " " + str(copy.get("cta") or "")
            if re.search(r"https?://", content, re.I) and rules.get("external_links") != "allowed":
                gates.append("draft contains a link without verified link permission")
            if visual.get("status") in ("planned", "generated") and rules.get("images") != "allowed":
                gates.append("image use lacks verified permission; use text_only here")
            if gates:
                errors.append(label + ": ready is invalid: " + "; ".join(gates))
            elif len(errors) == errors_before:
                counts["ready"] += 1
    if counts["verified_needs"] < requested:
        warnings.append(f"Shortfall: {counts['verified_needs']} verified unique needs of {requested} requested; do not fill with invented or unread posts")
    if counts["ready"] < counts["verified_needs"]:
        warnings.append(f"Only {counts['ready']} placements are marked ready; verified needs are not all publishable")
    if counts["background_needs"]:
        warnings.append(f"{counts['background_needs']} read background needs are excluded from the requested current-needs target")
    for field, value in (("verified_demand_count", counts["verified_needs"]), ("recommended_placement_count", counts["ready"])):
        if field in data and data[field] != value:
            errors.append(f"{field} claims {data[field]!r}, but computed value is {value}")
    return result


def csv_safe(value):
    value = "" if value is None else str(value)
    stripped = value.lstrip(" \t\r\n\ufeff")
    if (stripped and stripped[0] in "=+-@") or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


def render_campaign(data, base_dir, output_dir):
    """Write escaped HTML and formula-safe CSV; never render invalid campaigns."""
    base = Path(base_dir).resolve()
    target = Path(output_dir).resolve()
    if not target.is_relative_to(base):
        raise ValueError("render directory must stay inside the campaign directory")
    outputs = [target / "index.html", target / "opportunities.csv"]
    # Resolve containment, but reject symlinked output components before writes.
    if any(path.is_symlink() for path in [Path(output_dir)] + list(Path(output_dir).parents)):
        raise ValueError("render directory must not use symbolic links")
    if any(path.is_symlink() for path in outputs):
        raise ValueError("render output file must not be a symbolic link")
    if any(not path.resolve().is_relative_to(base) for path in outputs):
        raise ValueError("render output file resolves outside the campaign directory")
    if any(path.exists() for path in outputs):
        raise ValueError("render output already exists; choose a new review directory")
    report = validate_campaign(data, base)
    if report["errors"]:
        raise ValueError("cannot render invalid campaign: " + "; ".join(report["errors"]))
    esc = lambda value: html.escape("" if value is None else str(value), quote=True)
    def link(url, label):
        return '<a href="' + esc(url) + '" rel="noreferrer">' + esc(label) + '</a>' if web_url(url) else esc(label)
    blocks = []
    rows = []
    for item in data["items"]:
        s, r, c, v = item["source"], item["recommendation"], item["copy"], item["visual"]
        dest_evidence = r.get("destination_evidence")
        if not dest_evidence and r.get("action") == "reply":
            dest_evidence = {"evidence_status": s.get("evidence_status"), "state": s.get("thread_state"),
                             "checked_at": s.get("checked_at"), "inherited_from_source": True}
        picture = ""
        if v["status"] == "generated":
            asset = local_asset(base, v["file"])
            relative = quote(os.path.relpath(asset, target).replace(os.sep, "/"), safe="/")
            picture = ('<figure><a href="' + esc(relative) + '">Open image</a><br><img src="' + esc(relative)
                       + '" alt="' + esc(v["alt_text"]) + '"><figcaption>' + esc(v.get("caption", "")) + '</figcaption></figure>')
        blocks.append('<section><h2>' + esc(item["id"]) + ' · ' + esc(s.get("title", "")) + '</h2>'
                      + '<p><strong>' + esc(r["status"]) + '</strong> · ' + esc(item["platform"]) + ' · ' + esc(r["action"]) + '</p>'
                      + '<p>Scope: ' + esc(s.get("scope", "current")) + '</p>'
                      + '<p>' + link(s["thread_url"], "Source post") + ' · ' + link(item["community"]["url"], item["community"]["name"])
                      + ' · ' + link(item["rules"].get("url"), "Rules") + ' · ' + link(r.get("destination_url"), "Proposed destination") + '</p>'
                      + '<p>Need: ' + esc(s.get("problem_summary", "")) + '</p><p>Product helps: ' + esc(item["product_fit"].get("can_help", "")) + '</p>'
                      + '<p>Rules: ' + esc(item["rules"].get("summary", "")) + '</p><p>Decision: ' + esc(r.get("reason", "")) + '</p>'
                      + '<p>Rules apply to: ' + link(item["rules"].get("applies_to_url"), item["rules"].get("applies_to_url"))
                      + ' · ' + esc(item["rules"].get("applies_to_action")) + '</p>'
                      + '<p>Destination evidence: ' + esc(json.dumps(dest_evidence, ensure_ascii=False)) + '</p>'
                      + '<h3>Draft</h3><pre>' + esc(c.get("title") or "") + '\n' + esc(c.get("body", "")) + '</pre>'
                      + '<p>Disclosure: ' + esc(c.get("maker_disclosure", "")) + '</p><p>CTA: ' + esc(c.get("cta")) + '</p>'
                      + '<ol>' + ''.join('<li>' + esc(step) + '</li>' for step in r.get("manual_steps", [])) + '</ol>'
                      + '<p>Image: ' + esc(v["status"]) + '</p>' + picture + '</section>')
        rows.append([item["id"], item["platform"], item["community"]["name"], s["thread_url"], s.get("problem_summary", ""),
                     item["product_fit"].get("can_help", ""), r["status"], r["action"], r.get("destination_url", ""),
                     item["rules"].get("url", ""), c.get("title", ""), c.get("body", ""), c.get("cta", ""),
                     v["status"], v.get("file", ""), v.get("alt_text", ""), s.get("scope", "current")])
    count = report["counts"]
    heading = "TEST FIXTURE — NOT REAL OPPORTUNITIES" if data.get("fixture") else "Community opportunity review"
    markup = ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
              '<title>' + esc(heading) + '</title><style>body{font:16px/1.6 system-ui;max-width:980px;margin:36px auto;padding:0 20px}section{border-top:1px solid #ddd;padding:20px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f8;padding:16px}img{max-width:100%;max-height:460px}a{overflow-wrap:anywhere}</style>'
              '<body><h1>' + esc(heading) + '</h1><p>' + esc(data["product"]["name"]) + '</p><p>'
              + esc(f"Requested {count['requested']} · current verified needs {count['verified_needs']} · background needs {count['background_needs']} · ready placements {count['ready']} · generated images {count['generated_images']}")
              + '</p><p>Drafts only. Structural checks do not independently verify website content or grant publishing permission.</p>'
              + '<ul>' + ''.join('<li>' + esc(w) + '</li>' for w in report["warnings"]) + '</ul>' + ''.join(blocks) + '</body></html>')
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["id", "platform", "community", "post_url", "problem", "product_help", "status", "action", "destination", "rules_url", "draft_title", "draft_body", "cta", "image_status", "image_file", "image_alt", "scope"])
    writer.writerows([[csv_safe(cell) for cell in row] for row in rows])
    target.mkdir(parents=True, exist_ok=True)
    with outputs[0].open("x", encoding="utf-8") as handle:
        handle.write(markup)
    with outputs[1].open("x", encoding="utf-8-sig", newline="") as handle:
        handle.write(stream.getvalue())
    return outputs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", type=Path, help="UTF-8 campaign JSON")
    parser.add_argument("--render", type=Path, help="Review folder inside the campaign directory; relative paths resolve there")
    args = parser.parse_args(argv)
    try:
        source = args.campaign.resolve()
        data = json.loads(source.read_text(encoding="utf-8-sig"))
        report = validate_campaign(data, source.parent)
        if args.render and not report["errors"]:
            target = args.render if args.render.is_absolute() else source.parent / args.render
            report["rendered"] = [str(p) for p in render_campaign(data, source.parent, target)]
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report["errors"] else 0
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({"errors": [str(exc)]}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
